// SPDX-License-Identifier: Apache-2.0
//
// Continuous Lagrangian/KKT TDM-ratio optimization and discrete legalization.
// Based on Pui & Young, TODAES 2020, and Chen et al., ASP-DAC 2026.

#include <algorithm>
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

constexpr double kEps = 1.0e-12;

struct Domain {
  int lanes = 0;
};

struct Hop {
  int domain = -1;
  int direction = -1;
  double base_delay_ns = 0.0;
  double beta_ns = 0.0;
};

struct TimingPath {
  double clock_period_ns = 0.0;
  double fixed_delay_ns = 0.0;
  std::vector<int> hops;
};

struct Input {
  int max_iterations = 500;
  int max_ratio = 1;
  int ratio_quantum = 8;
  int post_refinement_iterations = 200;
  double convergence = 1.0e-8;
  double positive_scale = 1.0;
  double negative_scale = 1.0;
  double max_period = 1.0;
  std::vector<Domain> domains;
  std::vector<Hop> hops;
  std::vector<TimingPath> paths;
};

std::vector<int> parse_list(const std::string& text) {
  std::vector<int> result;
  if (text == "-") {
    return result;
  }
  std::stringstream stream(text);
  std::string token;
  while (std::getline(stream, token, ',')) {
    if (!token.empty()) {
      result.push_back(std::stoi(token));
    }
  }
  return result;
}

Input read_input(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) {
    throw std::runtime_error("cannot open input: " + path);
  }
  std::string line;
  std::getline(stream, line);
  if (line != "EMUFLOW_TDM_RATIO_INPUT_V1") {
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
      record >> input.max_iterations >> input.max_ratio >>
          input.ratio_quantum >> input.post_refinement_iterations >>
          input.convergence >> input.positive_scale >>
          input.negative_scale >> input.max_period;
    } else if (kind == "DOMAIN") {
      int index = -1;
      Domain domain;
      record >> index >> domain.lanes;
      if (index != static_cast<int>(input.domains.size())) {
        throw std::runtime_error("DOMAIN indices must be contiguous");
      }
      input.domains.push_back(domain);
    } else if (kind == "HOP") {
      int index = -1;
      Hop hop;
      record >> index >> hop.domain >> hop.direction >>
          hop.base_delay_ns >> hop.beta_ns;
      if (index != static_cast<int>(input.hops.size())) {
        throw std::runtime_error("HOP indices must be contiguous");
      }
      input.hops.push_back(hop);
    } else if (kind == "PATH") {
      int index = -1;
      TimingPath timing_path;
      std::string hops;
      record >> index >> timing_path.clock_period_ns >>
          timing_path.fixed_delay_ns >> hops;
      timing_path.hops = parse_list(hops);
      if (index != static_cast<int>(input.paths.size())) {
        throw std::runtime_error("PATH indices must be contiguous");
      }
      input.paths.push_back(std::move(timing_path));
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!record) {
      throw std::runtime_error("malformed input record: " + line);
    }
  }
  if (input.domains.empty() || input.hops.empty() || input.paths.empty() ||
      input.max_ratio <= 0 || input.ratio_quantum <= 0 ||
      input.max_iterations <= 0 || input.post_refinement_iterations < 0 ||
      input.convergence <= 0.0 || input.positive_scale <= 0.0 ||
      input.negative_scale <= 0.0 || input.max_period <= 0.0) {
    throw std::runtime_error("incomplete ratio-optimization input");
  }
  for (const Domain& domain : input.domains) {
    if (domain.lanes <= 0) {
      throw std::runtime_error("domain lane count must be positive");
    }
  }
  for (const Hop& hop : input.hops) {
    if (hop.domain < 0 ||
        hop.domain >= static_cast<int>(input.domains.size()) ||
        hop.direction < 0 || hop.beta_ns <= 0.0 ||
        hop.base_delay_ns < 0.0) {
      throw std::runtime_error("invalid hop");
    }
  }
  for (const TimingPath& timing_path : input.paths) {
    if (timing_path.clock_period_ns <= 0.0 ||
        timing_path.fixed_delay_ns < 0.0 || timing_path.hops.empty()) {
      throw std::runtime_error("invalid timing path");
    }
    std::set<int> unique_hops;
    for (int hop : timing_path.hops) {
      if (hop < 0 || hop >= static_cast<int>(input.hops.size()) ||
          !unique_hops.insert(hop).second) {
        throw std::runtime_error(
            "timing path contains an invalid or duplicate hop");
      }
    }
  }
  return input;
}

