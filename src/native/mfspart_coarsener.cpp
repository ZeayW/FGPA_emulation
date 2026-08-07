// SPDX-License-Identifier: Apache-2.0
//
// Independent paper-level reproduction of the affinity-coarsening core from
// MFSPart (TCAD 2026, DOI 10.1109/TCAD.2026.3656070).  The authors' companion
// repository has no software license and is neither copied nor linked here.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
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
  bool protected_radius = false;
  std::vector<long long> weights;
};

struct Net {
  double weight = 1.0;
  int source = -1;
  std::vector<int> sinks;
};

struct Graph {
  std::vector<Node> nodes;
  std::vector<Net> nets;
};

struct Input {
  int dimensions = 0;
  int stop_delta = 0;
  int max_levels = 0;
  std::uint64_t seed = 0;
  std::vector<long long> coarse_bounds;
  bool margin_mode = false;
  int parts = 0;
  int fixed_radius = 0;
  int fixed_margin = 0;
  std::vector<std::vector<int>> part_distances;
  Graph graph;
};

struct Merge {
  int coarse = -1;
  int left = -1;
  int right = -1;
  double affinity = 0.0;
};

struct LevelResult {
  Graph graph;
  std::vector<int> fine_to_coarse;
  std::vector<Merge> merges;
  std::vector<std::pair<int, std::vector<int>>> fixed_merges;
  long long rejected_protected = 0;
  long long rejected_margin = 0;
  long long rejected_bound = 0;
  long long rejected_fixed = 0;
  long long margin_repair_rounds = 0;
  long long margin_distance_searches = 0;
};

using NetKey = std::pair<int, std::vector<int>>;

std::uint64_t splitmix64(std::uint64_t value) {
  value += 0x9e3779b97f4a7c15ULL;
  value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
  value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
  return value ^ (value >> 31U);
}

