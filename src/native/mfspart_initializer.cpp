// SPDX-License-Identifier: Apache-2.0
//
// Independent paper-level reproduction of MFSPart delayed propagation and
// two-phase initial partitioning (TCAD 2026, Eqs. 5--8).  No source from the
// unlicensed companion repository is copied or linked.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <queue>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

struct Node {
  int fixed_part = -1;
  std::vector<long long> weights;
};

struct Net {
  double weight = 1.0;
  int source = -1;
  std::vector<int> sinks;
};

struct Input {
  int parts = 0;
  int dimensions = 0;
  int hmax = 0;
  std::uint64_t seed = 0;
  double theta = 1.0;
  double eta = 1.0;
  double lambda = 1.0;
  double mu = 1.0;
  double temperature = 1.0;
  std::vector<std::vector<int>> distances;
  std::vector<std::vector<long long>> capacities;
  std::vector<double> part_degrees;
  std::vector<Node> nodes;
  std::vector<Net> nets;
};

struct AssignmentRecord {
  int node = -1;
  int part = -1;
  int phase = 0;
  double score = 0.0;
};

struct DomainRecord {
  int assignment_step = -1;
  int node = -1;
  std::vector<int> parts;
};

using Candidates = std::vector<std::vector<int>>;

struct PropagationState {
  Candidates candidates;
  std::vector<bool> processed;
};

struct PriorityEntry {
  double priority = 0.0;
  int node = -1;
  int version = -1;

  bool operator<(const PriorityEntry& other) const {
    if (priority != other.priority) {
      return priority < other.priority;
    }
    return node > other.node;
  }
};

std::uint64_t splitmix64(std::uint64_t value) {
  value += 0x9e3779b97f4a7c15ULL;
  value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
  value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
  return value ^ (value >> 31U);
}