class Optimizer {
 public:
  explicit Optimizer(Input input)
      : input_(std::move(input)),
        continuous_(input_.hops.size(), input_.max_ratio),
        discrete_(input_.hops.size(), input_.max_ratio),
        lane_(input_.hops.size(), -1),
        path_mu_(input_.paths.size(), 0.0),
        edge_mu_(input_.hops.size(), 0.0),
        hop_paths_(input_.hops.size()),
        domain_hops_(input_.domains.size()) {
    for (int hop = 0; hop < static_cast<int>(input_.hops.size()); ++hop) {
      domain_hops_[input_.hops[hop].domain].push_back(hop);
    }
    for (int path = 0; path < static_cast<int>(input_.paths.size()); ++path) {
      for (int hop : input_.paths[path].hops) {
        hop_paths_[hop].push_back(path);
      }
    }
    initialize_path_multipliers();
  }

  void run() {
    double best_objective = -std::numeric_limits<double>::infinity();
    std::vector<double> best_ratios = continuous_;
    int stale = 0;
    for (int iteration = 0; iteration < input_.max_iterations; ++iteration) {
      aggregate_edge_multipliers();
      solve_kkt_ratios();
      const double objective = worst_normalized_slack(continuous_);
      if (objective > best_objective + input_.convergence) {
        best_objective = objective;
        best_ratios = continuous_;
        stale = 0;
      } else {
        ++stale;
      }
      completed_iterations_ = iteration + 1;
      if (stale >= 40) {
        break;
      }
      update_path_multipliers(iteration);
    }
    continuous_ = best_ratios;
    legalize();
    post_refine();
  }

  void write(const std::string& path) const {
    std::ofstream output(path);
    if (!output) {
      throw std::runtime_error("cannot open output: " + path);
    }
    output << "EMUFLOW_TDM_RATIO_OUTPUT_V1\n";
    output << std::setprecision(17);
    for (int index = 0; index < static_cast<int>(input_.hops.size()); ++index) {
      output << "HOP " << index << ' ' << continuous_[index] << ' '
             << discrete_[index] << ' ' << lane_[index] << '\n';
    }
    for (int index = 0; index < static_cast<int>(input_.paths.size()); ++index) {
      const auto [delay, slack, normalized] =
          path_metrics_discrete(input_.paths[index]);
      output << "PATH " << index << ' ' << delay << ' ' << slack << ' '
             << normalized << '\n';
    }
    output << "METRIC iterations " << completed_iterations_ << '\n';
    output << "METRIC continuous_worst_normalized_slack "
           << worst_normalized_slack(continuous_) << '\n';
    output << "METRIC discrete_worst_normalized_slack "
           << worst_normalized_slack_discrete() << '\n';
    output << "METRIC max_discrete_ratio "
           << *std::max_element(discrete_.begin(), discrete_.end()) << '\n';
    output << "METRIC post_refinement_swaps "
           << post_refinement_swaps_ << '\n';
    output << "METRIC dp_legalized_domains "
           << dp_legalized_domains_ << '\n';
    output << "METRIC greedy_legalized_domains "
           << greedy_legalized_domains_ << '\n';
  }