std::uint64_t tie_key(std::uint64_t seed, int level, int left, int right) {
  std::uint64_t value = seed;
  value ^= splitmix64(static_cast<std::uint64_t>(level + 1));
  value ^= splitmix64(static_cast<std::uint64_t>(left + 1) << 32U |
                      static_cast<std::uint32_t>(right + 1));
  return splitmix64(value);
}

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string magic;
  std::getline(stream, magic);
  if (magic != "EMUFLOW_MFSPART_COARSENER_INPUT_V2") {
    throw std::runtime_error("unsupported input header");
  }

  Input input;
  int node_count = -1;
  int net_count = -1;
  bool saw_param = false;
  bool saw_mode = false;
  std::vector<bool> saw_bounds;
  std::vector<bool> saw_nodes;
  std::vector<bool> saw_nets;
  std::vector<std::vector<bool>> saw_distances;
  std::string kind;
  while (stream >> kind) {
    if (kind == "PARAM") {
      if (saw_param) {
        throw std::runtime_error("duplicate PARAM record");
      }
      stream >> node_count >> input.dimensions >> net_count >> input.stop_delta >>
          input.max_levels >> input.seed;
      if (node_count <= 0 || input.dimensions <= 0 || net_count < 0 ||
          input.stop_delta < 0 || input.max_levels <= 0) {
        throw std::runtime_error("invalid PARAM record");
      }
      input.coarse_bounds.assign(input.dimensions, 0);
      input.graph.nodes.assign(node_count, Node{});
      input.graph.nets.assign(net_count, Net{});
      saw_bounds.assign(input.dimensions, false);
      saw_nodes.assign(node_count, false);
      saw_nets.assign(net_count, false);
      saw_param = true;
    } else if (kind == "MODE") {
      if (!saw_param || saw_mode) {
        throw std::runtime_error("invalid or duplicate MODE record");
      }
      std::string mode;
      stream >> mode;
      if (mode == "A") {
        input.margin_mode = false;
      } else if (mode == "M") {
        input.margin_mode = true;
        stream >> input.parts >> input.fixed_radius >> input.fixed_margin;
        if (input.parts <= 0 || input.fixed_radius < 0 ||
            input.fixed_margin < 0) {
          throw std::runtime_error("invalid margin MODE record");
        }
        input.part_distances.assign(input.parts,
                                    std::vector<int>(input.parts, -1));
        saw_distances.assign(input.parts,
                             std::vector<bool>(input.parts, false));
      } else {
        throw std::runtime_error("unsupported coarsening MODE");
      }
      saw_mode = true;
    } else if (kind == "DIST") {
      if (!saw_mode || !input.margin_mode) {
        throw std::runtime_error("DIST record requires margin MODE");
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
      input.part_distances[source][target] = distance;
      saw_distances[source][target] = true;
    } else if (kind == "BOUND") {
      if (!saw_mode) {
        throw std::runtime_error("BOUND record precedes MODE");
      }
      int dimension = -1;
      long long bound = -1;
      stream >> dimension >> bound;
      if (dimension < 0 || dimension >= input.dimensions || bound <= 0) {
        throw std::runtime_error("invalid BOUND record");
      }
      if (saw_bounds[dimension]) {
        throw std::runtime_error("duplicate BOUND record");
      }
      input.coarse_bounds[dimension] = bound;
      saw_bounds[dimension] = true;
    } else if (kind == "NODE") {
      if (!saw_mode) {
        throw std::runtime_error("NODE record precedes MODE");
      }
      int index = -1;
      Node node;
      stream >> index >> node.fixed_part;
      node.weights.resize(input.dimensions);
      for (long long& weight : node.weights) {
        stream >> weight;
      }
      if (index < 0 || index >= node_count || node.fixed_part < -1 ||
          (input.margin_mode && node.fixed_part >= input.parts) ||
          node.weights.empty() || node.weights.front() <= 0 ||
          std::any_of(node.weights.begin(), node.weights.end(),
                      [](long long weight) { return weight < 0; })) {
        throw std::runtime_error("invalid NODE record");
      }
      if (saw_nodes[index]) {
        throw std::runtime_error("duplicate NODE record");
      }
      input.graph.nodes[index] = std::move(node);
      saw_nodes[index] = true;
    } else if (kind == "NET") {
      if (!saw_mode) {
        throw std::runtime_error("NET record precedes MODE");
      }
      int index = -1;
      int sink_count = -1;
      Net net;
      stream >> index >> net.weight >> net.source >> sink_count;
      if (index < 0 || index >= net_count || !std::isfinite(net.weight) ||
          net.weight <= 0.0 || net.source < 0 || net.source >= node_count ||
          sink_count <= 0) {
        throw std::runtime_error("invalid NET record");
      }
      if (saw_nets[index]) {
        throw std::runtime_error("duplicate NET record");
      }
      net.sinks.resize(sink_count);
      std::set<int> unique;
      for (int& sink : net.sinks) {
        stream >> sink;
        if (sink < 0 || sink >= node_count || sink == net.source ||
            !unique.insert(sink).second) {
          throw std::runtime_error("invalid NET sink");
        }
      }
      std::sort(net.sinks.begin(), net.sinks.end());
      input.graph.nets[index] = std::move(net);
      saw_nets[index] = true;
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!stream) {
      throw std::runtime_error("malformed input record");
    }
  }
  if (!saw_param || !saw_mode ||
      std::any_of(saw_bounds.begin(), saw_bounds.end(),
                  [](bool seen) { return !seen; }) ||
      std::any_of(saw_nodes.begin(), saw_nodes.end(),
                  [](bool seen) { return !seen; }) ||
      std::any_of(saw_nets.begin(), saw_nets.end(),
                  [](bool seen) { return !seen; })) {
    throw std::runtime_error("incomplete input");
  }
  if (input.margin_mode) {
    for (int part = 0; part < input.parts; ++part) {
      if (std::any_of(saw_distances[part].begin(), saw_distances[part].end(),
                      [](bool seen) { return !seen; }) ||
          input.part_distances[part][part] != 0) {
        throw std::runtime_error("incomplete margin topology");
      }
      for (int other = 0; other < input.parts; ++other) {
        if (input.part_distances[part][other] !=
            input.part_distances[other][part]) {
          throw std::runtime_error("margin topology must be symmetric");
        }
      }
    }
    std::vector<std::vector<int>> adjacency(input.graph.nodes.size());
    for (const Net& net : input.graph.nets) {
      for (const int sink : net.sinks) {
        adjacency[net.source].push_back(sink);
        adjacency[sink].push_back(net.source);
      }
    }
    std::vector<int> distance(input.graph.nodes.size(), -1);
    std::queue<int> queue;
    for (int node = 0; node < static_cast<int>(input.graph.nodes.size());
         ++node) {
      if (input.graph.nodes[node].fixed_part >= 0) {
        distance[node] = 0;
        queue.push(node);
      }
    }
    while (!queue.empty()) {
      const int node = queue.front();
      queue.pop();
      if (distance[node] >= input.fixed_radius) {
        continue;
      }
      for (const int neighbor : adjacency[node]) {
        if (distance[neighbor] < 0) {
          distance[neighbor] = distance[node] + 1;
          queue.push(neighbor);
        }
      }
    }
    for (int node = 0; node < static_cast<int>(input.graph.nodes.size());
         ++node) {
      input.graph.nodes[node].protected_radius =
          distance[node] >= 0 && distance[node] <= input.fixed_radius;
    }
  }
  return input;
}

