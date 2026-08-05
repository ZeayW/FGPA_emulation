// Copyright (c) 2026 EmuFlow contributors.
// SPDX-License-Identifier: Apache-2.0
//
// Fixed-routing topology refinement for the 2025 EDA Elite multi-FPGA model.
// The kernel greedily minimizes the exact quantized maximum path delay.  Each
// move adds the smallest legal number of channels that crosses a TDM-ratio
// quantization boundary, then updates every affected source-to-sink path.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

struct Pair {
  int left = -1;
  int right = -1;
  int load = 0;
  int initial_channels = 0;
  std::vector<int> paths;
};

struct Model {
  int fpga_count = 0;
  int change_budget = 0;
  int quantum = 0;
  int max_ratio = 0;
  double alpha_ns = 0.0;
  double beta_ns = 0.0;
  std::vector<int> limits;
  std::vector<std::vector<int>> topology;
  std::vector<Pair> pairs;
  std::vector<std::vector<int>> paths;
};

int quantized_ratio(int load, int channels, int quantum) {
  if (load <= 0) return 0;
  if (channels <= 0) throw std::runtime_error("used pair has no channels");
  const long long denominator = static_cast<long long>(channels) * quantum;
  return static_cast<int>(((load + denominator - 1) / denominator) * quantum);
}

double pair_delay(const Model& model, int pair_index) {
  const Pair& pair = model.pairs.at(pair_index);
  const int channels = model.topology[pair.left][pair.right];
  return model.beta_ns + model.alpha_ns *
      quantized_ratio(pair.load, channels, model.quantum);
}

Model read_model(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open input: " + path);
  std::string magic;
  input >> magic;
  if (magic != "EMUFLOW_EDA2025_TOPOLOGY_V1") {
    throw std::runtime_error("unsupported input schema");
  }
  Model model;
  int pair_count = 0;
  input >> model.fpga_count >> pair_count >> model.change_budget >>
      model.quantum >> model.max_ratio >> model.alpha_ns >> model.beta_ns;
  if (!input || model.fpga_count <= 1 || pair_count < 0 ||
      model.change_budget < 0 || model.quantum <= 0 ||
      model.max_ratio <= 0 || model.max_ratio % model.quantum != 0 ||
      model.alpha_ns <= 0.0 || model.beta_ns < 0.0) {
    throw std::runtime_error("invalid model header");
  }
  model.limits.resize(model.fpga_count);
  for (int& limit : model.limits) input >> limit;
  model.topology.assign(model.fpga_count,
                        std::vector<int>(model.fpga_count, 0));
  for (auto& row : model.topology) {
    for (int& channels : row) input >> channels;
  }
  for (int left = 0; left < model.fpga_count; ++left) {
    if (model.limits[left] <= 0 || model.topology[left][left] != 0) {
      throw std::runtime_error("invalid FPGA limit or topology diagonal");
    }
    int used = 0;
    for (int right = 0; right < model.fpga_count; ++right) {
      if (model.topology[left][right] < 0 ||
          model.topology[left][right] != model.topology[right][left]) {
        throw std::runtime_error("topology must be non-negative and symmetric");
      }
      used += model.topology[left][right];
    }
    if (used > model.limits[left]) {
      throw std::runtime_error("initial topology exceeds an FPGA IO limit");
    }
  }
  model.pairs.resize(pair_count);
  for (Pair& pair : model.pairs) {
    input >> pair.left >> pair.right >> pair.load;
    if (!input || pair.left < 0 || pair.right <= pair.left ||
        pair.right >= model.fpga_count || pair.load <= 0 ||
        model.topology[pair.left][pair.right] <= 0) {
      throw std::runtime_error("invalid used-pair record");
    }
    pair.initial_channels = model.topology[pair.left][pair.right];
    if (quantized_ratio(pair.load, pair.initial_channels, model.quantum) >
        model.max_ratio) {
      throw std::runtime_error("input route exceeds maximum TDM ratio");
    }
  }
  int path_count = 0;
  input >> path_count;
  if (!input || path_count <= 0) throw std::runtime_error("no routed paths");
  model.paths.resize(path_count);
  for (int path_index = 0; path_index < path_count; ++path_index) {
    int edge_count = 0;
    input >> edge_count;
    if (!input || edge_count <= 0) throw std::runtime_error("empty routed path");
    auto& path_edges = model.paths[path_index];
    path_edges.resize(edge_count);
    std::unordered_set<int> unique;
    for (int& pair_index : path_edges) {
      input >> pair_index;
      if (!input || pair_index < 0 || pair_index >= pair_count ||
          !unique.insert(pair_index).second) {
        throw std::runtime_error("invalid or repeated pair in routed path");
      }
      model.pairs[pair_index].paths.push_back(path_index);
    }
  }
  return model;
}

