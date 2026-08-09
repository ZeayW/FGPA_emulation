// SPDX-License-Identifier: Apache-2.0
//
// Placement-aware TDM signal grouping and virtual pin assignment.
// This is EmuFlow's region-signature engineering baseline: group signals with
// compatible TDM ratios while minimizing the OR-popcount of normalized-region
// signatures, refine groups using placement proximity, then solve group-to-pin
// assignment as an exact minimum-cost bipartite flow.  Normalized placement
// regions are not physical SLR/SLL crossings, so this provider is intentionally
// distinct from the planned faithful Chimew reproduction.

#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {

struct Domain {
  int lanes = 0;
};

struct Signal {
  int domain = -1;
  int ratio = 0;
  int slot = -1;
  unsigned long long crossing = 0;
  double source_y = 0.0;
  double sink_y = 0.0;
};

struct Input {
  int refinement_iterations = 100;
  double crossing_weight = 1.0;
  double position_weight = 1.0;
  std::vector<Domain> domains;
  std::vector<Signal> signals;
};

struct Group {
  int domain = -1;
  int ratio = 0;
  unsigned long long crossing = 0;
  std::vector<int> signals;
  std::set<int> slots;
  std::array<int, 64> bit_counts{};
  double sum_y = 0.0;
  double sum_y2 = 0.0;
};

int popcount(unsigned long long value) {
  return __builtin_popcountll(value);
}

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string line;
  std::getline(stream, line);
  if (line != "EMUFLOW_PIN_PLANNER_INPUT_V1") {
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
    if (kind == "PARAM") {
      record >> input.refinement_iterations >> input.crossing_weight >>
          input.position_weight;
    } else if (kind == "DOMAIN") {
      int index = -1;
      Domain domain;
      record >> index >> domain.lanes;
      if (index != static_cast<int>(input.domains.size())) {
        throw std::runtime_error("DOMAIN indices must be contiguous");
      }
      input.domains.push_back(domain);
    } else if (kind == "SIGNAL") {
      int index = -1;
      Signal signal;
      record >> index >> signal.domain >> signal.ratio >> signal.slot >>
          signal.crossing >> signal.source_y >> signal.sink_y;
      if (index != static_cast<int>(input.signals.size())) {
        throw std::runtime_error("SIGNAL indices must be contiguous");
      }
      input.signals.push_back(signal);
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!record) {
      throw std::runtime_error("malformed input record: " + line);
    }
  }
  if (input.domains.empty() || input.signals.empty() ||
      input.refinement_iterations < 0 || input.crossing_weight < 0.0 ||
      input.position_weight < 0.0) {
    throw std::runtime_error("incomplete pin-planner input");
  }
  for (const Domain& domain : input.domains) {
    if (domain.lanes <= 0) {
      throw std::runtime_error("domain lane count must be positive");
    }
  }
  for (const Signal& signal : input.signals) {
    if (signal.domain < 0 ||
        signal.domain >= static_cast<int>(input.domains.size()) ||
        signal.ratio <= 0 || signal.slot < 0 ||
        !std::isfinite(signal.source_y) ||
        !std::isfinite(signal.sink_y)) {
      throw std::runtime_error("invalid signal");
    }
  }
  return input;
}

double group_mean_y(
    const Group& group, const std::vector<Signal>& signals) {
  double sum = 0.0;
  for (int signal : group.signals) {
    sum += 0.5 * (signals[signal].source_y + signals[signal].sink_y);
  }
  return sum / static_cast<double>(group.signals.size());
}

double group_cost(
    const Group& group, const std::vector<Signal>& signals,
    double crossing_weight, double position_weight) {
  static_cast<void>(signals);
  if (group.signals.empty()) {
    return 0.0;
  }
  const double values = 2.0 * group.signals.size();
  const double spread =
      std::max(0.0, group.sum_y2 - group.sum_y * group.sum_y / values);
  return crossing_weight * popcount(group.crossing) +
      position_weight * spread;
}

double signal_sum_y(const Signal& signal) {
  return signal.source_y + signal.sink_y;
}

double signal_sum_y2(const Signal& signal) {
  return signal.source_y * signal.source_y +
      signal.sink_y * signal.sink_y;
}

unsigned long long crossing_after(
    const Group& group, unsigned long long remove,
    unsigned long long add) {
  unsigned long long result = 0;
  for (int bit = 0; bit < 64; ++bit) {
    const unsigned long long mask = 1ULL << bit;
    int count = group.bit_counts[bit];
    count -= (remove & mask) != 0;
    count += (add & mask) != 0;
    if (count > 0) {
      result |= mask;
    }
  }
  return result;
}

