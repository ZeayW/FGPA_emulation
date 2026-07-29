// SPDX-License-Identifier: Apache-2.0
//
// Exact sparse minimum-cost bipartite flow for physical package-pin binding.
// The Python control plane validates electrical compatibility and emits only
// legal demand/channel edges. This engine chooses a globally minimum-cost,
// one-to-one assignment without using a proprietary solver.

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {

struct Demand {
  int domain = -1;
};

struct Channel {
  int domain = -1;
};

struct CompatibleEdge {
  int demand = -1;
  int channel = -1;
  std::int64_t cost = 0;
};

struct Input {
  std::vector<Demand> demands;
  std::vector<Channel> channels;
  std::vector<CompatibleEdge> edges;
};

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string line;
  std::getline(stream, line);
  if (line != "EMUFLOW_BSP_PIN_SOLVER_INPUT_V1") {
    throw std::runtime_error("invalid input header");
  }
  Input input;
  while (std::getline(stream, line)) {
    if (line.empty() || line[0] == '#') {
      continue;
    }
    std::stringstream record(line);
    std::string kind;
    record >> kind;
    if (kind == "DEMAND") {
      int index = -1;
      Demand demand;
      record >> index >> demand.domain;
      if (index != static_cast<int>(input.demands.size())) {
        throw std::runtime_error("DEMAND indices must be contiguous");
      }
      input.demands.push_back(demand);
    } else if (kind == "CHANNEL") {
      int index = -1;
      Channel channel;
      record >> index >> channel.domain;
      if (index != static_cast<int>(input.channels.size())) {
        throw std::runtime_error("CHANNEL indices must be contiguous");
      }
      input.channels.push_back(channel);
    } else if (kind == "EDGE") {
      CompatibleEdge edge;
      record >> edge.demand >> edge.channel >> edge.cost;
      input.edges.push_back(edge);
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!record) {
      throw std::runtime_error("malformed input record: " + line);
    }
  }
  if (input.demands.empty() || input.channels.empty()) {
    throw std::runtime_error("pin-binding model is empty");
  }
  for (const Demand& demand : input.demands) {
    if (demand.domain < 0) {
      throw std::runtime_error("demand domain must be non-negative");
    }
  }
  for (const Channel& channel : input.channels) {
    if (channel.domain < 0) {
      throw std::runtime_error("channel domain must be non-negative");
    }
  }
  for (const CompatibleEdge& edge : input.edges) {
    if (edge.demand < 0 ||
        edge.demand >= static_cast<int>(input.demands.size()) ||
        edge.channel < 0 ||
        edge.channel >= static_cast<int>(input.channels.size()) ||
        edge.cost < 0 ||
        input.demands[edge.demand].domain !=
            input.channels[edge.channel].domain) {
      throw std::runtime_error("invalid compatible edge");
    }
  }
  std::sort(input.edges.begin(), input.edges.end(),
            [](const CompatibleEdge& lhs, const CompatibleEdge& rhs) {
              return std::tie(lhs.demand, lhs.channel, lhs.cost) <
                  std::tie(rhs.demand, rhs.channel, rhs.cost);
            });
  for (std::size_t index = 1; index < input.edges.size(); ++index) {
    if (input.edges[index - 1].demand == input.edges[index].demand &&
        input.edges[index - 1].channel == input.edges[index].channel) {
      throw std::runtime_error("duplicate compatible edge");
    }
  }
  return input;
}

struct Edge {
  int to = -1;
  int reverse = -1;
  int capacity = 0;
  std::int64_t cost = 0;
};

class MinCostFlow {
 public:
  explicit MinCostFlow(int nodes) : graph_(nodes) {}

  void add_edge(int from, int to, int capacity, std::int64_t cost) {
    Edge forward{to, static_cast<int>(graph_[to].size()), capacity, cost};
    Edge reverse{from, static_cast<int>(graph_[from].size()), 0, -cost};
    graph_[from].push_back(forward);
    graph_[to].push_back(reverse);
  }

