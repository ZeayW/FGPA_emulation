// SPDX-License-Identifier: Apache-2.0
//
// Independent paper-level reproduction of MFSPart direct k-way FM refinement
// (TCAD 2026, Eqs. 9--10).  No source from the unlicensed companion
// repository is copied or linked.

#include <algorithm>
#include <cmath>
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
  int move_distance = 0;
  int early_stop = 0;
  double gamma = 0.0;
  double lambda = 0.0;
  double mu = 0.0;
  std::vector<std::vector<int>> distances;
  std::vector<std::vector<long long>> capacities;
  std::vector<Node> nodes;
  std::vector<Net> nets;
  std::vector<int> assignment;
};

struct Move {
  int node = -1;
  int source = -1;
  int target = -1;
  double gain = 0.0;
  double cumulative = 0.0;
};

struct CandidateMove {
  double gain = -std::numeric_limits<double>::infinity();
  int node = -1;
  int target = -1;
  int version = -1;

  bool operator<(const CandidateMove& other) const {
    if (gain != other.gain) {
      return gain < other.gain;
    }
    if (node != other.node) {
      return node > other.node;
    }
    return target > other.target;
  }
};

struct Metrics {
  double driver_sink_cut = 0.0;
  double connectivity = 0.0;
  double weighted_hops = 0.0;
  double mean_hops = 0.0;
  long long violating_pairs = 0;
  long long capacity_violations = 0;
  long long fixed_violations = 0;
};

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string magic;
  std::getline(stream, magic);
  if (magic != "EMUFLOW_MFSPART_REFINER_INPUT_V1") {
    throw std::runtime_error("unsupported input header");
  }
  Input input;
  int node_count = -1;
  int net_count = -1;
  bool saw_param = false;
  std::vector<std::vector<bool>> saw_distances;
  std::vector<std::vector<bool>> saw_capacities;
  std::vector<bool> saw_nodes;
  std::vector<bool> saw_nets;
  std::vector<bool> saw_assignments;
  std::string kind;
  while (stream >> kind) {
    if (kind == "PARAM") {
      if (saw_param) {
        throw std::runtime_error("duplicate PARAM record");
      }
      stream >> input.parts >> node_count >> input.dimensions >> net_count >>
          input.hmax >> input.move_distance >> input.early_stop >> input.gamma >>
          input.lambda >> input.mu;
      if (input.parts <= 0 || node_count <= 0 || input.dimensions <= 0 ||
          net_count < 0 || input.hmax < 1 || input.move_distance < 1 ||
          input.early_stop < 1 || !std::isfinite(input.gamma) ||
          !std::isfinite(input.lambda) || !std::isfinite(input.mu) ||
          input.gamma < 0.0 || input.lambda < 0.0 || input.mu < 0.0) {
        throw std::runtime_error("invalid PARAM record");
      }
      input.distances.assign(input.parts,
                             std::vector<int>(input.parts, -1));
      input.capacities.assign(
          input.parts, std::vector<long long>(input.dimensions, 0));
      input.nodes.assign(node_count, Node{});
      input.nets.assign(net_count, Net{});
      input.assignment.assign(node_count, -1);
      saw_distances.assign(input.parts,
                           std::vector<bool>(input.parts, false));
      saw_capacities.assign(input.parts,
                            std::vector<bool>(input.dimensions, false));
      saw_nodes.assign(node_count, false);
      saw_nets.assign(net_count, false);
      saw_assignments.assign(node_count, false);
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
      if (index < 0 || index >= node_count || saw_nodes[index] ||
          node.fixed_part < -1 || node.fixed_part >= input.parts ||
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
    } else if (kind == "ASSIGN") {
      if (!saw_param) {
        throw std::runtime_error("ASSIGN record precedes PARAM");
      }
      int node = -1;
      int part = -1;
      stream >> node >> part;
      if (node < 0 || node >= node_count || part < 0 || part >= input.parts ||
          saw_assignments[node]) {
        throw std::runtime_error("invalid or duplicate ASSIGN record");
      }
      input.assignment[node] = part;
      saw_assignments[node] = true;
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!stream) {
      throw std::runtime_error("malformed input record");
    }
  }
  const auto missing = [](const auto& values) {
    return std::any_of(values.begin(), values.end(),
                       [](bool value) { return !value; });
  };
  if (!saw_param || missing(saw_nodes) || missing(saw_nets) ||
      missing(saw_assignments)) {
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

std::vector<std::vector<std::pair<int, double>>> build_pair_adjacency(
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

std::vector<std::vector<int>> build_incidence(const Input& input) {
  std::vector<std::vector<int>> incidence(input.nodes.size());
  for (int net_index = 0; net_index < static_cast<int>(input.nets.size());
       ++net_index) {
    const Net& net = input.nets[net_index];
    incidence[net.source].push_back(net_index);
    for (const int sink : net.sinks) {
      incidence[sink].push_back(net_index);
    }
  }
  return incidence;
}

std::vector<std::vector<long long>> compute_loads(
    const Input& input, const std::vector<int>& assignment) {
  std::vector<std::vector<long long>> loads(
      input.parts, std::vector<long long>(input.dimensions, 0));
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      loads[assignment[node]][dimension] += input.nodes[node].weights[dimension];
    }
  }
  return loads;
}

bool target_fits(const Input& input,
                 const std::vector<std::vector<long long>>& loads, int node,
                 int target) {
  for (int dimension = 0; dimension < input.dimensions; ++dimension) {
    if (loads[target][dimension] + input.nodes[node].weights[dimension] >
        input.capacities[target][dimension]) {
      return false;
    }
  }
  return true;
}

double compatibility(
    const Input& input,
    const std::vector<std::vector<std::pair<int, double>>>& adjacency,
    const std::vector<std::vector<int>>& incidence,
    const std::vector<int>& assignment, int node, int candidate_part) {
  double local_hop_score = 0.0;
  double violation_penalty = 0.0;
  for (const auto& [neighbor, weight] : adjacency[node]) {
    const int distance = input.distances[assignment[neighbor]][candidate_part];
    if (distance <= input.hmax) {
      local_hop_score +=
          static_cast<double>(input.hmax - distance) * weight;
    } else {
      violation_penalty +=
          weight * (1.0 + input.mu * (distance - input.hmax));
    }
  }
  double connectivity = 0.0;
  for (const int net_index : incidence[node]) {
    const Net& net = input.nets[net_index];
    std::set<int> spanned;
    spanned.insert(net.source == node ? candidate_part
                                      : assignment[net.source]);
    for (const int sink : net.sinks) {
      spanned.insert(sink == node ? candidate_part : assignment[sink]);
    }
    connectivity += net.weight * static_cast<double>(spanned.size());
  }
  return local_hop_score - input.gamma * connectivity -
         input.lambda * violation_penalty;
}

Metrics compute_metrics(const Input& input,
                        const std::vector<int>& assignment) {
  Metrics metrics;
  double total_pair_weight = 0.0;
  for (const Net& net : input.nets) {
    std::set<int> remote_sink_parts;
    for (const int sink : net.sinks) {
      const int distance =
          input.distances[assignment[net.source]][assignment[sink]];
      if (assignment[net.source] != assignment[sink]) {
        metrics.driver_sink_cut += net.weight;
        remote_sink_parts.insert(assignment[sink]);
      }
      if (distance > input.hmax) {
        ++metrics.violating_pairs;
      }
      metrics.weighted_hops += net.weight * static_cast<double>(distance);
      total_pair_weight += net.weight;
    }
    metrics.connectivity +=
        net.weight * static_cast<double>(remote_sink_parts.size());
  }
  metrics.mean_hops = total_pair_weight > 0.0
                           ? metrics.weighted_hops / total_pair_weight
                           : 0.0;
  const auto loads = compute_loads(input, assignment);
  for (int part = 0; part < input.parts; ++part) {
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      if (loads[part][dimension] > input.capacities[part][dimension]) {
        ++metrics.capacity_violations;
      }
    }
  }
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    if (input.nodes[node].fixed_part >= 0 &&
        assignment[node] != input.nodes[node].fixed_part) {
      ++metrics.fixed_violations;
    }
  }
  return metrics;
}