double cost_if_added(
    const Group& group, const Signal& signal, double crossing_weight,
    double position_weight) {
  const double sum = group.sum_y + signal_sum_y(signal);
  const double sum2 = group.sum_y2 + signal_sum_y2(signal);
  const double values = 2.0 * (group.signals.size() + 1);
  const double spread = std::max(0.0, sum2 - sum * sum / values);
  return crossing_weight * popcount(group.crossing | signal.crossing) +
      position_weight * spread;
}

double cost_after_swap(
    const Group& group, const Signal& remove, const Signal& add,
    double crossing_weight, double position_weight) {
  const double sum =
      group.sum_y - signal_sum_y(remove) + signal_sum_y(add);
  const double sum2 =
      group.sum_y2 - signal_sum_y2(remove) + signal_sum_y2(add);
  const double values = 2.0 * group.signals.size();
  const double spread = std::max(0.0, sum2 - sum * sum / values);
  return crossing_weight *
          popcount(crossing_after(group, remove.crossing, add.crossing)) +
      position_weight * spread;
}

std::vector<Group> initial_grouping(const Input& input, int domain) {
  std::map<int, std::map<int, std::vector<int>>> by_ratio_and_slot;
  for (int index = 0; index < static_cast<int>(input.signals.size()); ++index) {
    if (input.signals[index].domain == domain) {
      const Signal& signal = input.signals[index];
      by_ratio_and_slot[signal.ratio][signal.slot].push_back(index);
    }
  }

  std::vector<Group> groups;
  for (auto& [ratio, by_slot] : by_ratio_and_slot) {
    int signal_count = 0;
    int maximum_slot_multiplicity = 0;
    for (auto& [slot, bucket] : by_slot) {
      static_cast<void>(slot);
      signal_count += static_cast<int>(bucket.size());
      maximum_slot_multiplicity =
          std::max(maximum_slot_multiplicity,
                   static_cast<int>(bucket.size()));
      std::sort(bucket.begin(), bucket.end(), [&](int lhs, int rhs) {
        const Signal& a = input.signals[lhs];
        const Signal& b = input.signals[rhs];
        return std::make_tuple(-popcount(a.crossing),
                               0.5 * (a.source_y + a.sink_y), lhs) <
            std::make_tuple(-popcount(b.crossing),
                            0.5 * (b.source_y + b.sink_y), rhs);
      });
    }
    const int group_count =
        std::max((signal_count + ratio - 1) / ratio,
                 maximum_slot_multiplicity);
    if (static_cast<int>(groups.size()) + group_count >
        input.domains[domain].lanes) {
      throw std::runtime_error(
          "minimum legal TDM grouping exceeds physical lane capacity");
    }

    const int first_group = static_cast<int>(groups.size());
    for (int index = 0; index < group_count; ++index) {
      Group group;
      group.domain = domain;
      group.ratio = ratio;
      groups.push_back(std::move(group));
    }

    std::vector<std::pair<int, std::vector<int>*>> slot_order;
    for (auto& [slot, bucket] : by_slot) {
      slot_order.emplace_back(slot, &bucket);
    }
    std::sort(slot_order.begin(), slot_order.end(),
              [](const auto& lhs, const auto& rhs) {
                return std::make_pair(
                           -static_cast<int>(lhs.second->size()), lhs.first) <
                    std::make_pair(
                           -static_cast<int>(rhs.second->size()), rhs.first);
              });

    // This is a balanced capacitated coloring of the slot-conflict graph.
    // Signals with the same slot form a clique and therefore go to distinct
    // groups. Choosing the least-loaded legal group keeps all group sizes
    // within one of each other, so the lower-bound group count above is
    // sufficient whenever the instance is feasible.
    for (const auto& [slot, bucket_ptr] : slot_order) {
      static_cast<void>(slot);
      std::set<int> used_for_slot;
      for (int signal_index : *bucket_ptr) {
        const Signal& signal = input.signals[signal_index];
        int best = -1;
        std::tuple<int, double, int> best_key{
            std::numeric_limits<int>::max(),
            std::numeric_limits<double>::infinity(), -1};
        for (int index = first_group;
             index < first_group + group_count; ++index) {
          if (used_for_slot.count(index) ||
              static_cast<int>(groups[index].signals.size()) >= ratio) {
            continue;
          }
          const double delta =
              cost_if_added(groups[index], signal, input.crossing_weight,
                            input.position_weight) -
              group_cost(groups[index], input.signals,
                         input.crossing_weight, input.position_weight);
          const auto key = std::make_tuple(
              static_cast<int>(groups[index].signals.size()), delta, index);
          if (key < best_key) {
            best = index;
            best_key = key;
          }
        }
        if (best < 0) {
          throw std::runtime_error(
              "balanced TDM grouping could not construct a legal solution");
        }
        used_for_slot.insert(best);
        groups[best].signals.push_back(signal_index);
        groups[best].slots.insert(signal.slot);
        groups[best].crossing |= signal.crossing;
        groups[best].sum_y += signal_sum_y(signal);
        groups[best].sum_y2 += signal_sum_y2(signal);
        for (int bit = 0; bit < 64; ++bit) {
          groups[best].bit_counts[bit] +=
              (signal.crossing & (1ULL << bit)) != 0;
        }
      }
    }
    for (int index = first_group;
         index < first_group + group_count; ++index) {
      if (groups[index].signals.empty()) {
        throw std::runtime_error("TDM grouping produced an empty group");
      }
    }
  }
  return groups;
}