 private:
  double normalized_slack(const TimingPath& path, double slack) const {
    if (slack >= 0.0) {
      return slack * path.clock_period_ns /
          (input_.positive_scale * input_.max_period);
    }
    return slack / (input_.negative_scale * path.clock_period_ns);
  }

  std::tuple<double, double, double> path_metrics(
      const TimingPath& path, const std::vector<double>& ratios) const {
    double delay = path.fixed_delay_ns;
    for (int hop : path.hops) {
      delay += input_.hops[hop].base_delay_ns +
          input_.hops[hop].beta_ns * (ratios[hop] - 1.0);
    }
    const double slack = path.clock_period_ns - delay;
    return {delay, slack, normalized_slack(path, slack)};
  }

  std::tuple<double, double, double> path_metrics_discrete(
      const TimingPath& path) const {
    double delay = path.fixed_delay_ns;
    for (int hop : path.hops) {
      delay += input_.hops[hop].base_delay_ns +
          input_.hops[hop].beta_ns * (discrete_[hop] - 1.0);
    }
    const double slack = path.clock_period_ns - delay;
    return {delay, slack, normalized_slack(path, slack)};
  }

  std::tuple<double, double, double> path_metrics_swapped(
      const TimingPath& path, int lhs, int rhs) const {
    double delay = path.fixed_delay_ns;
    for (int hop : path.hops) {
      int ratio = discrete_[hop];
      if (hop == lhs) {
        ratio = discrete_[rhs];
      } else if (hop == rhs) {
        ratio = discrete_[lhs];
      }
      delay += input_.hops[hop].base_delay_ns +
          input_.hops[hop].beta_ns * (ratio - 1.0);
    }
    const double slack = path.clock_period_ns - delay;
    return {delay, slack, normalized_slack(path, slack)};
  }

  double delay_normalization_scale(const TimingPath& path,
                                   double slack) const {
    if (slack >= 0.0) {
      return path.clock_period_ns /
          (input_.positive_scale * input_.max_period);
    }
    return 1.0 / (input_.negative_scale * path.clock_period_ns);
  }

  double worst_normalized_slack(const std::vector<double>& ratios) const {
    double worst = std::numeric_limits<double>::infinity();
    for (const TimingPath& path : input_.paths) {
      worst = std::min(worst, std::get<2>(path_metrics(path, ratios)));
    }
    return worst;
  }

  double worst_normalized_slack_discrete() const {
    std::vector<double> ratios(discrete_.begin(), discrete_.end());
    return worst_normalized_slack(ratios);
  }

  void initialize_path_multipliers() {
    // A normalized exponential distribution is a feasible dual path flow:
    // mu >= 0 and sum(mu) = 1. It favors initially critical paths.
    std::vector<double> scores;
    scores.reserve(input_.paths.size());
    const std::vector<double> unit_ratios(input_.hops.size(), 1.0);
    double maximum = -std::numeric_limits<double>::infinity();
    for (const TimingPath& path : input_.paths) {
      const double normalized =
          std::get<2>(path_metrics(path, unit_ratios));
      const double score = -8.0 * normalized;
      scores.push_back(score);
      maximum = std::max(maximum, score);
    }
    double total = 0.0;
    for (double& score : scores) {
      score = std::exp(std::max(-60.0, score - maximum));
      total += score;
    }
    for (int index = 0; index < static_cast<int>(scores.size()); ++index) {
      path_mu_[index] = scores[index] / total;
    }
  }

  void aggregate_edge_multipliers() {
    std::fill(edge_mu_.begin(), edge_mu_.end(), 0.0);
    for (int path = 0; path < static_cast<int>(input_.paths.size()); ++path) {
      const double slack =
          std::get<1>(path_metrics(input_.paths[path], continuous_));
      const double scale =
          delay_normalization_scale(input_.paths[path], slack);
      for (int hop : input_.paths[path].hops) {
        edge_mu_[hop] += path_mu_[path] * scale;
      }
    }
  }

