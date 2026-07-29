// SPDX-License-Identifier: Apache-2.0
//
// Channel-usage feedback for simultaneous partitioning and TDM grouping.
// The formulation follows Chen et al., ICCAD 2018, and augments channel
// usage with the maximum-pairwise-cut pressure used by Zheng & Young,
// ASP-DAC 2023.

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr double kEps = 1.0e-12;

struct Net {
  double criticality = 0.0;
  int ratio = 1;
};

struct Input {
  int max_ratio = 1;
  double alpha = 1.0;
  double pair_pressure_weight = 1.0;
  std::vector<Net> nets;
};

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string magic;
  std::getline(stream, magic);
  if (magic != "EMUFLOW_PARTITION_FEEDBACK_INPUT_V1") {
    throw std::runtime_error("unsupported input header");
  }
  Input input;
  std::string kind;
  while (stream >> kind) {
    if (kind == "PARAM") {
      stream >> input.max_ratio >> input.alpha >>
          input.pair_pressure_weight;
    } else if (kind == "NET") {
      int index = -1;
      Net net;
      stream >> index >> net.criticality >> net.ratio;
      if (index != static_cast<int>(input.nets.size())) {
        throw std::runtime_error("NET indices must be contiguous");
      }
      input.nets.push_back(net);
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!stream) {
      throw std::runtime_error("malformed input record");
    }
  }
  if (input.max_ratio <= 0 || input.alpha <= 0.0 ||
      input.pair_pressure_weight < 0.0 || input.nets.empty()) {
    throw std::runtime_error("invalid feedback input");
  }
  for (const Net& net : input.nets) {
    if (!std::isfinite(net.criticality) || net.criticality < 0.0 ||
        net.criticality > 1.0 || net.ratio <= 0 ||
        net.ratio > input.max_ratio) {
      throw std::runtime_error("invalid NET record");
    }
  }
  return input;
}

void run(const Input& input, const std::string& output_path) {
  double threshold = -std::numeric_limits<double>::infinity();
  for (const Net& net : input.nets) {
    threshold = std::max(
        threshold, net.criticality + input.alpha * net.ratio);
  }

  std::vector<int> group_sizes;
  std::vector<double> channel_usage;
  std::vector<double> combined;
  double minimum = std::numeric_limits<double>::infinity();
  double maximum = 0.0;
  for (const Net& net : input.nets) {
    const int group_size = std::clamp(
        static_cast<int>(std::floor(
            (threshold - net.criticality + kEps) / input.alpha)),
        1, input.max_ratio);
    const double usage = 1.0 / group_size;
    const double pressure =
        static_cast<double>(net.ratio) / input.max_ratio;
    const double value =
        usage * (1.0 + input.pair_pressure_weight * pressure);
    group_sizes.push_back(group_size);
    channel_usage.push_back(usage);
    combined.push_back(value);
    minimum = std::min(minimum, value);
    maximum = std::max(maximum, value);
  }

  std::ofstream output(output_path);
  if (!output) {
    throw std::runtime_error("cannot open output: " + output_path);
  }
  output << "EMUFLOW_PARTITION_FEEDBACK_OUTPUT_V1\n";
  output << std::setprecision(17);
  for (int index = 0; index < static_cast<int>(input.nets.size()); ++index) {
    // Normalize the smallest feedback weight to one so unannotated nets retain
    // unit cost and every annotated cut remains at least equally important.
    const double weight = combined[index] / minimum;
    output << "NET " << index << ' ' << group_sizes[index] << ' '
           << channel_usage[index] << ' ' << combined[index] << ' '
           << weight << '\n';
  }
  output << "METRIC objective_threshold " << threshold << '\n';
  output << "METRIC minimum_combined_usage " << minimum << '\n';
  output << "METRIC maximum_combined_usage " << maximum << '\n';
  output << "METRIC maximum_feedback_weight " << maximum / minimum << '\n';
}

void usage(const char* executable) {
  std::cerr << "usage: " << executable << " INPUT OUTPUT\n";
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    usage(argv[0]);
    return 0;
  }
  if (argc != 3) {
    usage(argv[0]);
    return 2;
  }
  try {
    run(read_input(argv[1]), argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_tdm_partition_feedback: " << error.what() << '\n';
    return 1;
  }
}