double deterministic_unit(std::uint64_t seed, std::uint64_t event) {
  const std::uint64_t bits = splitmix64(seed ^ splitmix64(event + 1));
  return static_cast<double>(bits >> 11U) * (1.0 / 9007199254740992.0);
}

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string magic;
  std::getline(stream, magic);
  if (magic != "EMUFLOW_MFSPART_INITIALIZER_INPUT_V1") {
    throw std::runtime_error("unsupported input header");
  }
  Input input;
  int node_count = -1;
  int net_count = -1;
  bool saw_param = false;
  std::vector<std::vector<bool>> saw_distances;
  std::vector<std::vector<bool>> saw_capacities;
  std::vector<bool> saw_degrees;
  std::vector<bool> saw_nodes;
  std::vector<bool> saw_nets;
  std::string kind;
  while (stream >> kind) {
    if (kind == "PARAM") {
      if (saw_param) {
        throw std::runtime_error("duplicate PARAM record");
      }
      stream >> input.parts >> node_count >> input.dimensions >> net_count >>
          input.hmax >> input.seed >> input.theta >> input.eta >>
          input.lambda >> input.mu >> input.temperature;
      if (input.parts <= 0 || node_count <= 0 || input.dimensions <= 0 ||
          net_count < 0 || input.hmax < 1 || !std::isfinite(input.theta) ||
          !std::isfinite(input.eta) || !std::isfinite(input.lambda) ||
          !std::isfinite(input.mu) || !std::isfinite(input.temperature) ||
          input.theta < 0.0 || input.eta < 0.0 || input.lambda < 0.0 ||
          input.mu < 0.0 || input.temperature <= 0.0) {
        throw std::runtime_error("invalid PARAM record");
      }
      input.distances.assign(input.parts,
                             std::vector<int>(input.parts, -1));
      input.capacities.assign(
          input.parts, std::vector<long long>(input.dimensions, 0));
      input.part_degrees.assign(input.parts, 0.0);
      input.nodes.assign(node_count, Node{});
      input.nets.assign(net_count, Net{});
      saw_distances.assign(input.parts,
                           std::vector<bool>(input.parts, false));
      saw_capacities.assign(input.parts,
                            std::vector<bool>(input.dimensions, false));
      saw_degrees.assign(input.parts, false);
      saw_nodes.assign(node_count, false);
      saw_nets.assign(net_count, false);
      saw_param = true;
    } else if (kind == "DIST") {
      if (!saw_param) {
        throw std::runtime_error("DIST record precedes PARAM");
      }
      int source = -1;
      int target = -1;
      int distance = -1;
      stream >> source >> target >> distance;
      if (source < 0 || source >= input.parts || target < 0 ||
          target >= input.parts || distance < 0 ||
          saw_distances[source][target]) {
        throw std::runtime_error("invalid or duplicate DIST record");
      }
      input.distances[source][target] = distance;
      saw_distances[source][target] = true;
    } else if (kind == "CAP") {
      if (!saw_param) {
        throw std::runtime_error("CAP record precedes PARAM");
      }
      int part = -1;
      int dimension = -1;
      long long capacity = -1;
      stream >> part >> dimension >> capacity;
      if (part < 0 || part >= input.parts || dimension < 0 ||
          dimension >= input.dimensions || capacity <= 0 ||
          saw_capacities[part][dimension]) {
        throw std::runtime_error("invalid or duplicate CAP record");
      }
      input.capacities[part][dimension] = capacity;
      saw_capacities[part][dimension] = true;
    } else if (kind == "DEG") {
      if (!saw_param) {
        throw std::runtime_error("DEG record precedes PARAM");
      }
      int part = -1;
      double degree = -1.0;
      stream >> part >> degree;
      if (part < 0 || part >= input.parts || !std::isfinite(degree) ||
          degree < 0.0 || saw_degrees[part]) {
        throw std::runtime_error("invalid or duplicate DEG record");
      }
      input.part_degrees[part] = degree;
      saw_degrees[part] = true;
    } else if (kind == "NODE") {
      if (!saw_param) {
        throw std::runtime_error("NODE record precedes PARAM");
      }
      int index = -1;
      Node node;
      stream >> index >> node.fixed_part;
      node.weights.resize(input.dimensions);
      for (long long& weight : node.weights) {
        stream >> weight;
      }
      if (index < 0 || index >= node_count || node.fixed_part < -1 ||
          node.fixed_part >= input.parts || saw_nodes[index] ||
          node.weights.empty() || node.weights.front() <= 0 ||
          std::any_of(node.weights.begin(), node.weights.end(),
                      [](long long value) { return value < 0; })) {
        throw std::runtime_error("invalid or duplicate NODE record");
      }
      input.nodes[index] = std::move(node);
      saw_nodes[index] = true;
    } else if (kind == "NET") {
      if (!saw_param) {
        throw std::runtime_error("NET record precedes PARAM");
      }
      int index = -1;
      int sink_count = -1;
      Net net;
      stream >> index >> net.weight >> net.source >> sink_count;
      if (index < 0 || index >= net_count || saw_nets[index] ||
          !std::isfinite(net.weight) || net.weight <= 0.0 ||
          net.source < 0 || net.source >= node_count || sink_count <= 0) {
        throw std::runtime_error("invalid or duplicate NET record");
      }
      std::set<int> unique;
      net.sinks.resize(sink_count);
      for (int& sink : net.sinks) {
        stream >> sink;
        if (sink < 0 || sink >= node_count || sink == net.source ||
            !unique.insert(sink).second) {
          throw std::runtime_error("invalid NET sink");
        }
      }
      std::sort(net.sinks.begin(), net.sinks.end());
      input.nets[index] = std::move(net);
      saw_nets[index] = true;
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!stream) {
      throw std::runtime_error("malformed input record");
    }
  }
  const auto missing = [](const auto& flags) {
    return std::any_of(flags.begin(), flags.end(),
                       [](bool value) { return !value; });
  };
  if (!saw_param || missing(saw_degrees) || missing(saw_nodes) ||
      missing(saw_nets)) {
    throw std::runtime_error("incomplete input");
  }
  for (int part = 0; part < input.parts; ++part) {
    if (missing(saw_distances[part]) || missing(saw_capacities[part]) ||
        input.distances[part][part] != 0) {
      throw std::runtime_error("incomplete topology or capacity input");
    }
    for (int other = 0; other < input.parts; ++other) {
      if (input.distances[part][other] != input.distances[other][part]) {
        throw std::runtime_error("paper-mode FPGA distances must be symmetric");
      }
    }
  }
  return input;
}

std::vector<std::vector<std::pair<int, double>>> build_adjacency(
    const Input& input) {
  std::vector<std::vector<std::pair<int, double>>> adjacency(
      input.nodes.size());
  for (const Net& net : input.nets) {
    for (const int sink : net.sinks) {
      adjacency[net.source].push_back({sink, net.weight});
      adjacency[sink].push_back({net.source, net.weight});
    }
  }
  return adjacency;
}

