// SPDX-License-Identifier: Apache-2.0
// Deterministic position refinement bounded by Chimew Section 3.3.2.

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace {

struct Signal {
  int index = -1;
  int domain = -1;
  int ratio = 0;
  unsigned long long encoding = 0;
  int group = -1;
  double source_y = 0.0;
};

std::vector<Signal> read_input(const std::string& path) {
  std::ifstream stream(path);
  std::string header;
  if (!(stream >> header) || header != "EMUFLOW_CHIMEW_REFINER_INPUT_V1") {
    throw std::runtime_error("invalid Chimew refiner input header");
  }
  std::vector<Signal> signals;
  std::string record;
  while (stream >> record) {
    if (record != "SIGNAL") {
      throw std::runtime_error("invalid Chimew refiner record");
    }
    Signal signal;
    if (!(stream >> signal.index >> signal.domain >> signal.ratio >>
          signal.encoding >> signal.group >> signal.source_y)) {
      throw std::runtime_error("malformed Chimew refiner signal");
    }
    if (signal.index != static_cast<int>(signals.size()) ||
        signal.domain < 0 || signal.ratio <= 0 || signal.group < 0 ||
        !std::isfinite(signal.source_y)) {
      throw std::runtime_error("invalid Chimew refiner signal identity");
    }
    signals.push_back(signal);
  }
  if (signals.empty()) {
    throw std::runtime_error("Chimew refiner requires at least one signal");
  }
  return signals;
}

std::map<int, std::set<int>> group_members(
    const std::vector<Signal>& signals) {
  std::map<int, std::set<int>> members;
  for (int index = 0; index < static_cast<int>(signals.size()); ++index) {
    members[signals[index].group].insert(index);
  }
  return members;
}

double pairwise_objective(const std::vector<Signal>& signals,
                          const std::map<int, std::set<int>>& members,
                          const std::set<int>& groups) {
  double objective = 0.0;
  for (int group : groups) {
    const auto found = members.find(group);
    if (found == members.end()) {
      continue;
    }
    std::vector<double> values;
    values.reserve(found->second.size());
    for (int index : found->second) {
      values.push_back(signals[index].source_y);
    }
    std::sort(values.begin(), values.end());
    double prefix = 0.0;
    for (std::size_t index = 0; index < values.size(); ++index) {
      objective += values[index] * static_cast<double>(index) - prefix;
      prefix += values[index];
    }
  }
  return objective;
}

double median(std::vector<double> values) {
  if (values.empty()) {
    throw std::runtime_error("cannot calculate an empty median");
  }
  std::sort(values.begin(), values.end());
  const std::size_t middle = values.size() / 2;
  if (values.size() % 2 == 1) {
    return values[middle];
  }
  return (values[middle - 1] + values[middle]) / 2.0;
}

void refine_bucket(std::vector<Signal>& signals,
                   std::map<int, std::set<int>>& members,
                   const std::vector<int>& bucket,
                   unsigned long long encoding, int& accepted_buckets,
                   int& moved_signals) {
  std::map<int, int> slots;
  std::set<int> affected_groups;
  for (int index : bucket) {
    ++slots[signals[index].group];
    affected_groups.insert(signals[index].group);
  }
  if (bucket.size() < 2 || affected_groups.size() < 2) {
    return;
  }

  std::vector<std::pair<double, int>> ordered_groups;
  for (int group : affected_groups) {
    std::vector<double> anchor_values;
    for (int index : members.at(group)) {
      if (signals[index].encoding != encoding) {
        anchor_values.push_back(signals[index].source_y);
      }
    }
    if (anchor_values.empty()) {
      for (int index : members.at(group)) {
        if (signals[index].encoding == encoding) {
          anchor_values.push_back(signals[index].source_y);
        }
      }
    }
    ordered_groups.emplace_back(median(anchor_values), group);
  }
  std::sort(ordered_groups.begin(), ordered_groups.end());

  std::vector<int> ordered_signals = bucket;
  std::sort(ordered_signals.begin(), ordered_signals.end(),
            [&](int lhs, int rhs) {
              return std::make_tuple(signals[lhs].source_y,
                                     signals[lhs].index) <
                     std::make_tuple(signals[rhs].source_y,
                                     signals[rhs].index);
            });

  const double before = pairwise_objective(signals, members, affected_groups);
  std::vector<int> old_groups;
  old_groups.reserve(ordered_signals.size());
  for (int index : ordered_signals) {
    old_groups.push_back(signals[index].group);
  }
  int position = 0;
  for (const auto& [anchor, group] : ordered_groups) {
    (void)anchor;
    for (int slot = 0; slot < slots[group]; ++slot) {
      signals[ordered_signals[position++]].group = group;
    }
  }
  for (int offset = 0; offset < static_cast<int>(ordered_signals.size());
       ++offset) {
    members[old_groups[offset]].erase(ordered_signals[offset]);
  }
  for (int index : ordered_signals) {
    members[signals[index].group].insert(index);
  }
  const double after = pairwise_objective(signals, members, affected_groups);
  if (after > before + 1e-12) {
    for (int offset = 0; offset < static_cast<int>(ordered_signals.size());
         ++offset) {
      members[signals[ordered_signals[offset]].group].erase(
          ordered_signals[offset]);
      signals[ordered_signals[offset]].group = old_groups[offset];
      members[old_groups[offset]].insert(ordered_signals[offset]);
    }
    return;
  }
  int moved = 0;
  for (int offset = 0; offset < static_cast<int>(ordered_signals.size());
       ++offset) {
    moved += signals[ordered_signals[offset]].group != old_groups[offset];
  }
  if (moved > 0) {
    ++accepted_buckets;
    moved_signals += moved;
  }
}

void run(const std::string& input_path, const std::string& output_path) {
  auto signals = read_input(input_path);
  auto members = group_members(signals);
  std::set<int> all_groups;
  std::map<std::tuple<int, int, unsigned long long>, std::vector<int>> buckets;
  for (int index = 0; index < static_cast<int>(signals.size()); ++index) {
    all_groups.insert(signals[index].group);
    buckets[{signals[index].domain, signals[index].ratio,
             signals[index].encoding}]
        .push_back(index);
  }
  const double before = pairwise_objective(signals, members, all_groups);
  int accepted_buckets = 0;
  int moved_signals = 0;
  for (const auto& [key, bucket] : buckets) {
    refine_bucket(signals, members, bucket, std::get<2>(key), accepted_buckets,
                  moved_signals);
  }
  const double after = pairwise_objective(signals, members, all_groups);
  if (after > before + 1e-12) {
    throw std::runtime_error("position refinement increased its objective");
  }

  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open Chimew refiner output");
  }
  output << "EMUFLOW_CHIMEW_REFINER_OUTPUT_V1\n";
  output << std::setprecision(17);
  output << "METRIC " << accepted_buckets << " " << moved_signals << " "
         << before << " " << after << "\n";
  std::sort(signals.begin(), signals.end(),
            [](const Signal& lhs, const Signal& rhs) {
              return lhs.index < rhs.index;
            });
  for (const auto& signal : signals) {
    output << "ASSIGN " << signal.index << " " << signal.group << "\n";
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_chimew_position_refiner INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_chimew_position_refiner INPUT OUTPUT\n";
    return 2;
  }
  try {
    run(argv[1], argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_chimew_position_refiner: " << error.what() << "\n";
    return 1;
  }
}