  void solve_kkt_ratios() {
    for (int domain = 0; domain < static_cast<int>(input_.domains.size());
         ++domain) {
      const std::vector<int>& domain_hops = domain_hops_[domain];
      if (static_cast<int>(domain_hops.size()) <=
          input_.domains[domain].lanes) {
        for (int hop : domain_hops) {
          continuous_[hop] = 1.0;
        }
        continue;
      }
      if (static_cast<double>(domain_hops.size()) / input_.max_ratio >
          input_.domains[domain].lanes + kEps) {
        throw std::runtime_error(
            "domain cannot fit within maximum continuous ratio");
      }
      std::vector<double> root_weights;
      root_weights.reserve(domain_hops.size());
      for (int hop : domain_hops) {
        root_weights.push_back(std::sqrt(
            input_.hops[hop].beta_ns *
            std::max(edge_mu_[hop], kEps)));
      }
      auto usage = [&](double root_lambda) {
        double usage = 0.0;
        for (double root_weight : root_weights) {
          const double ratio = std::clamp(
              root_lambda / root_weight, 1.0,
              static_cast<double>(input_.max_ratio));
          usage += 1.0 / ratio;
        }
        return usage;
      };
      double root_lambda =
          std::accumulate(
              root_weights.begin(), root_weights.end(), 0.0) /
          input_.domains[domain].lanes;
      std::vector<int> previous_status(domain_hops.size(), 2);
      bool active_set_converged = false;
      for (int round = 0; round < 32; ++round) {
        std::vector<int> status(domain_hops.size(), 0);
        double fixed_usage = 0.0;
        double free_root_sum = 0.0;
        for (int index = 0; index < static_cast<int>(domain_hops.size());
             ++index) {
          const double ratio = root_lambda / root_weights[index];
          if (ratio <= 1.0) {
            status[index] = -1;
            fixed_usage += 1.0;
          } else if (ratio >= input_.max_ratio) {
            status[index] = 1;
            fixed_usage += 1.0 / input_.max_ratio;
          } else {
            free_root_sum += root_weights[index];
          }
        }
        const double remaining =
            input_.domains[domain].lanes - fixed_usage;
        if (remaining <= 0.0 && free_root_sum > 0.0) {
          break;
        }
        const double updated =
            free_root_sum > 0.0
                ? free_root_sum / remaining
                : root_lambda;
        if (
            status == previous_status &&
            std::abs(updated - root_lambda) <=
                kEps * std::max(1.0, root_lambda)
        ) {
          root_lambda = updated;
          active_set_converged = true;
          break;
        }
        previous_status = std::move(status);
        root_lambda = updated;
      }
      if (
          !active_set_converged ||
          usage(root_lambda) >
              input_.domains[domain].lanes + 1.0e-9
      ) {
        double low = 0.0;
        double high = std::max(1.0, root_lambda);
        while (usage(high) > input_.domains[domain].lanes) {
          high *= 2.0;
          if (!std::isfinite(high)) {
            throw std::runtime_error("KKT lambda search diverged");
          }
        }
        for (int round = 0; round < 64; ++round) {
          const double middle = (low + high) * 0.5;
          if (usage(middle) > input_.domains[domain].lanes) {
            low = middle;
          } else {
            high = middle;
          }
        }
        root_lambda = high;
      }
      for (int index = 0; index < static_cast<int>(domain_hops.size());
           ++index) {
        const int hop = domain_hops[index];
        continuous_[hop] = std::clamp(
            root_lambda / root_weights[index], 1.0,
            static_cast<double>(input_.max_ratio));
      }
    }
  }

