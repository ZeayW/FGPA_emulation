// SPDX-License-Identifier: Apache-2.0
// Independent implementation of Chimew Algorithm 1 (FPGA 2026).

#include <algorithm>
#include <deque>
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
  std::map<unsigned long long, std::deque<int>> remaining_by_encoding;
  for (int index : remaining) {
    remaining_by_encoding[signals[index].encoding].push_back(index);
  }

  std::vector<std::vector<int>> groups;
  int remaining_count = static_cast<int>(remaining.size());
  while (remaining_count > 0) {
    std::vector<int> group;
    bool have_seed = false;
    unsigned long long target = 0;
    std::tuple<int, long long, int> seed_key;
    for (const auto& [encoding, indices] : remaining_by_encoding) {
      if (indices.empty()) {
        continue;
      }
      const auto key = std::make_tuple(
          -popcount(encoding), -static_cast<long long>(encoding),
          indices.front());
      if (!have_seed || key < seed_key) {
        have_seed = true;
        seed_key = key;
        target = encoding;
      }
    }
    while (remaining_count > 0 && static_cast<int>(group.size()) < ratio) {
      bool have_best = false;
      unsigned long long best_encoding = 0;
      std::tuple<int, int, int, int, long long, int> best_key{
          4, 0, 0, 0, 0, 0};
      for (const auto& [encoding, indices] : remaining_by_encoding) {
        if (indices.empty()) {
          continue;
        }
        int category = 2;
        if (encoding == target) {
          category = 0;
        } else if ((encoding | target) == target) {
          category = 1;
        }
        const int different = popcount(encoding ^ target);
        const auto key = std::make_tuple(
            category,
            category == 2 ? different : 0,
            -popcount(encoding),
            static_cast<int>(indices.size()),
            -static_cast<long long>(encoding), indices.front());
        if (!have_best || key < best_key) {
          have_best = true;
          best_encoding = encoding;
          best_key = key;
        }
      }
      auto& selected_indices = remaining_by_encoding[best_encoding];
      const int selected = selected_indices.front();
      selected_indices.pop_front();
      target |= best_encoding;
      group.push_back(selected);
      --remaining_count;
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