void write_metrics(std::ostream& stream, const std::string& prefix,
                   const Metrics& metrics) {
  stream << "METRIC " << prefix << "_driver_sink_cut " << std::setprecision(17)
         << metrics.driver_sink_cut << '\n';
  stream << "METRIC " << prefix << "_connectivity " << std::setprecision(17)
         << metrics.connectivity << '\n';
  stream << "METRIC " << prefix << "_weighted_hops " << std::setprecision(17)
         << metrics.weighted_hops << '\n';
  stream << "METRIC " << prefix << "_mean_hops " << std::setprecision(17)
         << metrics.mean_hops << '\n';
  stream << "METRIC " << prefix << "_violating_pairs "
         << metrics.violating_pairs << '\n';
  stream << "METRIC " << prefix << "_capacity_violations "
         << metrics.capacity_violations << '\n';
  stream << "METRIC " << prefix << "_fixed_violations "
         << metrics.fixed_violations << '\n';
}

void run(const Input& input, const std::string& output_path) {
  std::vector<int> assignment = input.assignment;
  auto loads = compute_loads(input, assignment);
  const Metrics initial_metrics = compute_metrics(input, assignment);
  if (initial_metrics.capacity_violations != 0 ||
      initial_metrics.fixed_violations != 0) {
    throw std::runtime_error("initial assignment violates capacity or fixed nodes");
  }
  const auto adjacency = build_pair_adjacency(input);
  const auto incidence = build_incidence(input);
  std::vector<bool> locked(input.nodes.size(), false);
  std::vector<int> versions(input.nodes.size(), 0);
  std::priority_queue<CandidateMove> queue;
  std::vector<Move> moves;
  long long compatibility_evaluations = 0;
  long long candidate_recomputations = 0;
  long long capacity_invalidations = 0;

  std::vector<std::vector<std::pair<long long, int>>> weight_index(
      input.dimensions);
  for (int dimension = 0; dimension < input.dimensions; ++dimension) {
    for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
      weight_index[dimension].push_back(
          {input.nodes[node].weights[dimension], node});
    }
    std::sort(weight_index[dimension].begin(),
              weight_index[dimension].end());
  }

  auto recompute_candidate = [&](int node) {
    ++versions[node];
    if (locked[node] || input.nodes[node].fixed_part >= 0) {
      return;
    }
    ++candidate_recomputations;
    const int source = assignment[node];
    const double source_score =
        compatibility(input, adjacency, incidence, assignment, node, source);
    ++compatibility_evaluations;
    int best_target = -1;
    double best_gain = -std::numeric_limits<double>::infinity();
    for (int target = 0; target < input.parts; ++target) {
      if (target == source ||
          input.distances[source][target] > input.move_distance ||
          !target_fits(input, loads, node, target)) {
        continue;
      }
      const double gain =
          compatibility(input, adjacency, incidence, assignment, node,
                        target) -
          source_score;
      ++compatibility_evaluations;
      if (gain > best_gain ||
          (gain == best_gain && target < best_target)) {
        best_gain = gain;
        best_target = target;
      }
    }
    if (best_target >= 0) {
      queue.push({best_gain, node, best_target, versions[node]});
    }
  };
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    recompute_candidate(node);
  }

  double cumulative = 0.0;
  double best_cumulative = 0.0;
  int best_prefix = 0;
  int ineffective = 0;
  while (ineffective < input.early_stop) {
    while (!queue.empty() &&
           (locked[queue.top().node] ||
            queue.top().version != versions[queue.top().node])) {
      queue.pop();
    }
    if (queue.empty()) {
      break;
    }
    const CandidateMove best = queue.top();
    queue.pop();
    const int best_node = best.node;
    const int best_target = best.target;
    const double best_gain = best.gain;
    const int source = assignment[best_node];
    std::vector<long long> old_source_remaining(input.dimensions);
    std::vector<long long> old_target_remaining(input.dimensions);
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      old_source_remaining[dimension] =
          input.capacities[source][dimension] - loads[source][dimension];
      old_target_remaining[dimension] =
          input.capacities[best_target][dimension] -
          loads[best_target][dimension];
    }
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      loads[source][dimension] -= input.nodes[best_node].weights[dimension];
      loads[best_target][dimension] += input.nodes[best_node].weights[dimension];
    }
    assignment[best_node] = best_target;
    locked[best_node] = true;
    ++versions[best_node];
    cumulative += best_gain;
    moves.push_back({best_node, source, best_target, best_gain, cumulative});
    if (cumulative > best_cumulative) {
      best_cumulative = cumulative;
      best_prefix = static_cast<int>(moves.size());
      ineffective = 0;
    } else {
      ++ineffective;
    }

    std::set<int> affected;
    for (const auto& [neighbor, unused_weight] : adjacency[best_node]) {
      (void)unused_weight;
      affected.insert(neighbor);
    }
    for (const int net_index : incidence[best_node]) {
      const Net& net = input.nets[net_index];
      affected.insert(net.source);
      affected.insert(net.sinks.begin(), net.sinks.end());
    }
    auto invalidate_capacity_interval = [&](int dimension, long long low,
                                            long long high) {
      if (low >= high) {
        return;
      }
      const auto begin = std::upper_bound(
          weight_index[dimension].begin(), weight_index[dimension].end(),
          std::pair<long long, int>{low, std::numeric_limits<int>::max()});
      const auto end = std::upper_bound(
          weight_index[dimension].begin(), weight_index[dimension].end(),
          std::pair<long long, int>{high, std::numeric_limits<int>::max()});
      for (auto iterator = begin; iterator != end; ++iterator) {
        if (affected.insert(iterator->second).second) {
          ++capacity_invalidations;
        }
      }
    };
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      const long long new_source_remaining =
          input.capacities[source][dimension] - loads[source][dimension];
      const long long new_target_remaining =
          input.capacities[best_target][dimension] -
          loads[best_target][dimension];
      invalidate_capacity_interval(dimension, old_source_remaining[dimension],
                                   new_source_remaining);
      invalidate_capacity_interval(dimension, new_target_remaining,
                                   old_target_remaining[dimension]);
    }
    affected.erase(best_node);
    for (const int node : affected) {
      recompute_candidate(node);
    }
  }
  for (int index = static_cast<int>(moves.size()) - 1; index >= best_prefix;
       --index) {
    const Move& move = moves[index];
    assignment[move.node] = move.source;
  }
  const Metrics final_metrics = compute_metrics(input, assignment);

  std::ofstream stream(output_path);
  if (!stream) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  stream << "EMUFLOW_MFSPART_REFINER_OUTPUT_V1\n";
  stream << "STATUS PASS\n";
  for (int index = 0; index < static_cast<int>(moves.size()); ++index) {
    const Move& move = moves[index];
    stream << "MOVE " << index << ' ' << move.node << ' ' << move.source << ' '
           << move.target << ' ' << std::setprecision(17) << move.gain << ' '
           << move.cumulative << ' ' << (index < best_prefix ? 1 : 0) << '\n';
  }
  for (int node = 0; node < static_cast<int>(assignment.size()); ++node) {
    stream << "FINAL " << node << ' ' << assignment[node] << '\n';
  }
  stream << "METRIC attempted_moves " << moves.size() << '\n';
  stream << "METRIC best_prefix " << best_prefix << '\n';
  stream << "METRIC best_cumulative_gain " << std::setprecision(17)
         << best_cumulative << '\n';
  stream << "METRIC candidate_recomputations " << candidate_recomputations
         << '\n';
  stream << "METRIC compatibility_evaluations "
         << compatibility_evaluations << '\n';
  stream << "METRIC capacity_invalidations " << capacity_invalidations
         << '\n';
  write_metrics(stream, "initial", initial_metrics);
  write_metrics(stream, "final", final_metrics);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_mfspart_refiner INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_mfspart_refiner INPUT OUTPUT\n";
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
