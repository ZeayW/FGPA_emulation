// SPDX-License-Identifier: Apache-2.0
//
// First-party EmuFlow min-used-FPGA legalization extension. This is not
// claimed as an algorithm from the MFSPart paper or companion repository.

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace {

struct Node {
  int fixed_part = -1;
  std::vector<long long> weights;
};

struct Net {
  double weight = 0.0;
  int source = -1;
  std::vector<int> sinks;
};

struct Input {
  int parts = 0;
  int dimensions = 0;
  int min_used = 0;
  std::vector<std::vector<long long>> capacities;
  std::vector<Node> nodes;
  std::vector<Net> nets;
  std::vector<int> assignment;
};

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string header;
  std::getline(stream, header);
  if (header != "EMUFLOW_MFSPART_LEGALIZER_INPUT_V1") {
    throw std::runtime_error("invalid input header");
  }
  Input input;
  int node_count = 0;
  int net_count = 0;
  bool saw_param = false;
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
          input.min_used;
      if (input.parts <= 0 || node_count <= 0 || input.dimensions <= 0 ||
          net_count < 0 || input.min_used <= 0 ||
          input.min_used > input.parts) {
        throw std::runtime_error("invalid PARAM record");
      }
      input.capacities.assign(
          input.parts, std::vector<long long>(input.dimensions, 0));
      input.nodes.resize(node_count);
      input.nets.resize(net_count);
      input.assignment.assign(node_count, -1);
      saw_capacities.assign(input.parts,
                            std::vector<bool>(input.dimensions, false));
      saw_nodes.assign(node_count, false);
      saw_nets.assign(net_count, false);
      saw_assignments.assign(node_count, false);
      saw_param = true;
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
          node.weights.front() <= 0 ||
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
      throw std::runtime_error("malformed input record: " + kind);
    }
  }
  if (!saw_param ||
      std::any_of(saw_capacities.begin(), saw_capacities.end(),
                  [](const std::vector<bool>& row) {
                    return std::any_of(row.begin(), row.end(),
                                       [](bool value) { return !value; });
                  }) ||
      std::any_of(saw_nodes.begin(), saw_nodes.end(),
                  [](bool value) { return !value; }) ||
      std::any_of(saw_nets.begin(), saw_nets.end(),
                  [](bool value) { return !value; }) ||
      std::any_of(saw_assignments.begin(), saw_assignments.end(),
                  [](bool value) { return !value; })) {
    throw std::runtime_error("incomplete input");
  }
  for (int node = 0; node < node_count; ++node) {
    if (input.nodes[node].fixed_part >= 0 &&
        input.nodes[node].fixed_part != input.assignment[node]) {
      throw std::runtime_error("initial assignment violates fixed node");
    }
  }
  return input;
}

struct Move {
  int node = -1;
  int source = -1;
  int target = -1;
  double pair_delta = 0.0;
  double connectivity_delta = 0.0;
};