void rebuild_nets(const Graph& fine, LevelResult& result) {
  std::map<NetKey, double> aggregated;
  for (const Net& net : fine.nets) {
    const int source = result.fine_to_coarse[net.source];
    std::set<int> sinks;
    for (const int sink : net.sinks) {
      const int mapped = result.fine_to_coarse[sink];
      if (mapped != source) {
        sinks.insert(mapped);
      }
    }
    if (!sinks.empty()) {
      aggregated[{source, std::vector<int>(sinks.begin(), sinks.end())}] +=
          net.weight;
    }
  }
  for (const auto& [key, weight] : aggregated) {
    result.graph.nets.push_back({weight, key.first, key.second});
  }
}

int append_cluster(const Graph& fine, const std::vector<int>& members,
                   LevelResult& result) {
  Node coarse = fine.nodes[members.front()];
  for (std::size_t member_index = 1; member_index < members.size();
       ++member_index) {
    const Node& other = fine.nodes[members[member_index]];
    for (std::size_t dimension = 0; dimension < coarse.weights.size();
         ++dimension) {
      coarse.weights[dimension] += other.weights[dimension];
    }
    if (coarse.fixed_part < 0) {
      coarse.fixed_part = other.fixed_part;
    }
    coarse.protected_radius =
        coarse.protected_radius || other.protected_radius;
  }
  const int coarse_index = static_cast<int>(result.graph.nodes.size());
  result.graph.nodes.push_back(std::move(coarse));
  for (const int member : members) {
    result.fine_to_coarse[member] = coarse_index;
  }
  return coarse_index;
}

LevelResult merge_same_fixed_parts(const Graph& fine) {
  LevelResult result;
  result.fine_to_coarse.assign(fine.nodes.size(), -1);
  std::map<int, std::vector<int>> members_by_part;
  for (int node = 0; node < static_cast<int>(fine.nodes.size()); ++node) {
    if (fine.nodes[node].fixed_part >= 0) {
      members_by_part[fine.nodes[node].fixed_part].push_back(node);
    }
  }
  for (int node = 0; node < static_cast<int>(fine.nodes.size()); ++node) {
    if (result.fine_to_coarse[node] >= 0) {
      continue;
    }
    const int fixed_part = fine.nodes[node].fixed_part;
    if (fixed_part >= 0 && members_by_part[fixed_part].size() > 1) {
      const auto& members = members_by_part[fixed_part];
      const int coarse = append_cluster(fine, members, result);
      result.fixed_merges.push_back({coarse, members});
    } else {
      append_cluster(fine, {node}, result);
    }
  }
  rebuild_nets(fine, result);
  return result;
}

