// SPDX-License-Identifier: Apache-2.0
// Chimew Section 3.4 two-stage bank/channel assignment kernel.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <queue>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {

struct Point {
  double x = 0.0;
  double y = 0.0;
};

struct Member {
  Point fanout;
  std::vector<Point> fanins;
};

struct Group {
  int index = -1;
  int domain = -1;
  int kind = -1;       // 0: TDM group, 1: common signal.
  int direction = -1;  // 0: FPGA A -> B, 1: FPGA B -> A.
  int expected_members = 0;
  std::vector<Member> members;
};

struct Channel {
  int index = -1;
  int bank = -1;
  int order = -1;
  Point pin_a;
  Point pin_b;
};

struct BankPair {
  int index = -1;
  int domain = -1;
  Point bank_a;
  Point bank_b;
  std::vector<int> channels;
};

struct Input {
  double cost_scale = 0.0;
  std::vector<BankPair> banks;
  std::vector<Channel> channels;
  std::vector<Group> groups;
};

double manhattan(const Point& lhs, const Point& rhs) {
  return std::abs(lhs.x - rhs.x) + std::abs(lhs.y - rhs.y);
}

bool finite_point(const Point& point) {
  return std::isfinite(point.x) && std::isfinite(point.y);
}

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  std::string header;
  if (!(stream >> header) ||
      header != "EMUFLOW_CHIMEW_BANK_CHANNEL_INPUT_V1") {
    throw std::runtime_error("invalid Chimew bank/channel input header");
  }
  Input input;
  std::string record;
  while (stream >> record) {
    if (record == "PARAM") {
      if (!(stream >> input.cost_scale) || !std::isfinite(input.cost_scale) ||
          input.cost_scale <= 0.0) {
        throw std::runtime_error("invalid Chimew cost scale");
      }
    } else if (record == "BANK") {
      BankPair bank;
      if (!(stream >> bank.index >> bank.domain >> bank.bank_a.x >>
            bank.bank_a.y >> bank.bank_b.x >> bank.bank_b.y) ||
          bank.index != static_cast<int>(input.banks.size()) ||
          bank.domain < 0 || !finite_point(bank.bank_a) ||
          !finite_point(bank.bank_b)) {
        throw std::runtime_error("invalid Chimew bank pair");
      }
      input.banks.push_back(bank);
    } else if (record == "CHANNEL") {
      Channel channel;
      if (!(stream >> channel.index >> channel.bank >> channel.order >>
            channel.pin_a.x >> channel.pin_a.y >> channel.pin_b.x >>
            channel.pin_b.y) ||
          channel.index != static_cast<int>(input.channels.size()) ||
          channel.bank < 0 ||
          channel.bank >= static_cast<int>(input.banks.size()) ||
          channel.order < 0 || !finite_point(channel.pin_a) ||
          !finite_point(channel.pin_b)) {
        throw std::runtime_error("invalid Chimew channel");
      }
      input.banks[channel.bank].channels.push_back(channel.index);
      input.channels.push_back(channel);
    } else if (record == "GROUP") {
      Group group;
      if (!(stream >> group.index >> group.domain >> group.kind >>
            group.direction >> group.expected_members) ||
          group.index != static_cast<int>(input.groups.size()) ||
          group.domain < 0 || (group.kind != 0 && group.kind != 1) ||
          (group.direction != 0 && group.direction != 1) ||
          group.expected_members <= 0 ||
          (group.kind == 1 && group.expected_members != 1)) {
        throw std::runtime_error("invalid Chimew signal group");
      }
      input.groups.push_back(group);
    } else if (record == "MEMBER") {
      int group_index = -1;
      int member_index = -1;
      int fanin_count = 0;
      Member member;
      if (!(stream >> group_index >> member_index >> member.fanout.x >>
            member.fanout.y >> fanin_count) ||
          group_index < 0 ||
          group_index >= static_cast<int>(input.groups.size()) ||
          member_index !=
              static_cast<int>(input.groups[group_index].members.size()) ||
          fanin_count <= 0 || !finite_point(member.fanout)) {
        throw std::runtime_error("invalid Chimew signal member");
      }
      member.fanins.resize(fanin_count);
      for (Point& fanin : member.fanins) {
        if (!(stream >> fanin.x >> fanin.y) || !finite_point(fanin)) {
          throw std::runtime_error("invalid Chimew fanin location");
        }
      }
      input.groups[group_index].members.push_back(std::move(member));
    } else {
      throw std::runtime_error("invalid Chimew bank/channel record");
    }
  }
  if (!(input.cost_scale > 0.0) || input.banks.empty() ||
      input.channels.empty() || input.groups.empty()) {
    throw std::runtime_error("incomplete Chimew bank/channel input");
  }
  for (BankPair& bank : input.banks) {
    if (bank.channels.empty()) {
      throw std::runtime_error("Chimew bank pair has no channels");
    }
    std::sort(bank.channels.begin(), bank.channels.end(),
              [&](int lhs, int rhs) {
                return std::tie(input.channels[lhs].order, lhs) <
                    std::tie(input.channels[rhs].order, rhs);
              });
    for (int order = 0; order < static_cast<int>(bank.channels.size());
         ++order) {
      if (input.channels[bank.channels[order]].order != order) {
        throw std::runtime_error(
            "Chimew bank channel order must be contiguous");
      }
    }
  }
  for (const Group& group : input.groups) {
    if (static_cast<int>(group.members.size()) != group.expected_members) {
      throw std::runtime_error("Chimew group member count does not agree");
    }
  }
  return input;
}