std::vector<int> propagate_new_anchors(
    const Input& input,
    const std::vector<std::vector<std::pair<int, double>>>& adjacency,
    PropagationState& state, const std::vector<int>& anchors) {
  std::queue<int> queue;
  std::vector<bool> queued(input.nodes.size(), false);
  std::vector<bool> changed(input.nodes.size(), false);
  for (const int anchor : anchors) {
    if (!state.processed[anchor] && !queued[anchor]) {
      queue.push(anchor);
      queued[anchor] = true;
    }
  }
  while (!queue.empty()) {
    const int anchor = queue.front();
    queue.pop();
    queued[anchor] = false;
    if (state.processed[anchor] || state.candidates[anchor].size() != 1) {
      continue;
    }
    state.processed[anchor] = true;
    const int anchor_part = state.candidates[anchor].front();
    const int max_distance = *std::max_element(
        input.distances[anchor_part].begin(),
        input.distances[anchor_part].end());
    if (max_distance <= input.hmax) {
      continue;
    }
    std::vector<int> circuit_distance(input.nodes.size(), -1);
    std::queue<int> bfs;
    std::vector<int> reached;
    circuit_distance[anchor] = 0;
    bfs.push(anchor);
    reached.push_back(anchor);
    while (!bfs.empty()) {
      const int node = bfs.front();
      bfs.pop();
      if ((circuit_distance[node] + 1) * input.hmax >= max_distance) {
        continue;
      }
      for (const auto& [neighbor, unused_weight] : adjacency[node]) {
        (void)unused_weight;
        if (circuit_distance[neighbor] < 0) {
          circuit_distance[neighbor] = circuit_distance[node] + 1;
          bfs.push(neighbor);
          reached.push_back(neighbor);
        }
      }
    }
    for (const int node : reached) {
      const int distance = circuit_distance[node];
      if (node == anchor || distance * input.hmax >= max_distance) {
        continue;
      }
      std::vector<int> filtered;
      for (const int part : state.candidates[node]) {
        if (input.distances[anchor_part][part] <= distance * input.hmax) {
          filtered.push_back(part);
        }
      }
      if (filtered != state.candidates[node]) {
        state.candidates[node] = std::move(filtered);
        changed[node] = true;
        if (state.candidates[node].size() == 1 &&
            !state.processed[node] && !queued[node]) {
          queue.push(node);
          queued[node] = true;
        }
      }
    }
  }
  std::vector<int> changed_nodes;
  for (int node = 0; node < static_cast<int>(changed.size()); ++node) {
    if (changed[node]) {
      changed_nodes.push_back(node);
    }
  }
  return changed_nodes;
}

PropagationState initial_propagation(
    const Input& input,
    const std::vector<std::vector<std::pair<int, double>>>& adjacency,
    const std::vector<int>& assigned) {
  PropagationState state;
  state.candidates.resize(input.nodes.size());
  state.processed.assign(input.nodes.size(), false);
  std::vector<int> anchors;
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    if (assigned[node] >= 0) {
      state.candidates[node] = {assigned[node]};
      anchors.push_back(node);
    } else {
      state.candidates[node].resize(input.parts);
      for (int part = 0; part < input.parts; ++part) {
        state.candidates[node][part] = part;
      }
    }
  }
  (void)propagate_new_anchors(input, adjacency, state, anchors);
  return state;
}