struct AnchorDistances {
  std::vector<int> anchors;
  std::vector<std::vector<int>> distances;
};

AnchorDistances build_anchor_distances(const Graph& graph) {
  std::vector<std::vector<int>> adjacency(graph.nodes.size());
  for (const Net& net : graph.nets) {
    for (const int sink : net.sinks) {
      adjacency[net.source].push_back(sink);
      adjacency[sink].push_back(net.source);
    }
  }
  AnchorDistances result;
  for (int node = 0; node < static_cast<int>(graph.nodes.size()); ++node) {
    if (graph.nodes[node].fixed_part < 0) {
      continue;
    }
    result.anchors.push_back(node);
    std::vector<int> distances(graph.nodes.size(), -1);
    std::queue<int> queue;
    distances[node] = 0;
    queue.push(node);
    while (!queue.empty()) {
      const int current = queue.front();
      queue.pop();
      for (const int neighbor : adjacency[current]) {
        if (distances[neighbor] < 0) {
          distances[neighbor] = distances[current] + 1;
          queue.push(neighbor);
        }
      }
    }
    result.distances.push_back(std::move(distances));
  }
  return result;
}

bool margin_allows(const Graph& graph, const Input& input,
                   const AnchorDistances& anchors, int left, int right) {
  if (!input.margin_mode) {
    return true;
  }
  const int infinity = std::numeric_limits<int>::max() / 4;
  for (int first = 0; first < static_cast<int>(anchors.anchors.size());
       ++first) {
    for (int second = first + 1;
         second < static_cast<int>(anchors.anchors.size()); ++second) {
      const int first_anchor = anchors.anchors[first];
      const int second_anchor = anchors.anchors[second];
      const int first_part = graph.nodes[first_anchor].fixed_part;
      const int second_part = graph.nodes[second_anchor].fixed_part;
      if (first_part == second_part) {
        continue;
      }
      const int required = input.part_distances[first_part][second_part] +
                           input.fixed_margin;
      const int baseline = anchors.distances[first][second_anchor];
      const int minimum_allowed =
          baseline < 0 ? required : std::min(baseline, required);
      int contracted = baseline;
      const auto combine = [&](int left_distance, int right_distance) {
        return left_distance < 0 || right_distance < 0
                   ? infinity
                   : left_distance + right_distance;
      };
      contracted = std::min(
          contracted < 0 ? infinity : contracted,
          combine(anchors.distances[first][left],
                  anchors.distances[second][right]));
      contracted = std::min(
          contracted,
          combine(anchors.distances[first][right],
                  anchors.distances[second][left]));
      if (contracted < minimum_allowed) {
        return false;
      }
    }
  }
  return true;
}