  void update_path_multipliers(int iteration) {
    std::vector<double> costs(input_.paths.size());
    double maximum = -std::numeric_limits<double>::infinity();
    for (int path = 0; path < static_cast<int>(input_.paths.size()); ++path) {
      const double normalized =
          std::get<2>(path_metrics(input_.paths[path], continuous_));
      costs[path] = -normalized;
      maximum = std::max(maximum, costs[path]);
    }
    const double rate = 0.2 * std::pow(0.5, 0.01 * iteration);
    double total = 0.0;
    for (int path = 0; path < static_cast<int>(costs.size()); ++path) {
      const double exponent =
          std::clamp(8.0 * rate * (costs[path] - maximum), -60.0, 0.0);
      path_mu_[path] *= std::exp(exponent);
      path_mu_[path] = std::max(path_mu_[path], kEps);
      total += path_mu_[path];
    }
    for (double& value : path_mu_) {
      value /= total;
    }
  }

  std::vector<int> allowed_ratios() const {
    std::vector<int> result = {1};
    for (int ratio = input_.ratio_quantum; ratio <= input_.max_ratio;
         ratio += input_.ratio_quantum) {
      result.push_back(ratio);
    }
    return result;
  }

  int groups_for_bound(const std::vector<int>& ordered,
                       const std::vector<int>& allowed, double bound,
                       bool assign, int lane_offset = 0) {
    int position = 0;
    int lane = 0;
    while (position < static_cast<int>(ordered.size())) {
      const double continuous = continuous_[ordered[position]];
      int choice = -1;
      for (int ratio : allowed) {
        if (std::abs(ratio - continuous) <= bound + kEps) {
          choice = ratio;
        }
      }
      if (choice < 0) {
        return std::numeric_limits<int>::max();
      }
      const int end = std::min(
          static_cast<int>(ordered.size()), position + choice);
      int compatible_end = position + 1;
      while (compatible_end < end &&
             std::abs(continuous_[ordered[compatible_end]] - choice) <=
                 bound + kEps) {
        ++compatible_end;
      }
      if (assign) {
        for (int index = position; index < compatible_end; ++index) {
          discrete_[ordered[index]] = choice;
          lane_[ordered[index]] = lane_offset + lane;
        }
      }
      position = compatible_end;
      ++lane;
    }
    return lane;
  }

  bool exact_displacement_dp(
      const std::map<int, std::vector<int>>& ordered_by_direction,
      const std::vector<int>& allowed, double bound, int lane_budget) {
    std::vector<int> ordered;
    std::vector<int> direction;
    for (const auto& [direction_id, group] : ordered_by_direction) {
      for (int hop : group) {
        ordered.push_back(hop);
        direction.push_back(direction_id);
      }
    }
    // The TODAES 2020 DP is exact but quadratic in the signal count.  Use it
    // directly on compact domains, where it is also checked by exhaustive
    // enumeration, and keep the paper's minimum-wire greedy construction for
    // industrial-scale domains.
    constexpr int kExactDomainLimit = 256;
    if (ordered.empty() ||
        static_cast<int>(ordered.size()) > kExactDomainLimit) {
      return false;
    }

    const int count = static_cast<int>(ordered.size());
    const double infinity = std::numeric_limits<double>::infinity();
    std::vector<std::vector<double>> dp(
        count + 1, std::vector<double>(lane_budget + 1, infinity));
    struct Choice {
      int previous = -1;
      int ratio = -1;
    };
    std::vector<std::vector<Choice>> parent(
        count + 1, std::vector<Choice>(lane_budget + 1));
    dp[0][0] = 0.0;
    for (int position = 0; position < count; ++position) {
      for (int used = 0; used < lane_budget; ++used) {
        if (!std::isfinite(dp[position][used])) {
          continue;
        }
        for (int ratio : allowed) {
          if (std::abs(continuous_[ordered[position]] - ratio) >
              bound + kEps) {
            continue;
          }
          double displacement = 0.0;
          const int end_limit = std::min(count, position + ratio);
          for (int end = position; end < end_limit; ++end) {
            if (direction[end] != direction[position] ||
                std::abs(continuous_[ordered[end]] - ratio) >
                    bound + kEps) {
              break;
            }
            displacement +=
                std::abs(continuous_[ordered[end]] - ratio);
            const int next = end + 1;
            const double candidate =
                dp[position][used] + displacement;
            const Choice old = parent[next][used + 1];
            if (candidate + kEps < dp[next][used + 1] ||
                (std::abs(candidate - dp[next][used + 1]) <= kEps &&
                 (old.ratio < 0 || ratio < old.ratio))) {
              dp[next][used + 1] = candidate;
              parent[next][used + 1] = {position, ratio};
            }
          }
        }
      }
    }
    int best_lanes = -1;
    double best_cost = infinity;
    for (int used = 1; used <= lane_budget; ++used) {
      if (dp[count][used] + kEps < best_cost ||
          (std::abs(dp[count][used] - best_cost) <= kEps &&
           (best_lanes < 0 || used < best_lanes))) {
        best_cost = dp[count][used];
        best_lanes = used;
      }
    }
    if (best_lanes < 0 || !std::isfinite(best_cost)) {
      return false;
    }

    int position = count;
    int used = best_lanes;
    while (position > 0) {
      const Choice choice = parent[position][used];
      if (choice.previous < 0 || choice.ratio < 0) {
        throw std::runtime_error(
            "exact displacement DP has a broken parent chain");
      }
      for (int index = choice.previous; index < position; ++index) {
        discrete_[ordered[index]] = choice.ratio;
        lane_[ordered[index]] = used - 1;
      }
      position = choice.previous;
      --used;
    }
    ++dp_legalized_domains_;
    return true;
  }