double raw_cost(const Group& group, const Point& endpoint_a,
                const Point& endpoint_b) {
  const Point& output = group.direction == 0 ? endpoint_a : endpoint_b;
  const Point& input = group.direction == 0 ? endpoint_b : endpoint_a;
  double cost = 0.0;
  for (const Member& member : group.members) {
    cost += manhattan(member.fanout, output);
    double fanin_distance = 0.0;
    for (const Point& fanin : member.fanins) {
      fanin_distance += manhattan(fanin, input);
    }
    cost += fanin_distance / static_cast<double>(member.fanins.size());
  }
  return cost;
}

std::int64_t ranked_cost(double raw, double scale) {
  if (!std::isfinite(raw) || raw < 0.0 ||
      raw > static_cast<double>(std::numeric_limits<std::int64_t>::max()) /
                scale) {
    throw std::runtime_error("Chimew edge cost is out of range");
  }
  return static_cast<std::int64_t>(std::llround(raw * scale));
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

  int add_edge(int from, int to, int capacity, std::int64_t cost) {
    const int index = static_cast<int>(graph_[from].size());
    Edge forward{to, static_cast<int>(graph_[to].size()), capacity, cost};
    Edge reverse{from, index, 0, -cost};
    graph_[from].push_back(forward);
    graph_[to].push_back(reverse);
    return index;
  }

  std::pair<int, std::int64_t> solve(int source, int sink, int target) {
    const int nodes = static_cast<int>(graph_.size());
    const auto infinity = std::numeric_limits<std::int64_t>::max() / 4;
    int flow = 0;
    std::int64_t total = 0;
    while (flow < target) {
      std::vector<std::int64_t> distance(nodes, infinity);
      std::vector<int> previous_node(nodes, -1);
      std::vector<int> previous_edge(nodes, -1);
      std::vector<char> queued(nodes, false);
      std::queue<int> queue;
      distance[source] = 0;
      queue.push(source);
      queued[source] = true;
      while (!queue.empty()) {
        const int node = queue.front();
        queue.pop();
        queued[node] = false;
        for (int index = 0; index < static_cast<int>(graph_[node].size());
             ++index) {
          const Edge& edge = graph_[node][index];
          if (edge.capacity <= 0 || distance[node] == infinity) {
            continue;
          }
          const std::int64_t candidate = distance[node] + edge.cost;
          if (candidate < distance[edge.to]) {
            distance[edge.to] = candidate;
            previous_node[edge.to] = node;
            previous_edge[edge.to] = index;
            if (!queued[edge.to]) {
              queued[edge.to] = true;
              queue.push(edge.to);
            }
          }
        }
      }
      if (distance[sink] == infinity) {
        break;
      }
      for (int node = sink; node != source; node = previous_node[node]) {
        if (previous_node[node] < 0) {
          throw std::runtime_error("broken Chimew augmenting path");
        }
        Edge& edge = graph_[previous_node[node]][previous_edge[node]];
        --edge.capacity;
        ++graph_[node][edge.reverse].capacity;
      }
      ++flow;
      total += distance[sink];
    }
    return {flow, total};
  }

  std::vector<std::int64_t> certificate_potentials() const {
    const int nodes = static_cast<int>(graph_.size());
    std::vector<std::int64_t> distance(nodes, 0);
    std::vector<int> path_length(nodes, 0);
    std::vector<char> queued(nodes, true);
    std::queue<int> queue;
    for (int node = 0; node < nodes; ++node) {
      queue.push(node);
    }
    while (!queue.empty()) {
      const int node = queue.front();
      queue.pop();
      queued[node] = false;
      for (const Edge& edge : graph_[node]) {
        if (edge.capacity <= 0 ||
            distance[edge.to] <= distance[node] + edge.cost) {
          continue;
        }
        distance[edge.to] = distance[node] + edge.cost;
        path_length[edge.to] = path_length[node] + 1;
        if (path_length[edge.to] >= nodes) {
          throw std::runtime_error(
              "negative residual cycle in Chimew assignment");
        }
        if (!queued[edge.to]) {
          queued[edge.to] = true;
          queue.push(edge.to);
        }
      }
    }
    for (int node = 0; node < nodes; ++node) {
      for (const Edge& edge : graph_[node]) {
        if (edge.capacity > 0 &&
            edge.cost + distance[node] - distance[edge.to] < 0) {
          throw std::runtime_error("invalid Chimew optimality certificate");
        }
      }
    }
    return distance;
  }

  const Edge& edge(int node, int index) const { return graph_[node][index]; }

 private:
  std::vector<std::vector<Edge>> graph_;
};