void rebuild(Group& group, const std::vector<Signal>& signals) {
  group.slots.clear();
  group.crossing = 0;
  group.bit_counts.fill(0);
  group.sum_y = 0.0;
  group.sum_y2 = 0.0;
  std::sort(group.signals.begin(), group.signals.end());
  for (int signal : group.signals) {
    group.slots.insert(signals[signal].slot);
    group.crossing |= signals[signal].crossing;
    group.sum_y += signal_sum_y(signals[signal]);
    group.sum_y2 += signal_sum_y2(signals[signal]);
    for (int bit = 0; bit < 64; ++bit) {
      group.bit_counts[bit] +=
          (signals[signal].crossing & (1ULL << bit)) != 0;
    }
  }
}

void refine_groups(const Input& input, std::vector<Group>& groups) {
  for (int iteration = 0; iteration < input.refinement_iterations; ++iteration) {
    std::vector<std::vector<int>> candidates(groups.size());
    for (int group = 0; group < static_cast<int>(groups.size()); ++group) {
      const double mean = group_mean_y(groups[group], input.signals);
      candidates[group].resize(groups[group].signals.size());
      std::iota(candidates[group].begin(), candidates[group].end(), 0);
      std::sort(
          candidates[group].begin(), candidates[group].end(),
          [&](int lhs, int rhs) {
            const Signal& a =
                input.signals[groups[group].signals[lhs]];
            const Signal& b =
                input.signals[groups[group].signals[rhs]];
            const double da =
                std::abs(0.5 * (a.source_y + a.sink_y) - mean);
            const double db =
                std::abs(0.5 * (b.source_y + b.sink_y) - mean);
            return std::make_pair(-da, groups[group].signals[lhs]) <
                std::make_pair(-db, groups[group].signals[rhs]);
          });
      if (candidates[group].size() > 16) {
        candidates[group].resize(16);
      }
    }
    double best_delta = -1.0e-12;
    int best_a = -1;
    int best_b = -1;
    int best_ia = -1;
    int best_ib = -1;
    for (int a = 0; a < static_cast<int>(groups.size()); ++a) {
      for (int b = a + 1; b < static_cast<int>(groups.size()); ++b) {
        if (groups[a].ratio != groups[b].ratio) {
          continue;
        }
        const double before =
            group_cost(groups[a], input.signals, input.crossing_weight,
                       input.position_weight) +
            group_cost(groups[b], input.signals, input.crossing_weight,
                       input.position_weight);
        for (int ia : candidates[a]) {
          for (int ib : candidates[b]) {
            const int sa = groups[a].signals[ia];
            const int sb = groups[b].signals[ib];
            if ((input.signals[sa].slot != input.signals[sb].slot) &&
                (groups[a].slots.count(input.signals[sb].slot) ||
                 groups[b].slots.count(input.signals[sa].slot))) {
              continue;
            }
            const double after =
                cost_after_swap(
                    groups[a], input.signals[sa], input.signals[sb],
                    input.crossing_weight, input.position_weight) +
                cost_after_swap(
                    groups[b], input.signals[sb], input.signals[sa],
                    input.crossing_weight, input.position_weight);
            const double delta = after - before;
            if (std::make_tuple(delta, a, b, ia, ib) <
                std::make_tuple(best_delta, best_a, best_b, best_ia, best_ib)) {
              best_delta = delta;
              best_a = a;
              best_b = b;
              best_ia = ia;
              best_ib = ib;
            }
          }
        }
      }
    }
    if (best_a < 0) {
      break;
    }
    std::swap(groups[best_a].signals[best_ia],
              groups[best_b].signals[best_ib]);
    rebuild(groups[best_a], input.signals);
    rebuild(groups[best_b], input.signals);
  }
}