void run(const Input& input, const std::string& output_path) {
  std::vector<int> assignment = input.assignment;
  std::vector<int> counts(input.parts, 0);
  std::vector<std::vector<long long>> loads(
      input.parts, std::vector<long long>(input.dimensions, 0));
  for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
    const int part = assignment[node];
    ++counts[part];
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      loads[part][dimension] += input.nodes[node].weights[dimension];
      if (loads[part][dimension] > input.capacities[part][dimension]) {
        throw std::runtime_error("initial assignment exceeds capacity");
      }
    }
  }
  std::vector<std::vector<int>> incident(input.nodes.size());
  std::vector<std::map<int, int>> net_part_counts(input.nets.size());
  for (int net_index = 0; net_index < static_cast<int>(input.nets.size());
       ++net_index) {
    const Net& net = input.nets[net_index];
    incident[net.source].push_back(net_index);
    ++net_part_counts[net_index][assignment[net.source]];
    for (const int sink : net.sinks) {
      incident[sink].push_back(net_index);
      ++net_part_counts[net_index][assignment[sink]];
    }
  }

  const auto deltas = [&](int node, int source, int target) {
    double pair_delta = 0.0;
    double connectivity_delta = 0.0;
    for (const int net_index : incident[node]) {
      const Net& net = input.nets[net_index];
      const int before_parts = net_part_counts[net_index].size();
      std::map<int, int> after_counts = net_part_counts[net_index];
      if (--after_counts[source] == 0) {
        after_counts.erase(source);
      }
      ++after_counts[target];
      connectivity_delta +=
          net.weight * (static_cast<int>(after_counts.size()) - before_parts);
      const int before_source = assignment[net.source];
      const int after_source = net.source == node ? target : before_source;
      for (const int sink : net.sinks) {
        const int before_sink = assignment[sink];
        const int after_sink = sink == node ? target : before_sink;
        pair_delta += net.weight *
                      (static_cast<int>(after_source != after_sink) -
                       static_cast<int>(before_source != before_sink));
      }
    }
    return std::pair<double, double>{pair_delta, connectivity_delta};
  };

  std::vector<Move> moves;
  const auto used_parts = [&]() {
    return std::count_if(counts.begin(), counts.end(),
                         [](int count) { return count > 0; });
  };
  while (used_parts() < input.min_used) {
    const int target = static_cast<int>(
        std::find(counts.begin(), counts.end(), 0) - counts.begin());
    bool found = false;
    Move best;
    std::tuple<double, double, long long, int, int> best_key;
    for (int node = 0; node < static_cast<int>(input.nodes.size()); ++node) {
      const int source = assignment[node];
      if (counts[source] <= 1 || input.nodes[node].fixed_part >= 0) {
        continue;
      }
      bool fits = true;
      for (int dimension = 0; dimension < input.dimensions; ++dimension) {
        if (loads[target][dimension] + input.nodes[node].weights[dimension] >
            input.capacities[target][dimension]) {
          fits = false;
        }
      }
      if (!fits) {
        continue;
      }
      const auto [pair_delta, connectivity_delta] =
          deltas(node, source, target);
      const auto key = std::make_tuple(pair_delta, connectivity_delta,
                                       input.nodes[node].weights.front(), node,
                                       source);
      if (!found || key < best_key) {
        found = true;
        best_key = key;
        best = {node, source, target, pair_delta, connectivity_delta};
      }
    }
    if (!found) {
      throw std::runtime_error("no legal move can satisfy min_used");
    }
    assignment[best.node] = best.target;
    --counts[best.source];
    ++counts[best.target];
    for (int dimension = 0; dimension < input.dimensions; ++dimension) {
      const long long weight = input.nodes[best.node].weights[dimension];
      loads[best.source][dimension] -= weight;
      loads[best.target][dimension] += weight;
    }
    for (const int net_index : incident[best.node]) {
      std::map<int, int>& part_counts = net_part_counts[net_index];
      if (--part_counts[best.source] == 0) {
        part_counts.erase(best.source);
      }
      ++part_counts[best.target];
    }
    moves.push_back(best);
  }

  std::ofstream stream(output_path);
  if (!stream) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  stream << "EMUFLOW_MFSPART_LEGALIZER_OUTPUT_V1\nSTATUS PASS\n";
  for (int step = 0; step < static_cast<int>(moves.size()); ++step) {
    const Move& move = moves[step];
    stream << "MOVE " << step << ' ' << move.node << ' ' << move.source << ' '
           << move.target << ' ' << std::setprecision(17) << move.pair_delta
           << ' ' << move.connectivity_delta << '\n';
  }
  for (int node = 0; node < static_cast<int>(assignment.size()); ++node) {
    stream << "FINAL " << node << ' ' << assignment[node] << '\n';
  }
  stream << "METRIC moves " << moves.size() << '\n';
  stream << "METRIC used_parts " << used_parts() << '\n';
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_mfspart_legalizer INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_mfspart_legalizer INPUT OUTPUT\n";
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
