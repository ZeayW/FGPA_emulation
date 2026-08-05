// Copyright (c) 2026 EmuFlow contributors.
// SPDX-License-Identifier: Apache-2.0
//
// Routing-guided topology refinement for the 2025 EDA Elite multi-FPGA model.
// The kernel combines exact fixed-route channel refinement with direct-link
// shortcut candidates, always using the contest's quantized TDM-delay model.

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
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

struct Pair {
  int left = -1;
  int right = -1;
  int load = 0;
  std::vector<int> paths;
};

struct RoutedPath {
  int source = -1;
  int sink = -1;
  std::vector<int> edges;
};

struct Shortcut {
  int left = -1;
  int right = -1;
  std::vector<int> paths;
};

struct DonorMove {
  int left = -1;
  int right = -1;
  int pair_index = -1;
  double delay_increase = 0.0;
};

struct Model {
  int fpga_count = 0;
  int change_budget = 0;
  int quantum = 0;
  int max_ratio = 0;
  double alpha_ns = 0.0;
  double beta_ns = 0.0;
  bool enable_shortcuts = false;
  std::vector<int> limits;
  std::vector<std::vector<int>> topology;
  std::vector<std::vector<int>> input_topology;
  std::vector<std::vector<int>> used_pair_index;
  std::vector<std::vector<int>> shortcut_index;
  std::vector<Pair> pairs;
  std::vector<RoutedPath> paths;
  std::vector<Shortcut> shortcuts;
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
  if (magic != "EMUFLOW_EDA2025_TOPOLOGY_V2") {
    throw std::runtime_error("unsupported input schema");
  }
  Model model;
  int pair_count = 0;
  int enable_shortcuts = 0;
  input >> model.fpga_count >> pair_count >> model.change_budget >>
      model.quantum >> model.max_ratio >> model.alpha_ns >> model.beta_ns >>
      enable_shortcuts;
  if (!input || model.fpga_count <= 1 || pair_count < 0 ||
      model.change_budget < 0 || model.quantum <= 0 ||
      model.max_ratio <= 0 || model.max_ratio % model.quantum != 0 ||
      model.alpha_ns <= 0.0 || model.beta_ns < 0.0 ||
      (enable_shortcuts != 0 && enable_shortcuts != 1)) {
    throw std::runtime_error("invalid model header");
  }
  model.enable_shortcuts = enable_shortcuts != 0;
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
  model.input_topology = model.topology;
  model.used_pair_index.assign(
      model.fpga_count, std::vector<int>(model.fpga_count, -1));
  model.pairs.resize(pair_count);
  for (int pair_index = 0; pair_index < pair_count; ++pair_index) {
    Pair& pair = model.pairs[pair_index];
    input >> pair.left >> pair.right >> pair.load;
    if (!input || pair.left < 0 || pair.right <= pair.left ||
        pair.right >= model.fpga_count || pair.load <= 0 ||
        model.topology[pair.left][pair.right] <= 0) {
      throw std::runtime_error("invalid used-pair record");
    }
    if (model.used_pair_index[pair.left][pair.right] >= 0) {
      throw std::runtime_error("duplicate used-pair record");
    }
    model.used_pair_index[pair.left][pair.right] = pair_index;
    model.used_pair_index[pair.right][pair.left] = pair_index;
    if (quantized_ratio(pair.load, model.topology[pair.left][pair.right],
                        model.quantum) >
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
    RoutedPath& routed_path = model.paths[path_index];
    input >> routed_path.source >> routed_path.sink >> edge_count;
    if (!input || routed_path.source < 0 || routed_path.sink < 0 ||
        routed_path.source >= model.fpga_count ||
        routed_path.sink >= model.fpga_count ||
        routed_path.source == routed_path.sink || edge_count <= 0) {
      throw std::runtime_error("invalid routed path header");
    }
    auto& path_edges = routed_path.edges;
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
  model.shortcut_index.assign(
      model.fpga_count, std::vector<int>(model.fpga_count, -1));
  for (int path_index = 0; path_index < path_count; ++path_index) {
    int left = std::min(model.paths[path_index].source,
                        model.paths[path_index].sink);
    int right = std::max(model.paths[path_index].source,
                         model.paths[path_index].sink);
    // Existing physical pairs are refined through their exact routed load.
    if (!model.enable_shortcuts || model.topology[left][right] > 0) continue;
    int shortcut = model.shortcut_index[left][right];
    if (shortcut < 0) {
      shortcut = static_cast<int>(model.shortcuts.size());
      model.shortcut_index[left][right] = shortcut;
      model.shortcut_index[right][left] = shortcut;
      model.shortcuts.push_back({left, right, {}});
    }
    model.shortcuts[shortcut].paths.push_back(path_index);
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
  std::vector<double> base_path_delays(model.paths.size(), 0.0);
  for (std::size_t path_index = 0; path_index < model.paths.size(); ++path_index) {
    for (int pair_index : model.paths[path_index].edges) {
      base_path_delays[path_index] += delays[pair_index];
    }
  }
  std::vector<double> shortcut_delays(
      model.paths.size(), std::numeric_limits<double>::infinity());
  std::vector<double> effective_delays = base_path_delays;
  Result result;
  result.initial_worst =
      *std::max_element(effective_delays.begin(), effective_delays.end());
  std::vector<int> affected_stamp(model.paths.size(), 0);
  int stamp = 0;

  while (result.changes < model.change_budget) {
    std::vector<int> order(model.paths.size());
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int left, int right) {
      if (effective_delays[left] != effective_delays[right])
        return effective_delays[left] > effective_delays[right];
      return left < right;
    });
    const double current_worst = effective_delays[order.front()];
    const std::size_t critical_window = std::min<std::size_t>(2048, order.size());
    std::unordered_set<int> pair_candidates;
    std::unordered_set<int> shortcut_candidates;
    for (std::size_t rank = 0; rank < critical_window; ++rank) {
      const RoutedPath& path = model.paths[order[rank]];
      for (int pair_index : path.edges) {
        pair_candidates.insert(pair_index);
      }
      const int shortcut = model.shortcut_index[path.source][path.sink];
      if (shortcut >= 0) shortcut_candidates.insert(shortcut);
    }

    int best_kind = -1;  // 0: routed-pair capacity, 1: direct shortcut.
    int best_pair = -1;
    int best_add = 0;
    std::vector<DonorMove> best_donors;
    double best_worst = std::numeric_limits<double>::infinity();
    double best_pressure_gain = -1.0;
    double best_delta = 0.0;
    auto consider = [&](int kind, int index, int add,
                        const std::vector<DonorMove>& donors,
                        const std::vector<int>& affected,
                        const auto& new_effective) {
      if (affected.empty()) return;
      const int move_cost = add + static_cast<int>(donors.size());
      ++stamp;
      double max_affected = -1.0;
      double pressure_gain = 0.0;
      double maximum_gain = 0.0;
      const double scale = std::max(1.0, model.alpha_ns * model.quantum * 8.0);
      for (int path_index : affected) {
        affected_stamp[path_index] = stamp;
        const double updated = new_effective(path_index);
        max_affected = std::max(max_affected, updated);
        const double gain = effective_delays[path_index] - updated;
        maximum_gain = std::max(maximum_gain, gain);
        pressure_gain += gain * std::exp(std::max(
            -40.0, (effective_delays[path_index] - current_worst) / scale));
      }
      if (maximum_gain <= 1.0e-12) return;
      double max_unaffected = -1.0;
      for (int path_index : order) {
        if (affected_stamp[path_index] != stamp) {
          max_unaffected = effective_delays[path_index];
          break;
        }
      }
      const double predicted_worst = std::max(max_unaffected, max_affected);
      pressure_gain /= move_cost;
      const auto candidate_key = std::make_tuple(
          predicted_worst, -pressure_gain, -maximum_gain / move_cost,
          kind, index);
      const auto best_key = std::make_tuple(
          best_worst, -best_pressure_gain,
          best_add == 0 ? 0.0 : -best_delta / best_add,
          best_kind, best_pair);
      if (best_kind < 0 || candidate_key < best_key) {
        best_kind = kind;
        best_pair = index;
        best_add = add;
        best_donors = donors;
        best_worst = predicted_worst;
        best_pressure_gain = pressure_gain;
        best_delta = maximum_gain;
      }
    };

    auto low_cost_donors = [&](int endpoint, int target_neighbor,
                               int needed) {
      std::vector<DonorMove> donors;
      if (needed <= 0) return donors;
      std::vector<int> removed(model.fpga_count, 0);
      while (static_cast<int>(donors.size()) < needed) {
        int best_neighbor = -1;
        int best_pair_index = -1;
        double best_delta = std::numeric_limits<double>::infinity();
        double best_pressure = std::numeric_limits<double>::infinity();
        for (int neighbor = 0; neighbor < model.fpga_count; ++neighbor) {
          if (neighbor == endpoint || neighbor == target_neighbor) continue;
          const int channels =
              model.topology[endpoint][neighbor] - removed[neighbor];
          if (channels <= 1) continue;
          const int used = model.used_pair_index[endpoint][neighbor];
          double delta = 0.0;
          double pressure = 0.0;
          if (used >= 0) {
            const Pair& pair = model.pairs[used];
            const int old_ratio =
                quantized_ratio(pair.load, channels, model.quantum);
            const int new_ratio =
                quantized_ratio(pair.load, channels - 1, model.quantum);
            if (new_ratio > model.max_ratio) continue;
            delta = model.alpha_ns * (new_ratio - old_ratio);
            pressure = delta * (1.0 + pair.paths.size());
          }
          if (std::tie(pressure, delta, neighbor) <
              std::tie(best_pressure, best_delta, best_neighbor)) {
            best_neighbor = neighbor;
            best_pair_index = used;
            best_delta = delta;
            best_pressure = pressure;
          }
        }
        if (best_neighbor < 0) {
          donors.clear();
          return donors;
        }
        ++removed[best_neighbor];
        donors.push_back(
            {std::min(endpoint, best_neighbor),
             std::max(endpoint, best_neighbor), best_pair_index, best_delta});
      }
      return donors;
    };

    auto move_donors = [&](int left, int right, int add) {
      std::vector<DonorMove> donors;
      const int left_need = std::max(
          0, io_used[left] + add - model.limits[left]);
      const int right_need = std::max(
          0, io_used[right] + add - model.limits[right]);
      auto left_donors = low_cost_donors(left, right, left_need);
      auto right_donors = low_cost_donors(right, left, right_need);
      if ((left_need > 0 && left_donors.empty()) ||
          (right_need > 0 && right_donors.empty())) {
        return std::vector<DonorMove>{};
      }
      donors.insert(donors.end(), left_donors.begin(), left_donors.end());
      donors.insert(donors.end(), right_donors.begin(), right_donors.end());
      return donors;
    };

    for (int pair_index : pair_candidates) {
      const Pair& pair = model.pairs[pair_index];
      const int available = model.change_budget - result.changes;
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
      const auto donors = move_donors(pair.left, pair.right, add);
      const int left_need = std::max(
          0, io_used[pair.left] + add - model.limits[pair.left]);
      const int right_need = std::max(
          0, io_used[pair.right] + add - model.limits[pair.right]);
      if (static_cast<int>(donors.size()) != left_need + right_need ||
          add + static_cast<int>(donors.size()) > available) {
        continue;
      }
      const int new_ratio =
          quantized_ratio(pair.load, old_channels + add, model.quantum);
      const double delta = model.alpha_ns * (old_ratio - new_ratio);
      if (delta <= 0.0) continue;
      std::unordered_map<int, double> donor_increase;
      std::unordered_set<int> affected_set(pair.paths.begin(), pair.paths.end());
      std::vector<int> affected = pair.paths;
      for (const DonorMove& donor : donors) {
        if (donor.pair_index < 0 || donor.delay_increase <= 0.0) continue;
        for (int path_index : model.pairs[donor.pair_index].paths) {
          donor_increase[path_index] += donor.delay_increase;
          if (affected_set.insert(path_index).second)
            affected.push_back(path_index);
        }
      }
      std::unordered_set<int> target_paths(pair.paths.begin(), pair.paths.end());
      consider(0, pair_index, add, donors, affected, [&](int path_index) {
        const double updated_base = base_path_delays[path_index] +
            donor_increase[path_index] -
            (target_paths.count(path_index) ? delta : 0.0);
        return std::min(updated_base, shortcut_delays[path_index]);
      });
    }

    for (int shortcut_index : shortcut_candidates) {
      const Shortcut& shortcut = model.shortcuts[shortcut_index];
      const int available = model.change_budget - result.changes;
      if (available <= 0) continue;
      const int old_channels = model.topology[shortcut.left][shortcut.right];
      const int load = static_cast<int>(shortcut.paths.size());
      constexpr int kShortcutTargetRatio = 64;
      int add = old_channels == 0
                    ? std::min(
                          available,
                          std::max(2, (load + kShortcutTargetRatio - 1) /
                                          kShortcutTargetRatio))
                    : 1;
      const double old_direct = old_channels == 0
          ? std::numeric_limits<double>::infinity()
          : model.beta_ns + model.alpha_ns *
                quantized_ratio(load, old_channels, model.quantum);
      while (add <= available) {
        const int ratio = quantized_ratio(
            load, old_channels + add, model.quantum);
        const double delay = model.beta_ns + model.alpha_ns * ratio;
        if (ratio <= model.max_ratio && delay + 1.0e-12 < old_direct) break;
        ++add;
      }
      if (add > available) continue;
      const auto donors = move_donors(shortcut.left, shortcut.right, add);
      const int left_need = std::max(
          0, io_used[shortcut.left] + add - model.limits[shortcut.left]);
      const int right_need = std::max(
          0, io_used[shortcut.right] + add - model.limits[shortcut.right]);
      if (static_cast<int>(donors.size()) != left_need + right_need ||
          add + static_cast<int>(donors.size()) > available) {
        continue;
      }
      const double direct_delay = model.beta_ns + model.alpha_ns *
          quantized_ratio(load, old_channels + add, model.quantum);
      std::unordered_map<int, double> donor_increase;
      std::unordered_set<int> affected_set(
          shortcut.paths.begin(), shortcut.paths.end());
      std::vector<int> affected = shortcut.paths;
      for (const DonorMove& donor : donors) {
        if (donor.pair_index < 0 || donor.delay_increase <= 0.0) continue;
        for (int path_index : model.pairs[donor.pair_index].paths) {
          donor_increase[path_index] += donor.delay_increase;
          if (affected_set.insert(path_index).second)
            affected.push_back(path_index);
        }
      }
      std::unordered_set<int> target_paths(
          shortcut.paths.begin(), shortcut.paths.end());
      consider(1, shortcut_index, add, donors, affected,
               [&](int path_index) {
        const double updated_base =
            base_path_delays[path_index] + donor_increase[path_index];
        if (target_paths.count(path_index))
          return std::min(updated_base, direct_delay);
        return std::min(updated_base, shortcut_delays[path_index]);
      });
    }

    if (best_kind < 0) break;
    for (const DonorMove& donor : best_donors) {
      --model.topology[donor.left][donor.right];
      --model.topology[donor.right][donor.left];
      --io_used[donor.left];
      --io_used[donor.right];
      if (donor.pair_index >= 0 && donor.delay_increase > 0.0) {
        delays[donor.pair_index] += donor.delay_increase;
        for (int path_index : model.pairs[donor.pair_index].paths) {
          base_path_delays[path_index] += donor.delay_increase;
          effective_delays[path_index] = std::min(
              base_path_delays[path_index], shortcut_delays[path_index]);
        }
      }
    }
    if (best_kind == 0) {
      Pair& pair = model.pairs[best_pair];
      const int old_ratio = quantized_ratio(
          pair.load, model.topology[pair.left][pair.right], model.quantum);
      model.topology[pair.left][pair.right] += best_add;
      model.topology[pair.right][pair.left] += best_add;
      const int new_ratio = quantized_ratio(
          pair.load, model.topology[pair.left][pair.right], model.quantum);
      const double delta = model.alpha_ns * (old_ratio - new_ratio);
      io_used[pair.left] += best_add;
      io_used[pair.right] += best_add;
      delays[best_pair] -= delta;
      for (int path_index : pair.paths) {
        base_path_delays[path_index] -= delta;
        effective_delays[path_index] = std::min(
            base_path_delays[path_index], shortcut_delays[path_index]);
      }
    } else {
      Shortcut& shortcut = model.shortcuts[best_pair];
      model.topology[shortcut.left][shortcut.right] += best_add;
      model.topology[shortcut.right][shortcut.left] += best_add;
      io_used[shortcut.left] += best_add;
      io_used[shortcut.right] += best_add;
      const double direct_delay = model.beta_ns + model.alpha_ns *
          quantized_ratio(static_cast<int>(shortcut.paths.size()),
                          model.topology[shortcut.left][shortcut.right],
                          model.quantum);
      for (int path_index : shortcut.paths) {
        shortcut_delays[path_index] = direct_delay;
        effective_delays[path_index] =
            std::min(base_path_delays[path_index], direct_delay);
      }
    }
    result.changes += best_add + static_cast<int>(best_donors.size());
    ++result.iterations;
  }
  result.optimized_worst =
      *std::max_element(effective_delays.begin(), effective_delays.end());
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
  for (int left = 0; left < model.fpga_count; ++left) {
    for (int right = left + 1; right < model.fpga_count; ++right) {
      if (model.topology[left][right] != model.input_topology[left][right])
        ++changed_pairs;
    }
  }
  output << "PAIR_CHANGES " << changed_pairs << '\n';
  for (int left = 0; left < model.fpga_count; ++left) {
    for (int right = left + 1; right < model.fpga_count; ++right) {
      const int previous = model.input_topology[left][right];
      const int current = model.topology[left][right];
      if (current != previous) {
        int load = 0;
        const int used = model.used_pair_index[left][right];
        if (used >= 0) load = model.pairs[used].load;
        const int shortcut = model.shortcut_index[left][right];
        if (shortcut >= 0) {
          load = std::max(
              load, static_cast<int>(model.shortcuts[shortcut].paths.size()));
        }
        output << left << ' ' << right << ' ' << previous << ' ' << current
               << ' ' << load << '\n';
      }
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