struct CandidateEdge {
  int right = -1;
  int left = -1;
  std::int64_t cost = 0;
};

struct AssignmentResult {
  std::vector<int> right_for_left;
  std::vector<std::int64_t> cost_for_left;
  std::int64_t total_cost = 0;
  std::vector<std::int64_t> potentials;
};

AssignmentResult assign(int right_count, const std::vector<int>& capacities,
                        int left_count,
                        const std::vector<CandidateEdge>& candidates) {
  const int source = 0;
  const int first_right = 1;
  const int first_left = first_right + right_count;
  const int sink = first_left + left_count;

  // A materialized platform commonly has exactly one legal bank for every
  // group.  Running one residual-graph traversal per group in that case is
  // mathematically redundant and turns an otherwise linear stage-1 binding
  // into a quadratic workload.  Build the same unique feasible assignment
  // directly and emit a residual-dual certificate that the independent
  // Python checker verifies in exactly the same way as the general solver.
  std::vector<int> unique_right(left_count, -1);
  std::vector<std::int64_t> unique_cost(left_count, 0);
  bool unique_candidate = true;
  for (const CandidateEdge& candidate : candidates) {
    if (candidate.left < 0 || candidate.left >= left_count ||
        candidate.right < 0 || candidate.right >= right_count ||
        unique_right[candidate.left] >= 0) {
      unique_candidate = false;
      break;
    }
    unique_right[candidate.left] = candidate.right;
    unique_cost[candidate.left] = candidate.cost;
  }
  if (unique_candidate &&
      std::find(unique_right.begin(), unique_right.end(), -1) ==
          unique_right.end()) {
    std::vector<int> used(right_count, 0);
    AssignmentResult result;
    result.right_for_left = unique_right;
    result.cost_for_left = unique_cost;
    result.potentials.assign(sink + 1, 0);
    for (int left = 0; left < left_count; ++left) {
      const int right = unique_right[left];
      if (++used[right] > capacities[right]) {
        throw std::runtime_error("no complete Chimew assignment exists");
      }
      if (unique_cost[left] >
          std::numeric_limits<std::int64_t>::max() - result.total_cost) {
        throw std::runtime_error("Chimew assignment cost is out of range");
      }
      result.total_cost += unique_cost[left];
      result.potentials[first_left + left] = unique_cost[left];
      result.potentials[sink] =
          std::max(result.potentials[sink], unique_cost[left]);
    }
    return result;
  }

  MinCostFlow flow(sink + 1);
  for (int right = 0; right < right_count; ++right) {
    flow.add_edge(source, first_right + right, capacities[right], 0);
  }
  struct Reference {
    CandidateEdge candidate;
    int edge_index = -1;
  };
  std::vector<Reference> references;
  references.reserve(candidates.size());
  for (const CandidateEdge& candidate : candidates) {
    const int edge_index = flow.add_edge(first_right + candidate.right,
                                         first_left + candidate.left, 1,
                                         candidate.cost);
    references.push_back({candidate, edge_index});
  }
  for (int left = 0; left < left_count; ++left) {
    flow.add_edge(first_left + left, sink, 1, 0);
  }
  const auto [assigned, total] = flow.solve(source, sink, left_count);
  if (assigned != left_count) {
    throw std::runtime_error("no complete Chimew assignment exists");
  }
  AssignmentResult result;
  result.right_for_left.assign(left_count, -1);
  result.cost_for_left.assign(left_count, 0);
  result.total_cost = total;
  for (const Reference& reference : references) {
    const int node = first_right + reference.candidate.right;
    if (flow.edge(node, reference.edge_index).capacity != 0) {
      continue;
    }
    const int left = reference.candidate.left;
    if (result.right_for_left[left] >= 0) {
      throw std::runtime_error("ambiguous Chimew assignment");
    }
    result.right_for_left[left] = reference.candidate.right;
    result.cost_for_left[left] = reference.candidate.cost;
  }
  if (std::find(result.right_for_left.begin(), result.right_for_left.end(), -1) !=
      result.right_for_left.end()) {
    throw std::runtime_error("incomplete Chimew assignment reconstruction");
  }
  result.potentials = flow.certificate_potentials();
  return result;
}