LevelResult coarsen_once(const Graph& fine, const Input& input,
                         std::uint64_t seed, int level) {
  std::map<std::pair<int, int>, double> pair_weight;
  for (const Net& net : fine.nets) {
    for (const int sink : net.sinks) {
      const int left = std::min(net.source, sink);
      const int right = std::max(net.source, sink);
      pair_weight[{left, right}] += net.weight;
    }
  }

  struct Candidate {
    int left = -1;
    int right = -1;
    double affinity = 0.0;
    std::uint64_t tie = 0;
  };
  std::vector<Candidate> candidates;
  LevelResult result;
  result.fine_to_coarse.assign(fine.nodes.size(), -1);
  const AnchorDistances anchor_distances = build_anchor_distances(fine);
  for (const auto& [pair, weight] : pair_weight) {
    const Node& left = fine.nodes[pair.first];
    const Node& right = fine.nodes[pair.second];
    if (input.margin_mode &&
        (left.protected_radius || right.protected_radius)) {
      ++result.rejected_protected;
      continue;
    }
    if (left.fixed_part >= 0 && right.fixed_part >= 0 &&
        left.fixed_part != right.fixed_part) {
      ++result.rejected_fixed;
      continue;
    }
    bool fits = true;
    for (std::size_t dimension = 0; dimension < input.coarse_bounds.size();
         ++dimension) {
      if (left.weights[dimension] + right.weights[dimension] >
          input.coarse_bounds[dimension]) {
        fits = false;
      }
    }
    if (!fits) {
      ++result.rejected_bound;
      continue;
    }
    if (!margin_allows(fine, input, anchor_distances, pair.first,
                       pair.second)) {
      ++result.rejected_margin;
      continue;
    }
    const long double denominator =
        static_cast<long double>(left.weights.front()) *
        static_cast<long double>(right.weights.front());
    candidates.push_back(
        {pair.first, pair.second, static_cast<double>(weight / denominator),
         tie_key(seed, level, pair.first, pair.second)});
  }
  std::sort(candidates.begin(), candidates.end(),
            [](const Candidate& left, const Candidate& right) {
              if (left.affinity != right.affinity) {
                return left.affinity > right.affinity;
              }
              if (left.tie != right.tie) {
                return left.tie < right.tie;
              }
              return std::tie(left.left, left.right) <
                     std::tie(right.left, right.right);
            });

  std::vector<bool> matched(fine.nodes.size(), false);
  std::vector<Candidate> selected;
  for (const Candidate& candidate : candidates) {
    if (matched[candidate.left] || matched[candidate.right]) {
      continue;
    }
    matched[candidate.left] = true;
    matched[candidate.right] = true;
    selected.push_back(candidate);
  }

  std::vector<bool> active(selected.size(), true);
  bool has_distinct_anchors = false;
  for (int first = 0;
       first < static_cast<int>(anchor_distances.anchors.size()); ++first) {
    for (int second = first + 1;
         second < static_cast<int>(anchor_distances.anchors.size()); ++second) {
      if (fine.nodes[anchor_distances.anchors[first]].fixed_part !=
          fine.nodes[anchor_distances.anchors[second]].fixed_part) {
        has_distinct_anchors = true;
      }
    }
  }
  while (input.margin_mode && has_distinct_anchors) {
    struct Arc {
      int target = -1;
      int cost = 1;
      int merge = -1;
    };
    std::vector<std::vector<Arc>> adjacency(fine.nodes.size());
    for (const Net& net : fine.nets) {
      for (const int sink : net.sinks) {
        adjacency[net.source].push_back({sink, 1, -1});
        adjacency[sink].push_back({net.source, 1, -1});
      }
    }
    for (int merge = 0; merge < static_cast<int>(selected.size()); ++merge) {
      if (active[merge]) {
        adjacency[selected[merge].left].push_back(
            {selected[merge].right, 0, merge});
        adjacency[selected[merge].right].push_back(
            {selected[merge].left, 0, merge});
      }
    }
    std::set<int> remove;
    for (int first = 0;
         first < static_cast<int>(anchor_distances.anchors.size()); ++first) {
      const int source = anchor_distances.anchors[first];
      bool has_later_distinct = false;
      for (int second = first + 1;
           second < static_cast<int>(anchor_distances.anchors.size()); ++second) {
        has_later_distinct =
            has_later_distinct ||
            fine.nodes[source].fixed_part !=
                fine.nodes[anchor_distances.anchors[second]].fixed_part;
      }
      if (!has_later_distinct) {
        continue;
      }
      const int infinity = std::numeric_limits<int>::max() / 4;
      std::vector<int> distance(fine.nodes.size(), infinity);
      std::vector<int> parent(fine.nodes.size(), -1);
      std::vector<int> parent_merge(fine.nodes.size(), -1);
      std::deque<int> queue;
      distance[source] = 0;
      queue.push_front(source);
      while (!queue.empty()) {
        const int node = queue.front();
        queue.pop_front();
        for (const Arc& arc : adjacency[node]) {
          const int candidate_distance = distance[node] + arc.cost;
          if (candidate_distance < distance[arc.target] ||
              (candidate_distance == distance[arc.target] &&
               std::tie(node, arc.merge) <
                   std::tie(parent[arc.target], parent_merge[arc.target]))) {
            distance[arc.target] = candidate_distance;
            parent[arc.target] = node;
            parent_merge[arc.target] = arc.merge;
            if (arc.cost == 0) {
              queue.push_front(arc.target);
            } else {
              queue.push_back(arc.target);
            }
          }
        }
      }
      ++result.margin_distance_searches;
      for (int second = first + 1;
           second < static_cast<int>(anchor_distances.anchors.size()); ++second) {
        const int target = anchor_distances.anchors[second];
        const int first_part = fine.nodes[source].fixed_part;
        const int second_part = fine.nodes[target].fixed_part;
        if (first_part == second_part || distance[target] == infinity) {
          continue;
        }
        const int required = input.part_distances[first_part][second_part] +
                             input.fixed_margin;
        const int baseline = anchor_distances.distances[first][target];
        const int minimum_allowed =
            baseline < 0 ? required : std::min(baseline, required);
        const int deficit = minimum_allowed - distance[target];
        if (deficit <= 0) {
          continue;
        }
        std::vector<int> path_merges;
        int node = target;
        while (node != source && parent[node] >= 0) {
          if (parent_merge[node] >= 0) {
            path_merges.push_back(parent_merge[node]);
          }
          node = parent[node];
        }
        std::sort(path_merges.begin(), path_merges.end(),
                  [&](int left, int right) {
                    if (selected[left].affinity != selected[right].affinity) {
                      return selected[left].affinity < selected[right].affinity;
                    }
                    if (selected[left].tie != selected[right].tie) {
                      return selected[left].tie > selected[right].tie;
                    }
                    return left > right;
                  });
        path_merges.erase(
            std::unique(path_merges.begin(), path_merges.end()),
            path_merges.end());
        if (static_cast<int>(path_merges.size()) < deficit) {
          throw std::runtime_error("margin repair cannot identify enough merges");
        }
        for (int index = 0; index < deficit; ++index) {
          remove.insert(path_merges[index]);
        }
      }
    }
    if (remove.empty()) {
      break;
    }
    for (const int merge : remove) {
      active[merge] = false;
    }
    result.rejected_margin += remove.size();
    ++result.margin_repair_rounds;
  }

  for (int index = 0; index < static_cast<int>(selected.size()); ++index) {
    if (!active[index]) {
      continue;
    }
    const Candidate& candidate = selected[index];
    const int coarse = append_cluster(
        fine, {candidate.left, candidate.right}, result);
    result.merges.push_back(
        {coarse, candidate.left, candidate.right, candidate.affinity});
  }
  for (int node = 0; node < static_cast<int>(fine.nodes.size()); ++node) {
    if (result.fine_to_coarse[node] < 0) {
      append_cluster(fine, {node}, result);
    }
  }
  rebuild_nets(fine, result);
  return result;
}