  std::pair<int, std::int64_t> solve(int source, int sink, int target_flow) {
    const int nodes = static_cast<int>(graph_.size());
    const std::int64_t infinity =
        std::numeric_limits<std::int64_t>::max() / 4;
    int flow = 0;
    std::int64_t total_cost = 0;
    while (flow < target_flow) {
      std::vector<std::int64_t> distance(nodes, infinity);
      std::vector<int> previous_node(nodes, -1);
      std::vector<int> previous_edge(nodes, -1);
      std::vector<char> in_queue(nodes, false);
      std::queue<int> queue;
      distance[source] = 0;
      queue.push(source);
      in_queue[source] = true;
      while (!queue.empty()) {
        const int node = queue.front();
        queue.pop();
        in_queue[node] = false;
        for (int edge_index = 0;
             edge_index < static_cast<int>(graph_[node].size());
             ++edge_index) {
          const Edge& edge = graph_[node][edge_index];
          if (edge.capacity <= 0) {
            continue;
          }
          const std::int64_t candidate = distance[node] + edge.cost;
          if (candidate < distance[edge.to]) {
            distance[edge.to] = candidate;
            previous_node[edge.to] = node;
            previous_edge[edge.to] = edge_index;
            if (!in_queue[edge.to]) {
              queue.push(edge.to);
              in_queue[edge.to] = true;
            }
          }
        }
      }
      if (distance[sink] == infinity) {
        break;
      }
      for (int node = sink; node != source;
           node = previous_node[node]) {
        if (previous_node[node] < 0) {
          throw std::runtime_error("broken augmenting path");
        }
        Edge& edge =
            graph_[previous_node[node]][previous_edge[node]];
        --edge.capacity;
        ++graph_[node][edge.reverse].capacity;
      }
      ++flow;
      total_cost += distance[sink];
    }
    return {flow, total_cost};
  }

  const std::vector<Edge>& edges(int node) const {
    return graph_[node];
  }

 private:
  std::vector<std::vector<Edge>> graph_;
};

void run(const std::string& input_path, const std::string& output_path) {
  const Input input = read_input(input_path);
  const int source = 0;
  const int first_demand = 1;
  const int first_channel =
      first_demand + static_cast<int>(input.demands.size());
  const int sink =
      first_channel + static_cast<int>(input.channels.size());
  MinCostFlow flow(sink + 1);
  for (int demand = 0;
       demand < static_cast<int>(input.demands.size()); ++demand) {
    flow.add_edge(source, first_demand + demand, 1, 0);
  }
  for (const CompatibleEdge& edge : input.edges) {
    flow.add_edge(first_demand + edge.demand,
                  first_channel + edge.channel, 1, edge.cost);
  }
  for (int channel = 0;
       channel < static_cast<int>(input.channels.size()); ++channel) {
    flow.add_edge(first_channel + channel, sink, 1, 0);
  }
  const auto [assigned, total_cost] = flow.solve(
      source, sink, static_cast<int>(input.demands.size()));
  if (assigned != static_cast<int>(input.demands.size())) {
    throw std::runtime_error(
        "no complete electrically legal package-pin assignment exists");
  }

  std::vector<std::tuple<int, int, std::int64_t>> assignment;
  for (int demand = 0;
       demand < static_cast<int>(input.demands.size()); ++demand) {
    const int node = first_demand + demand;
    for (const Edge& edge : flow.edges(node)) {
      if (edge.to < first_channel || edge.to >= sink ||
          edge.capacity != 0) {
        continue;
      }
      assignment.emplace_back(
          demand, edge.to - first_channel, edge.cost);
    }
  }
  if (assignment.size() != input.demands.size()) {
    throw std::runtime_error("could not reconstruct min-cost assignment");
  }
  std::sort(assignment.begin(), assignment.end());

  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  output << "EMUFLOW_BSP_PIN_SOLVER_OUTPUT_V1\n";
  output << "METRIC " << assigned << " " << total_cost << "\n";
  for (const auto& [demand, channel, cost] : assignment) {
    output << "ASSIGN " << demand << " " << channel << " " << cost << "\n";
  }
}

}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc != 3) {
      throw std::runtime_error(
          "usage: emuflow_bsp_pin_solver INPUT OUTPUT");
    }
    run(argv[1], argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_bsp_pin_solver: " << error.what() << "\n";
    return 2;
  }
}