struct Stage2Result {
  int priority = 0;
  std::vector<int> groups;
  AssignmentResult assignment;
};

Stage2Result solve_bank(const Input& input, int bank_index,
                        const std::vector<int>& groups, int priority) {
  const BankPair& bank = input.banks[bank_index];
  int direction_counts[2] = {0, 0};
  for (int group_index : groups) {
    const Group& group = input.groups[group_index];
    if (group.kind == 0) {
      ++direction_counts[group.direction];
    }
  }
  std::vector<CandidateEdge> candidates;
  for (int right = 0; right < static_cast<int>(bank.channels.size()); ++right) {
    int required_kind = 1;
    int required_direction = -1;
    const int first_direction = priority;
    const int second_direction = 1 - priority;
    if (right < direction_counts[first_direction]) {
      required_kind = 0;
      required_direction = first_direction;
    } else if (right < direction_counts[first_direction] +
                           direction_counts[second_direction]) {
      required_kind = 0;
      required_direction = second_direction;
    }
    const Channel& channel = input.channels[bank.channels[right]];
    for (int left = 0; left < static_cast<int>(groups.size()); ++left) {
      const Group& group = input.groups[groups[left]];
      const bool eligible =
          required_kind == 0
              ? group.kind == 0 && group.direction == required_direction
              : group.kind == 1;
      if (!eligible) {
        continue;
      }
      candidates.push_back(
          {right, left,
           ranked_cost(raw_cost(group, channel.pin_a, channel.pin_b),
                       input.cost_scale)});
    }
  }
  std::vector<int> capacities(bank.channels.size(), 1);
  return {priority, groups,
          assign(static_cast<int>(bank.channels.size()), capacities,
                 static_cast<int>(groups.size()), candidates)};
}

void write_certificate(std::ofstream& output, const std::string& label,
                       const AssignmentResult& result) {
  output << "CERT " << label << " " << result.potentials.size() << "\n";
  for (int node = 0; node < static_cast<int>(result.potentials.size()); ++node) {
    output << "POT " << label << " " << node << " "
           << result.potentials[node] << "\n";
  }
}