void write_graph(std::ostream& stream, int level, const Graph& graph) {
  stream << "LEVEL " << level << ' ' << graph.nodes.size() << ' '
         << graph.nets.size() << '\n';
  for (int index = 0; index < static_cast<int>(graph.nodes.size()); ++index) {
    stream << "NODE " << level << ' ' << index << ' '
           << graph.nodes[index].fixed_part << ' '
           << (graph.nodes[index].protected_radius ? 1 : 0);
    for (const long long weight : graph.nodes[index].weights) {
      stream << ' ' << weight;
    }
    stream << '\n';
  }
  for (int index = 0; index < static_cast<int>(graph.nets.size()); ++index) {
    const Net& net = graph.nets[index];
    stream << "NET " << level << ' ' << index << ' ' << std::setprecision(17)
           << net.weight << ' ' << net.source << ' ' << net.sinks.size();
    for (const int sink : net.sinks) {
      stream << ' ' << sink;
    }
    stream << '\n';
  }
}

void run(const Input& input, const std::string& output_path) {
  std::vector<Graph> graphs = {input.graph};
  std::vector<LevelResult> levels;
  if (input.margin_mode) {
    std::map<int, int> fixed_counts;
    for (const Node& node : graphs.back().nodes) {
      if (node.fixed_part >= 0) {
        ++fixed_counts[node.fixed_part];
      }
    }
    if (std::any_of(fixed_counts.begin(), fixed_counts.end(),
                    [](const auto& item) { return item.second > 1; })) {
      LevelResult fixed = merge_same_fixed_parts(graphs.back());
      graphs.push_back(fixed.graph);
      levels.push_back(std::move(fixed));
    }
  }
  while (static_cast<int>(levels.size()) < input.max_levels) {
    const int level = static_cast<int>(levels.size());
    LevelResult next = coarsen_once(graphs.back(), input, input.seed, level);
    const int reduction = static_cast<int>(graphs.back().nodes.size()) -
                          static_cast<int>(next.graph.nodes.size());
    if (reduction <= input.stop_delta) {
      break;
    }
    graphs.push_back(next.graph);
    levels.push_back(std::move(next));
    if (graphs.back().nodes.size() <= 1) {
      break;
    }
  }

  std::ofstream stream(output_path);
  if (!stream) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  stream << "EMUFLOW_MFSPART_COARSENER_OUTPUT_V2\n";
  stream << "PARAM " << graphs.size() << ' ' << input.dimensions << ' '
         << input.seed << '\n';
  if (input.margin_mode) {
    stream << "MODE M " << input.parts << ' ' << input.fixed_radius << ' '
           << input.fixed_margin << '\n';
  } else {
    stream << "MODE A\n";
  }
  write_graph(stream, 0, graphs.front());
  for (int level = 0; level < static_cast<int>(levels.size()); ++level) {
    for (int fine = 0;
         fine < static_cast<int>(levels[level].fine_to_coarse.size()); ++fine) {
      stream << "MAP " << level << ' ' << fine << ' '
             << levels[level].fine_to_coarse[fine] << '\n';
    }
    for (const Merge& merge : levels[level].merges) {
      stream << "MERGE " << level << ' ' << merge.coarse << ' ' << merge.left
             << ' ' << merge.right << ' ' << std::setprecision(17)
             << merge.affinity << '\n';
    }
    for (const auto& [coarse, members] : levels[level].fixed_merges) {
      stream << "FIXED_MERGE " << level << ' ' << coarse << ' '
             << members.size();
      for (const int member : members) {
        stream << ' ' << member;
      }
      stream << '\n';
    }
    write_graph(stream, level + 1, graphs[level + 1]);
  }
  stream << "METRIC levels " << levels.size() << '\n';
  stream << "METRIC original_nodes " << graphs.front().nodes.size() << '\n';
  stream << "METRIC coarsest_nodes " << graphs.back().nodes.size() << '\n';
  long long rejected_protected = 0;
  long long rejected_margin = 0;
  long long rejected_bound = 0;
  long long rejected_fixed = 0;
  long long margin_repair_rounds = 0;
  long long margin_distance_searches = 0;
  for (const LevelResult& level : levels) {
    rejected_protected += level.rejected_protected;
    rejected_margin += level.rejected_margin;
    rejected_bound += level.rejected_bound;
    rejected_fixed += level.rejected_fixed;
    margin_repair_rounds += level.margin_repair_rounds;
    margin_distance_searches += level.margin_distance_searches;
  }
  stream << "METRIC rejected_protected " << rejected_protected << '\n';
  stream << "METRIC rejected_margin " << rejected_margin << '\n';
  stream << "METRIC rejected_bound " << rejected_bound << '\n';
  stream << "METRIC rejected_fixed " << rejected_fixed << '\n';
  stream << "METRIC margin_repair_rounds " << margin_repair_rounds << '\n';
  stream << "METRIC margin_distance_searches "
         << margin_distance_searches << '\n';
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_mfspart_coarsener INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_mfspart_coarsener INPUT OUTPUT\n";
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