// Exact rectangular assignment for groups <= pins using the Hungarian method.
std::vector<int> assign_pins(
    const Input& input, const std::vector<Group>& groups, int lanes) {
  const int n = static_cast<int>(groups.size());
  const int m = lanes;
  std::vector<std::vector<double>> cost(n + 1,
                                        std::vector<double>(m + 1, 0.0));
  for (int i = 1; i <= n; ++i) {
    for (int pin = 1; pin <= m; ++pin) {
      const double pin_y =
          m == 1 ? 0.5 : static_cast<double>(pin - 1) / (m - 1);
      for (int signal : groups[i - 1].signals) {
        cost[i][pin] +=
            std::abs(input.signals[signal].source_y - pin_y) +
            std::abs(input.signals[signal].sink_y - pin_y);
      }
    }
  }
  std::vector<double> u(n + 1), v(m + 1);
  std::vector<int> p(m + 1), way(m + 1);
  for (int i = 1; i <= n; ++i) {
    p[0] = i;
    int j0 = 0;
    std::vector<double> minv(m + 1,
                             std::numeric_limits<double>::infinity());
    std::vector<char> used(m + 1, false);
    do {
      used[j0] = true;
      const int i0 = p[j0];
      double delta = std::numeric_limits<double>::infinity();
      int j1 = 0;
      for (int j = 1; j <= m; ++j) {
        if (used[j]) continue;
        const double cur = cost[i0][j] - u[i0] - v[j];
        if (cur < minv[j]) {
          minv[j] = cur;
          way[j] = j0;
        }
        if (std::make_pair(minv[j], j) < std::make_pair(delta, j1)) {
          delta = minv[j];
          j1 = j;
        }
      }
      for (int j = 0; j <= m; ++j) {
        if (used[j]) {
          u[p[j]] += delta;
          v[j] -= delta;
        } else {
          minv[j] -= delta;
        }
      }
      j0 = j1;
    } while (p[j0] != 0);
    do {
      const int j1 = way[j0];
      p[j0] = p[j1];
      j0 = j1;
    } while (j0 != 0);
  }
  std::vector<int> result(n, -1);
  for (int pin = 1; pin <= m; ++pin) {
    if (p[pin] > 0) {
      result[p[pin] - 1] = pin - 1;
    }
  }
  return result;
}

void run(const std::string& input_path, const std::string& output_path) {
  const Input input = read_input(input_path);
  std::vector<std::tuple<int, int, int>> assignments;
  int next_group = 0;
  double objective = 0.0;
  for (int domain = 0; domain < static_cast<int>(input.domains.size());
       ++domain) {
    std::vector<Group> groups = initial_grouping(input, domain);
    refine_groups(input, groups);
    std::sort(groups.begin(), groups.end(), [&](const Group& a, const Group& b) {
      return std::make_tuple(a.ratio, group_mean_y(a, input.signals),
                             a.signals) <
          std::make_tuple(b.ratio, group_mean_y(b, input.signals), b.signals);
    });
    const std::vector<int> pins =
        assign_pins(input, groups, input.domains[domain].lanes);
    for (int index = 0; index < static_cast<int>(groups.size()); ++index) {
      objective += group_cost(groups[index], input.signals,
                              input.crossing_weight, input.position_weight);
      for (int signal : groups[index].signals) {
        assignments.emplace_back(signal, next_group + index, pins[index]);
      }
    }
    next_group += static_cast<int>(groups.size());
  }
  std::sort(assignments.begin(), assignments.end());
  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  output << "EMUFLOW_PIN_PLANNER_OUTPUT_V1\n";
  output << std::setprecision(17);
  output << "METRIC " << next_group << " " << objective << "\n";
  for (const auto& [signal, group, pin] : assignments) {
    output << "ASSIGN " << signal << " " << group << " " << pin << "\n";
  }
}

}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc != 3) {
      throw std::runtime_error("usage: emuflow_pin_planner INPUT OUTPUT");
    }
    run(argv[1], argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_pin_planner: " << error.what() << "\n";
    return 2;
  }
}
