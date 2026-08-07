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

Candidates propagate(
    const Input& input,
    const std::vector<std::vector<std::pair<int, double>>>& adjacency,
    const std::vector<int>& assigned) {
  Candidates candidates(input.nodes.size());
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    if (assigned[node] >= 0) {
      candidates[node] = {assigned[node]};
    } else {
      candidates[node].resize(input.parts);
      for (int part = 0; part < input.parts; ++part) {
        candidates[node][part] = part;
      }
    }
  }
  std::queue<int> queue;
  std::vector<bool> queued(input.nodes.size(), false);
  for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
    if (assigned[node] >= 0) {
      queue.push(node);
      queued[node] = true;
    }
  }
  while (!queue.empty()) {
    const int anchor = queue.front();
    queue.pop();
    if (candidates[anchor].empty()) {
      continue;
    }
    const int anchor_part = candidates[anchor].front();
    const int max_distance = *std::max_element(
        input.distances[anchor_part].begin(),
        input.distances[anchor_part].end());
    std::vector<int> circuit_distance(input.nodes.size(), -1);
    std::queue<int> bfs;
    circuit_distance[anchor] = 0;
    bfs.push(anchor);
    while (!bfs.empty()) {
      const int node = bfs.front();
      bfs.pop();
      if (circuit_distance[node] * input.hmax >= max_distance) {
        continue;
      }
      for (const auto& [neighbor, unused_weight] : adjacency[node]) {
        (void)unused_weight;
        if (circuit_distance[neighbor] < 0) {
          circuit_distance[neighbor] = circuit_distance[node] + 1;
          bfs.push(neighbor);
        }
      }
    }
    for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
      const int distance = circuit_distance[node];
      if (node == anchor || distance < 0 ||
          distance * input.hmax >= max_distance) {
        continue;
      }
      std::vector<int> filtered;
      for (const int part : candidates[node]) {
        if (input.distances[anchor_part][part] <= distance * input.hmax) {
          filtered.push_back(part);
        }
      }
      if (filtered != candidates[node]) {
        candidates[node] = std::move(filtered);
        if (candidates[node].size() == 1 && !queued[node]) {
          queue.push(node);
          queued[node] = true;
        }
      }
    }
  }
  return candidates;
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

  Candidates initial_candidates = propagate(input, adjacency, assigned);
  for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
    if (assigned[node] >= 0 && initial_candidates[node].empty()) {
      throw std::runtime_error("fixed assignments violate FPGA topology");
    }
  }
  std::uint64_t random_event = 0;
  std::vector<bool> phase_two(input.nodes.size(), false);
  while (true) {
    Candidates candidates = propagate(input, adjacency, assigned);
    int selected_node = -1;
    double selected_priority = -1.0;
    for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
      if (assigned[node] >= 0 || candidates[node].empty() || phase_two[node]) {
        continue;
      }
      double total = 0.0;
      double fixed = 0.0;
      for (const auto& [neighbor, weight] : adjacency[node]) {
        total += weight;
        if (assigned[neighbor] >= 0) {
          fixed += weight;
        }
      }
      const double priority = total > 0.0 ? fixed / total : 0.0;
      if (priority > selected_priority ||
          (priority == selected_priority && node < selected_node)) {
        selected_priority = priority;
        selected_node = node;
      }
    }
    if (selected_node < 0) {
      break;
    }
    struct Choice {
      int part = -1;
      double score = 0.0;
    };
    std::vector<Choice> choices;
    for (const int part : candidates[selected_node]) {
      if (!fits(input, loads, selected_node, part)) {
        continue;
      }
      std::vector<int> trial = assigned;
      trial[selected_node] = part;
      const Candidates trial_candidates = propagate(input, adjacency, trial);
      bool empties_other = false;
      for (int node = 0; node < static_cast<int>(trial.size()); ++node) {
        if (trial_candidates[node].empty()) {
          empties_other = true;
          break;
        }
      }
      if (!empties_other) {
        choices.push_back({part, score(input, adjacency, assigned, loads,
                                       selected_node, part, false)});
      }
    }
    if (choices.empty()) {
      phase_two[selected_node] = true;
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
    assign_node(input, assigned, loads, selected_node, selected.part);
    records.push_back({selected_node, selected.part, 1, selected.score});
    const Candidates updated_candidates = propagate(input, adjacency, assigned);
    for (int node = 0; node < static_cast<int>(assigned.size()); ++node) {
      if (updated_candidates[node].size() < candidates[node].size()) {
        domain_records.push_back(
            {static_cast<int>(records.size()) - 1, node,
             updated_candidates[node]});
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