  void legalize() {
    const std::vector<int> allowed = allowed_ratios();
    if (allowed.back() != input_.max_ratio) {
      throw std::runtime_error(
          "maximum ratio must be 1 or a multiple of ratio quantum");
    }
    for (int domain = 0; domain < static_cast<int>(input_.domains.size());
         ++domain) {
      std::map<int, std::vector<int>> ordered_by_direction;
      for (int hop = 0; hop < static_cast<int>(input_.hops.size()); ++hop) {
        if (input_.hops[hop].domain == domain) {
          ordered_by_direction[input_.hops[hop].direction].push_back(hop);
        }
      }
      int total_hops = 0;
      for (auto& [direction, ordered] : ordered_by_direction) {
        (void) direction;
        total_hops += ordered.size();
        std::stable_sort(
            ordered.begin(), ordered.end(), [&](int lhs, int rhs) {
              if (continuous_[lhs] != continuous_[rhs]) {
                return continuous_[lhs] < continuous_[rhs];
              }
              return lhs < rhs;
            });
      }
      if (total_hops >
          input_.domains[domain].lanes * input_.max_ratio) {
        throw std::runtime_error("domain cannot fit within maximum ratio");
      }
      auto group_count = [&](double bound) {
        int total = 0;
        for (const auto& [direction, ordered] : ordered_by_direction) {
          (void) direction;
          const int groups =
              groups_for_bound(ordered, allowed, bound, false);
          if (groups == std::numeric_limits<int>::max()) {
            return groups;
          }
          total += groups;
        }
        return total;
      };
      double low = 0.0;
      double high = input_.max_ratio;
      if (group_count(high) > input_.domains[domain].lanes) {
        throw std::runtime_error(
            "direction-separated groups cannot fit domain lane budget");
      }
      for (int round = 0; round < 64; ++round) {
        const double middle = (low + high) * 0.5;
        if (group_count(middle) <= input_.domains[domain].lanes) {
          high = middle;
        } else {
          low = middle;
        }
      }
      if (exact_displacement_dp(
              ordered_by_direction, allowed, high + 1.0e-8,
              input_.domains[domain].lanes)) {
        continue;
      }
      ++greedy_legalized_domains_;
      int lane_offset = 0;
      for (const auto& [direction, ordered] : ordered_by_direction) {
        (void) direction;
        const int groups = groups_for_bound(
            ordered, allowed, high, true, lane_offset);
        lane_offset += groups;
      }
      if (lane_offset > input_.domains[domain].lanes) {
        throw std::runtime_error("discrete legalization exceeded lane budget");
      }
    }
  }