struct Result {
  double initial_worst = 0.0;
  double optimized_worst = 0.0;
  int changes = 0;
  int iterations = 0;
};

Result optimize(Model& model) {
  const auto original_topology = model.topology;
  std::vector<int> io_used(model.fpga_count, 0);
  for (int fpga = 0; fpga < model.fpga_count; ++fpga) {
    io_used[fpga] = std::accumulate(model.topology[fpga].begin(),
                                    model.topology[fpga].end(), 0);
  }
  std::vector<double> delays(model.pairs.size(), 0.0);
  for (std::size_t index = 0; index < model.pairs.size(); ++index) {
    delays[index] = pair_delay(model, static_cast<int>(index));
  }
  std::vector<double> path_delays(model.paths.size(), 0.0);
  for (std::size_t path_index = 0; path_index < model.paths.size(); ++path_index) {
    for (int pair_index : model.paths[path_index]) {
      path_delays[path_index] += delays[pair_index];
    }
  }
  Result result;
  result.initial_worst = *std::max_element(path_delays.begin(), path_delays.end());

  while (result.changes < model.change_budget) {
    std::vector<int> order(model.paths.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int left, int right) {
      if (path_delays[left] != path_delays[right])
        return path_delays[left] > path_delays[right];
      return left < right;
    });
    const double current_worst = path_delays[order.front()];
    const std::size_t critical_window = std::min<std::size_t>(2048, order.size());
    std::unordered_set<int> candidate_set;
    for (std::size_t rank = 0; rank < critical_window; ++rank) {
      for (int pair_index : model.paths[order[rank]]) {
        candidate_set.insert(pair_index);
      }
    }

    int best_pair = -1;
    int best_add = 0;
    double best_worst = std::numeric_limits<double>::infinity();
    double best_pressure_gain = -1.0;
    double best_delta = 0.0;
    for (int pair_index : candidate_set) {
      const Pair& pair = model.pairs[pair_index];
      const int slack = std::min(model.limits[pair.left] - io_used[pair.left],
                                 model.limits[pair.right] - io_used[pair.right]);
      const int available = std::min(slack, model.change_budget - result.changes);
      if (available <= 0) continue;
      const int old_channels = model.topology[pair.left][pair.right];
      const int old_ratio = quantized_ratio(pair.load, old_channels, model.quantum);
      int add = 1;
      while (add <= available &&
             quantized_ratio(pair.load, old_channels + add, model.quantum) >=
                 old_ratio) {
        ++add;
      }
      if (add > available) continue;
      const int new_ratio =
          quantized_ratio(pair.load, old_channels + add, model.quantum);
      const double delta = model.alpha_ns * (old_ratio - new_ratio);
      if (delta <= 0.0) continue;

      double max_affected = -1.0;
      double max_unaffected = -1.0;
      for (int path_index : order) {
        const auto& edges = model.paths[path_index];
        const bool affected = std::find(edges.begin(), edges.end(), pair_index) !=
                              edges.end();
        if (affected && max_affected < 0.0) max_affected = path_delays[path_index];
        if (!affected && max_unaffected < 0.0) max_unaffected = path_delays[path_index];
        if (max_affected >= 0.0 && max_unaffected >= 0.0) break;
      }
      const double predicted_worst = std::max(
          max_unaffected, max_affected < 0.0 ? -1.0 : max_affected - delta);
      double pressure_gain = 0.0;
      const double scale = std::max(1.0, model.alpha_ns * model.quantum * 8.0);
      for (int path_index : pair.paths) {
        pressure_gain += delta *
            std::exp(std::max(-40.0,
                              (path_delays[path_index] - current_worst) / scale));
      }
      pressure_gain /= add;
      const auto candidate_key = std::make_tuple(
          predicted_worst, -pressure_gain, -delta / add, pair_index);
      const auto best_key = std::make_tuple(
          best_worst, -best_pressure_gain,
          best_add == 0 ? 0.0 : -best_delta / best_add, best_pair);
      if (best_pair < 0 || candidate_key < best_key) {
        best_pair = pair_index;
        best_add = add;
        best_worst = predicted_worst;
        best_pressure_gain = pressure_gain;
        best_delta = delta;
      }
    }
    if (best_pair < 0) break;
    Pair& pair = model.pairs[best_pair];
    model.topology[pair.left][pair.right] += best_add;
    model.topology[pair.right][pair.left] += best_add;
    io_used[pair.left] += best_add;
    io_used[pair.right] += best_add;
    delays[best_pair] -= best_delta;
    for (int path_index : pair.paths) path_delays[path_index] -= best_delta;
    result.changes += best_add;
    ++result.iterations;
  }
  result.optimized_worst =
      *std::max_element(path_delays.begin(), path_delays.end());
  if (result.optimized_worst >= result.initial_worst - 1.0e-9) {
    model.topology = original_topology;
    result.optimized_worst = result.initial_worst;
    result.changes = 0;
    result.iterations = 0;
  }
  return result;
}