bool trial_empties_domain(
    const Input& input,
    const std::vector<std::vector<std::pair<int, double>>>& adjacency,
    const PropagationState& state, int selected_node, int selected_part) {
  std::unordered_map<int, std::vector<int>> overlay;
  overlay.emplace(selected_node, std::vector<int>{selected_part});
  std::queue<int> queue;
  queue.push(selected_node);
  std::unordered_set<int> processed;
  std::unordered_set<int> queued{selected_node};
  auto candidate_parts = [&](int node) -> const std::vector<int>& {
    const auto found = overlay.find(node);
    return found == overlay.end() ? state.candidates[node] : found->second;
  };
  while (!queue.empty()) {
    const int anchor = queue.front();
    queue.pop();
    queued.erase(anchor);
    const auto& anchor_candidates = candidate_parts(anchor);
    if (state.processed[anchor] || processed.count(anchor) != 0 ||
        anchor_candidates.size() != 1) {
      continue;
    }
    processed.insert(anchor);
    const int anchor_part = anchor_candidates.front();
    const int max_distance = *std::max_element(
        input.distances[anchor_part].begin(),
        input.distances[anchor_part].end());
    if (max_distance <= input.hmax) {
      continue;
    }
    std::vector<int> circuit_distance(input.nodes.size(), -1);
    std::queue<int> bfs;
    std::vector<int> reached;
    circuit_distance[anchor] = 0;
    bfs.push(anchor);
    reached.push_back(anchor);
    while (!bfs.empty()) {
      const int node = bfs.front();
      bfs.pop();
      if ((circuit_distance[node] + 1) * input.hmax >= max_distance) {
        continue;
      }
      for (const auto& [neighbor, unused_weight] : adjacency[node]) {
        (void)unused_weight;
        if (circuit_distance[neighbor] < 0) {
          circuit_distance[neighbor] = circuit_distance[node] + 1;
          bfs.push(neighbor);
          reached.push_back(neighbor);
        }
      }
    }
    for (const int node : reached) {
      const int distance = circuit_distance[node];
      if (node == anchor || distance * input.hmax >= max_distance) {
        continue;
      }
      std::vector<int> filtered;
      for (const int part : candidate_parts(node)) {
        if (input.distances[anchor_part][part] <= distance * input.hmax) {
          filtered.push_back(part);
        }
      }
      if (filtered.empty()) {
        return true;
      }
      if (filtered != candidate_parts(node)) {
        overlay[node] = std::move(filtered);
        if (overlay[node].size() == 1 && !state.processed[node] &&
            processed.count(node) == 0 && queued.insert(node).second) {
          queue.push(node);
        }
      }
    }
  }
  return false;
}

bool fits(const Input& input,
          const std::vector<std::vector<long long>>& loads, int node,
          int part) {
  for (int dimension = 0; dimension < input.dimensions; ++dimension) {
    if (loads[part][dimension] + input.nodes[node].weights[dimension] >
        input.capacities[part][dimension]) {
      return false;
    }
  }
  return true;
}

double score(const Input& input,
             const std::vector<std::vector<std::pair<int, double>>>& adjacency,
             const std::vector<int>& assigned,
             const std::vector<std::vector<long long>>& loads, int node,
             int part, bool violation_penalty) {
  double connected = 0.0;
  double penalty = 0.0;
  for (const auto& [neighbor, weight] : adjacency[node]) {
    if (assigned[neighbor] == part) {
      connected += weight;
    }
    if (violation_penalty && assigned[neighbor] >= 0) {
      const int distance = input.distances[assigned[neighbor]][part];
      if (distance > input.hmax) {
        penalty += weight *
                   (1.0 + input.mu * static_cast<double>(distance - input.hmax));
      }
    }
  }
  const long long remaining =
      input.capacities[part][0] - loads[part][0] -
      input.nodes[node].weights[0];
  const double capacity_term =
      input.theta / std::max(1.0, static_cast<double>(remaining));
  return connected - capacity_term + input.eta * input.part_degrees[part] -
         input.lambda * penalty;
}

void assign_node(const Input& input, std::vector<int>& assigned,
                 std::vector<std::vector<long long>>& loads, int node,
                 int part) {
  assigned[node] = part;
  for (int dimension = 0; dimension < input.dimensions; ++dimension) {
    loads[part][dimension] += input.nodes[node].weights[dimension];
  }
}