  void post_refine() {
    std::vector<double> metrics(input_.paths.size());
    for (int path = 0; path < static_cast<int>(input_.paths.size()); ++path) {
      metrics[path] =
          std::get<2>(path_metrics_discrete(input_.paths[path]));
    }
    for (int iteration = 0;
         iteration < input_.post_refinement_iterations; ++iteration) {
      const int critical_path = static_cast<int>(
          std::min_element(metrics.begin(), metrics.end()) - metrics.begin());
      const double current_worst = metrics[critical_path];
      std::vector<int> critical_hops = input_.paths[critical_path].hops;
      std::stable_sort(
          critical_hops.begin(), critical_hops.end(), [&](int lhs, int rhs) {
            if (discrete_[lhs] != discrete_[rhs]) {
              return discrete_[lhs] > discrete_[rhs];
            }
            return lhs < rhs;
          });
      bool improved = false;
      for (int lhs : critical_hops) {
        std::vector<int> candidates;
        for (int rhs = 0; rhs < static_cast<int>(input_.hops.size()); ++rhs) {
          if (input_.hops[rhs].domain == input_.hops[lhs].domain &&
              input_.hops[rhs].direction == input_.hops[lhs].direction &&
              discrete_[rhs] < discrete_[lhs]) {
            candidates.push_back(rhs);
          }
        }
        std::stable_sort(
            candidates.begin(), candidates.end(), [&](int lhs_candidate,
                                                       int rhs_candidate) {
              if (discrete_[lhs_candidate] != discrete_[rhs_candidate]) {
                return discrete_[lhs_candidate] < discrete_[rhs_candidate];
              }
              return lhs_candidate < rhs_candidate;
            });
        for (int rhs : candidates) {
          std::set<int> affected(
              hop_paths_[lhs].begin(), hop_paths_[lhs].end());
          affected.insert(hop_paths_[rhs].begin(), hop_paths_[rhs].end());
          std::map<int, double> candidate_metrics;
          for (int path : affected) {
            candidate_metrics[path] = std::get<2>(
                path_metrics_swapped(input_.paths[path], lhs, rhs));
          }
          double candidate_worst = std::numeric_limits<double>::infinity();
          for (int path = 0; path < static_cast<int>(metrics.size()); ++path) {
            const auto found = candidate_metrics.find(path);
            candidate_worst = std::min(
                candidate_worst,
                found == candidate_metrics.end()
                    ? metrics[path]
                    : found->second);
          }
          if (candidate_worst > current_worst + input_.convergence) {
            std::swap(discrete_[lhs], discrete_[rhs]);
            std::swap(lane_[lhs], lane_[rhs]);
            for (const auto& [path, value] : candidate_metrics) {
              metrics[path] = value;
            }
            ++post_refinement_swaps_;
            improved = true;
            break;
          }
        }
        if (improved) {
          break;
        }
      }
      if (!improved) {
        break;
      }
    }
  }

  Input input_;
  std::vector<double> continuous_;
  std::vector<int> discrete_;
  std::vector<int> lane_;
  std::vector<double> path_mu_;
  std::vector<double> edge_mu_;
  std::vector<std::vector<int>> hop_paths_;
  std::vector<std::vector<int>> domain_hops_;
  int completed_iterations_ = 0;
  int post_refinement_swaps_ = 0;
  int dp_legalized_domains_ = 0;
  int greedy_legalized_domains_ = 0;
};

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
    Optimizer optimizer(read_input(argv[1]));
    optimizer.run();
    optimizer.write(argv[2]);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_tdm_ratio_optimizer: " << error.what() << '\n';
    return 1;
  }
}