void write_result(const std::string& path, const Model& model,
                  const Result& result) {
  std::ofstream output(path);
  if (!output) throw std::runtime_error("cannot open output: " + path);
  output << std::setprecision(17);
  output << "EMUFLOW_EDA2025_TOPOLOGY_RESULT_V1\n";
  output << "METRIC initial_worst_path_delay_ns " << result.initial_worst << '\n';
  output << "METRIC optimized_worst_path_delay_ns " << result.optimized_worst << '\n';
  output << "METRIC changed_channels " << result.changes << '\n';
  output << "METRIC iterations " << result.iterations << '\n';
  int changed_pairs = 0;
  for (const Pair& pair : model.pairs) {
    if (model.topology[pair.left][pair.right] != pair.initial_channels)
      ++changed_pairs;
  }
  output << "PAIR_CHANGES " << changed_pairs << '\n';
  for (const Pair& pair : model.pairs) {
    const int current = model.topology[pair.left][pair.right];
    if (current != pair.initial_channels) {
      output << pair.left << ' ' << pair.right << ' ' << pair.initial_channels
             << ' ' << current << ' ' << pair.load << '\n';
    }
  }
  output << "TOPOLOGY " << model.fpga_count << '\n';
  for (const auto& row : model.topology) {
    for (int column = 0; column < model.fpga_count; ++column) {
      if (column) output << ' ';
      output << row[column];
    }
    output << '\n';
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 2 && std::string(argv[1]) == "--help") {
    std::cout << "Usage: emuflow_eda2025_topology_optimizer INPUT OUTPUT\n";
    return 0;
  }
  if (argc != 3) {
    std::cerr << "expected INPUT and OUTPUT (use --help)\n";
    return 2;
  }
  try {
    Model model = read_model(argv[1]);
    const Result result = optimize(model);
    write_result(argv[2], model, result);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_eda2025_topology_optimizer: " << error.what() << '\n';
    return 1;
  }
}