void run(const Input& input, const std::string& output_path) {
  const auto adjacency = build_adjacency(input);
  std::vector<int> assigned(input.nodes.size(), -1);
  std::vector<std::vector<long long>> loads(
      input.parts, std::vector<long long>(input.dimensions, 0));
  std::vector<AssignmentRecord> records;
  std::vector<DomainRecord> domain_records;
  bool has_fixed = false;
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    if (input.nodes[node].fixed_part >= 0) {
      has_fixed = true;
      if (!fits(input, loads, node, input.nodes[node].fixed_part)) {
        throw std::runtime_error("fixed nodes exceed FPGA capacity");
      }
      assign_node(input, assigned, loads, node, input.nodes[node].fixed_part);
      records.push_back({node, input.nodes[node].fixed_part, 0, 0.0});
    }
  }
  if (!has_fixed) {
    int start_node = 0;
    double best_normalized_degree = -1.0;
    for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
      double degree = 0.0;
      for (const auto& [neighbor, weight] : adjacency[node]) {
        (void)neighbor;
        degree += weight;
      }
      const double normalized =
          degree / static_cast<double>(input.nodes[node].weights[0]);
      if (normalized > best_normalized_degree) {
        best_normalized_degree = normalized;
        start_node = node;
      }
    }
    int start_part = -1;
    for (int part = 0; part < input.parts; ++part) {
      if (fits(input, loads, start_node, part) &&
          (start_part < 0 ||
           input.part_degrees[part] > input.part_degrees[start_part])) {
        start_part = part;
      }
    }
    if (start_part < 0) {
      throw std::runtime_error("no FPGA can hold propagation start node");
    }
    assign_node(input, assigned, loads, start_node, start_part);
    records.push_back({start_node, start_part, 0, 0.0});
  }

  PropagationState propagation = initial_propagation(input, adjacency, assigned);
  const Candidates initial_candidates = propagation.candidates;
  for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
    if (assigned[node] >= 0 && initial_candidates[node].empty()) {
      throw std::runtime_error("fixed assignments violate FPGA topology");
    }
  }
  std::uint64_t random_event = 0;
  std::vector<bool> phase_two(input.nodes.size(), false);
  std::vector<double> total_weight(input.nodes.size(), 0.0);
  std::vector<int> priority_versions(input.nodes.size(), 0);
  std::priority_queue<PriorityEntry> priority_queue;
  long long priority_recomputations = 0;
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    for (const auto& [neighbor, weight] : adjacency[node]) {
      (void)neighbor;
      total_weight[node] += weight;
    }
  }
  auto refresh_priority = [&](int node) {
    ++priority_versions[node];
    if (assigned[node] >= 0 || phase_two[node] ||
        propagation.candidates[node].empty()) {
      return;
    }
    ++priority_recomputations;
    double fixed_weight = 0.0;
    for (const auto& [neighbor, weight] : adjacency[node]) {
      if (assigned[neighbor] >= 0) {
        fixed_weight += weight;
      }
    }
    const double priority = total_weight[node] > 0.0
                                ? fixed_weight / total_weight[node]
                                : 0.0;
    priority_queue.push({priority, node, priority_versions[node]});
  };
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    refresh_priority(node);
  }
  while (true) {
    const Candidates& candidates = propagation.candidates;
    while (!priority_queue.empty() &&
           priority_queue.top().version !=
               priority_versions[priority_queue.top().node]) {
      priority_queue.pop();
    }
    if (priority_queue.empty()) {
      break;
    }
    const int selected_node = priority_queue.top().node;
    priority_queue.pop();
    struct Choice {
      int part = -1;
      double score = 0.0;
    };
    std::vector<Choice> choices;
    for (const int part : candidates[selected_node]) {
      if (!fits(input, loads, selected_node, part)) {
        continue;
      }
      bool empties_other = false;
      if (*std::max_element(input.distances[part].begin(),
                            input.distances[part].end()) > input.hmax) {
        empties_other = trial_empties_domain(
            input, adjacency, propagation, selected_node, part);
      }
      if (!empties_other) {
        choices.push_back({part, score(input, adjacency, assigned, loads,
                                       selected_node, part, false)});
      }
    }
    if (choices.empty()) {
      phase_two[selected_node] = true;
      ++priority_versions[selected_node];
      continue;
    }
    const double maximum = std::max_element(
                               choices.begin(), choices.end(),
                               [](const Choice& left, const Choice& right) {
                                 return left.score < right.score;
                               })
                               ->score;
    double total_probability = 0.0;
    for (const Choice& choice : choices) {
      total_probability +=
          std::exp((choice.score - maximum) / input.temperature);
    }
    double draw = deterministic_unit(input.seed, random_event++) *
                  total_probability;
    Choice selected = choices.back();
    for (const Choice& choice : choices) {
      draw -= std::exp((choice.score - maximum) / input.temperature);
      if (draw <= 0.0) {
        selected = choice;
        break;
      }
    }
    std::set<int> changed_nodes;
    if (propagation.candidates[selected_node].size() != 1 ||
        propagation.candidates[selected_node].front() != selected.part) {
      changed_nodes.insert(selected_node);
    }
    assign_node(input, assigned, loads, selected_node, selected.part);
    ++priority_versions[selected_node];
    records.push_back({selected_node, selected.part, 1, selected.score});
    propagation.candidates[selected_node] = {selected.part};
    propagation.processed[selected_node] = false;
    const auto propagated_changes =
        propagate_new_anchors(input, adjacency, propagation, {selected_node});
    changed_nodes.insert(propagated_changes.begin(), propagated_changes.end());
    for (const int node : changed_nodes) {
      domain_records.push_back(
          {static_cast<int>(records.size()) - 1, node,
           propagation.candidates[node]});
      if (node != selected_node) {
        refresh_priority(node);
      }
    }
    for (const auto& [neighbor, weight] : adjacency[selected_node]) {
      (void)weight;
      if (assigned[neighbor] < 0) {
        refresh_priority(neighbor);
      }
    }
  }

  while (std::any_of(assigned.begin(), assigned.end(),
                     [](int part) { return part < 0; })) {
    int best_node = -1;
    int best_part = -1;
    double best_score = -std::numeric_limits<double>::infinity();
    for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
      if (assigned[node] >= 0) {
        continue;
      }
      for (int part = 0; part < input.parts; ++part) {
        if (!fits(input, loads, node, part)) {
          continue;
        }
        const double candidate_score =
            score(input, adjacency, assigned, loads, node, part, true);
        if (candidate_score > best_score ||
            (candidate_score == best_score &&
             std::tie(node, part) < std::tie(best_node, best_part))) {
          best_score = candidate_score;
          best_node = node;
          best_part = part;
        }
      }
    }
    if (best_node < 0) {
      throw std::runtime_error("initial partition cannot satisfy capacity");
    }
    assign_node(input, assigned, loads, best_node, best_part);
    records.push_back({best_node, best_part, 2, best_score});
  }

  long long violating_pairs = 0;
  double cut = 0.0;
  double connectivity = 0.0;
  double weighted_hops = 0.0;
  double total_pair_weight = 0.0;
  long long capacity_violations = 0;
  long long fixed_violations = 0;
  for (int part = 0; part < input.parts; ++part) {
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      if (loads[part][dimension] > input.capacities[part][dimension]) {
        ++capacity_violations;
      }
    }
  }
  for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
    if (input.nodes[node].fixed_part >= 0 &&
        assigned[node] != input.nodes[node].fixed_part) {
      ++fixed_violations;
    }
  }
  for (const Net& net : input.nets) {
    std::set<int> remote_sink_parts;
    for (const int sink : net.sinks) {
      const int distance = input.distances[assigned[net.source]][assigned[sink]];
      if (assigned[net.source] != assigned[sink]) {
        cut += net.weight;
        remote_sink_parts.insert(assigned[sink]);
      }
      if (distance > input.hmax) {
        ++violating_pairs;
      }
      weighted_hops += net.weight * static_cast<double>(distance);
      total_pair_weight += net.weight;
    }
    connectivity += net.weight * static_cast<double>(remote_sink_parts.size());
  }

  std::ofstream stream(output_path);
  if (!stream) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  stream << "EMUFLOW_MFSPART_INITIALIZER_OUTPUT_V1\n";
  stream << "STATUS PASS\n";
  for (int node = 0; node < static_cast<int>(initial_candidates.size()); ++node) {
    stream << "CAND " << node << ' ' << initial_candidates[node].size();
    for (const int part : initial_candidates[node]) {
      stream << ' ' << part;
    }
    stream << '\n';
  }
  for (const AssignmentRecord& record : records) {
    stream << "ASSIGN " << record.node << ' ' << record.part << ' '
           << record.phase << ' ' << std::setprecision(17) << record.score
           << '\n';
  }
  for (const DomainRecord& record : domain_records) {
    stream << "DOMAIN " << record.assignment_step << ' ' << record.node << ' '
           << record.parts.size();
    for (const int part : record.parts) {
      stream << ' ' << part;
    }
    stream << '\n';
  }
  stream << "METRIC driver_sink_cut " << std::setprecision(17) << cut << '\n';
  stream << "METRIC connectivity " << std::setprecision(17) << connectivity
         << '\n';
  stream << "METRIC violating_pairs " << violating_pairs << '\n';
  stream << "METRIC weighted_hops " << std::setprecision(17) << weighted_hops
         << '\n';
  stream << "METRIC mean_hops " << std::setprecision(17)
         << (total_pair_weight > 0.0 ? weighted_hops / total_pair_weight : 0.0)
         << '\n';
  stream << "METRIC capacity_violations " << capacity_violations << '\n';
  stream << "METRIC fixed_violations " << fixed_violations << '\n';
  stream << "METRIC priority_recomputations " << priority_recomputations
         << '\n';
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_mfspart_initializer INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_mfspart_initializer INPUT OUTPUT\n";
    return 2;
  }
  try {
    run(read_input(argv[1]), argv[2]);
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  return 0;
}
