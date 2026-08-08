// SPDX-License-Identifier: Apache-2.0
// Independent implementation of Chimew Algorithm 1 (FPGA 2026).

#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
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
};

int popcount(unsigned long long value) {
  return __builtin_popcountll(value);
}

std::vector<Signal> read_input(const std::string& path) {
  std::ifstream stream(path);
  std::string header;
  if (!(stream >> header) || header != "EMUFLOW_CHIMEW_GROUPER_INPUT_V1") {
    throw std::runtime_error("invalid Chimew grouper input header");
  }
  std::vector<Signal> signals;
  std::string record;
  while (stream >> record) {
    if (record != "SIGNAL") {
      throw std::runtime_error("invalid Chimew grouper record");
    }
    Signal signal;
    if (!(stream >> signal.index >> signal.domain >> signal.ratio >>
          signal.encoding)) {
      throw std::runtime_error("malformed Chimew signal record");
    }
    if (signal.index != static_cast<int>(signals.size()) ||
        signal.domain < 0 || signal.ratio <= 0) {
      throw std::runtime_error("invalid Chimew signal identity");
    }
    signals.push_back(signal);
  }
  if (signals.empty()) {
    throw std::runtime_error("Chimew grouper requires at least one signal");
  }
  return signals;
}

std::vector<std::vector<int>> group_bucket(
    const std::vector<Signal>& signals, std::vector<int> remaining,
    int ratio) {
  std::map<unsigned long long, int> multiplicity;
  for (int index : remaining) {
    ++multiplicity[signals[index].encoding];
  }
  std::sort(remaining.begin(), remaining.end(), [&](int lhs, int rhs) {
    const auto& a = signals[lhs];
    const auto& b = signals[rhs];
    return std::make_tuple(-popcount(a.encoding),
                           -static_cast<long long>(a.encoding), a.index) <
        std::make_tuple(-popcount(b.encoding),
                        -static_cast<long long>(b.encoding), b.index);
  });

  std::vector<std::vector<int>> groups;
  while (!remaining.empty()) {
    std::vector<int> group;
    unsigned long long target = signals[remaining.front()].encoding;
    while (!remaining.empty() && static_cast<int>(group.size()) < ratio) {
      int best_position = -1;
      std::tuple<int, int, int, int, long long, int> best_key{
          4, 0, 0, 0, 0, 0};
      for (int position = 0;
           position < static_cast<int>(remaining.size()); ++position) {
        const auto& signal = signals[remaining[position]];
        int category = 2;
        if (signal.encoding == target) {
          category = 0;
        } else if ((signal.encoding | target) == target) {
          category = 1;
        }
        const int different = popcount(signal.encoding ^ target);
        const auto key = std::make_tuple(
            category,
            category == 2 ? different : 0,
            -popcount(signal.encoding),
            multiplicity[signal.encoding],
            -static_cast<long long>(signal.encoding), signal.index);
        if (best_position < 0 || key < best_key) {
          best_position = position;
          best_key = key;
        }
      }
      const int selected = remaining[best_position];
      target |= signals[selected].encoding;
      group.push_back(selected);
      --multiplicity[signals[selected].encoding];
      remaining.erase(remaining.begin() + best_position);
    }
    groups.push_back(std::move(group));
  }
  return groups;
}

void run(const std::string& input_path, const std::string& output_path) {
  const auto signals = read_input(input_path);
  std::map<std::pair<int, int>, std::vector<int>> buckets;
  for (int index = 0; index < static_cast<int>(signals.size()); ++index) {
    buckets[{signals[index].domain, signals[index].ratio}].push_back(index);
  }

  std::vector<int> assignment(signals.size(), -1);
  int group_id = 0;
  long long crossing_bits = 0;
  for (const auto& [key, bucket] : buckets) {
    const int ratio = key.second;
    for (const auto& group : group_bucket(signals, bucket, ratio)) {
      unsigned long long encoding = 0;
      for (int signal : group) {
        assignment[signal] = group_id;
        encoding |= signals[signal].encoding;
      }
      crossing_bits += popcount(encoding);
      ++group_id;
    }
  }

  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open Chimew grouper output");
  }
  output << "EMUFLOW_CHIMEW_GROUPER_OUTPUT_V1\n";
  output << "METRIC " << group_id << " " << crossing_bits << "\n";
  for (int index = 0; index < static_cast<int>(assignment.size()); ++index) {
    output << "ASSIGN " << index << " " << assignment[index] << "\n";
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "usage: emuflow_chimew_signal_grouper INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "usage: emuflow_chimew_signal_grouper INPUT OUTPUT\n";
    return 2;
  }
  try {
    run(argv[1], argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_chimew_signal_grouper: " << error.what() << "\n";
    return 1;
  }
}