void run(const std::string& input_path, const std::string& output_path) {
  const Input input = read_input(input_path);
  std::vector<CandidateEdge> bank_candidates;
  for (int bank = 0; bank < static_cast<int>(input.banks.size()); ++bank) {
    for (int group = 0; group < static_cast<int>(input.groups.size()); ++group) {
      if (input.banks[bank].domain != input.groups[group].domain) {
        continue;
      }
      bank_candidates.push_back(
          {bank, group,
           ranked_cost(raw_cost(input.groups[group], input.banks[bank].bank_a,
                                input.banks[bank].bank_b),
                       input.cost_scale)});
    }
  }
  std::vector<int> bank_capacities;
  for (const BankPair& bank : input.banks) {
    bank_capacities.push_back(static_cast<int>(bank.channels.size()));
  }
  const AssignmentResult stage1 =
      assign(static_cast<int>(input.banks.size()), bank_capacities,
             static_cast<int>(input.groups.size()), bank_candidates);

  std::vector<std::vector<int>> groups_by_bank(input.banks.size());
  for (int group = 0; group < static_cast<int>(input.groups.size()); ++group) {
    groups_by_bank[stage1.right_for_left[group]].push_back(group);
  }
  std::vector<std::pair<Stage2Result, Stage2Result>> alternatives;
  std::vector<int> chosen_priority(input.banks.size(), 0);
  std::int64_t stage2_total = 0;
  for (int bank = 0; bank < static_cast<int>(input.banks.size()); ++bank) {
    if (groups_by_bank[bank].empty()) {
      alternatives.push_back({Stage2Result{}, Stage2Result{}});
      continue;
    }
    const Stage2Result first = solve_bank(input, bank, groups_by_bank[bank], 0);
    const Stage2Result second = solve_bank(input, bank, groups_by_bank[bank], 1);
    alternatives.push_back({first, second});
    chosen_priority[bank] =
        second.assignment.total_cost < first.assignment.total_cost ? 1 : 0;
    stage2_total += chosen_priority[bank] == 0
                        ? first.assignment.total_cost
                        : second.assignment.total_cost;
  }

  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open Chimew bank/channel output");
  }
  output << "EMUFLOW_CHIMEW_BANK_CHANNEL_OUTPUT_V1\n";
  output << "METRIC " << input.groups.size() << " " << stage1.total_cost << " "
         << stage2_total << "\n";
  for (int group = 0; group < static_cast<int>(input.groups.size()); ++group) {
    output << "BANK_ASSIGN " << group << " " << stage1.right_for_left[group]
           << " " << stage1.cost_for_left[group] << "\n";
  }
  write_certificate(output, "STAGE1", stage1);
  for (int bank = 0; bank < static_cast<int>(input.banks.size()); ++bank) {
    if (groups_by_bank[bank].empty()) {
      continue;
    }
    output << "CHOSEN " << bank << " " << chosen_priority[bank] << "\n";
    for (int priority = 0; priority < 2; ++priority) {
      const Stage2Result& result = priority == 0 ? alternatives[bank].first
                                                 : alternatives[bank].second;
      const std::string label =
          "BANK" + std::to_string(bank) + "P" + std::to_string(priority);
      output << "ALTERNATIVE " << bank << " " << priority << " "
             << result.assignment.total_cost << "\n";
      for (int left = 0; left < static_cast<int>(result.groups.size()); ++left) {
        const int local_channel = result.assignment.right_for_left[left];
        output << "CHANNEL_ASSIGN " << bank << " " << priority << " "
               << result.groups[left] << " "
               << input.banks[bank].channels[local_channel] << " "
               << result.assignment.cost_for_left[left] << "\n";
      }
      write_certificate(output, label, result.assignment);
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_chimew_bank_channel_assigner INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_chimew_bank_channel_assigner INPUT OUTPUT\n";
    return 2;
  }
  try {
    run(argv[1], argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_chimew_bank_channel_assigner: " << error.what()
              << "\n";
    return 1;
  }
}
