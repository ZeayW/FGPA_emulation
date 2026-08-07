#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {

struct Hop {
  int index = -1;
  int round = 0;
  int domain = -1;
  int lane = -1;
  int ratio = 1;
  int latency = 0;
  int parent = -1;
  int priority = -1;
  double base_ns = 0.0;
  double beta_ns = 0.0;
};

struct Sink {
  int route = -1;
  int round = 0;
  int hop = -1;
};

struct TimingPath {
  int index = -1;
  double period_ns = 0.0;
  double fixed_ns = 0.0;
  std::vector<int> hops;
};

struct Model {
  int frame_slots = 0;
  int runtime_barrier_slots = 1;
  int settle_slots = 1;
  int max_iterations = 0;
  int planned_round_one_ready = -1;
  double positive_scale_ns = 1.0;
  double negative_scale_ns = 1.0;
  double maximum_period_ns = 1.0;
  std::vector<Hop> hops;
  std::vector<Sink> sinks;
  std::vector<TimingPath> paths;
};

struct Schedule {
  bool feasible = false;
  std::vector<int> slots;
  std::vector<int> ready;
  std::vector<int> round_ready;
  std::vector<int> route_completion;
  double worst_normalized_slack = -std::numeric_limits<double>::infinity();
  int worst_path = -1;
  int completion_slot = std::numeric_limits<int>::max();
  long long total_wait_slots = std::numeric_limits<long long>::max();
};

std::vector<int> parse_indices(const std::string& value) {
  if (value == "-") return {};
  std::vector<int> result;
  std::stringstream stream(value);
  std::string item;
  while (std::getline(stream, item, ',')) {
    if (item.empty()) throw std::runtime_error("empty path hop index");
    result.push_back(std::stoi(item));
  }
  return result;
}

Model read_model(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open input");
  std::string header;
  std::getline(input, header);
  if (header != "EMUFLOW_TDM_SLOT_INPUT_V1") {
    throw std::runtime_error("invalid input header");
  }
  Model model;
  std::string line;
  bool have_parameters = false;
  while (std::getline(input, line)) {
    if (line.empty()) continue;
    std::stringstream fields(line);
    std::string kind;
    fields >> kind;
    if (kind == "PARAM") {
      fields >> model.frame_slots >> model.runtime_barrier_slots >>
          model.settle_slots >> model.max_iterations >>
          model.planned_round_one_ready >> model.positive_scale_ns >>
          model.negative_scale_ns >> model.maximum_period_ns;
      have_parameters = true;
    } else if (kind == "HOP") {
      Hop hop;
      fields >> hop.index >> hop.round >> hop.domain >> hop.lane >>
          hop.ratio >> hop.latency >> hop.parent >> hop.priority >>
          hop.base_ns >> hop.beta_ns;
      model.hops.push_back(hop);
    } else if (kind == "SINK") {
      Sink sink;
      fields >> sink.route >> sink.round >> sink.hop;
      model.sinks.push_back(sink);
    } else if (kind == "PATH") {
      TimingPath timing_path;
      std::string hops;
      fields >> timing_path.index >> timing_path.period_ns >>
          timing_path.fixed_ns >> hops;
      timing_path.hops = parse_indices(hops);
      model.paths.push_back(std::move(timing_path));
    } else {
      throw std::runtime_error("invalid input record: " + line);
    }
    std::string trailing;
    if (fields >> trailing) {
      throw std::runtime_error("trailing input fields: " + line);
    }
    if (!fields.eof()) throw std::runtime_error("malformed input: " + line);
  }
  if (!have_parameters || model.frame_slots <= 0 ||
      model.runtime_barrier_slots < 0 || model.settle_slots < 0 ||
      model.max_iterations < 0 || model.positive_scale_ns <= 0.0 ||
      model.negative_scale_ns <= 0.0 || model.maximum_period_ns <= 0.0) {
    throw std::runtime_error("invalid parameters");
  }
  std::sort(model.hops.begin(), model.hops.end(),
            [](const Hop& left, const Hop& right) {
              return left.index < right.index;
            });
  std::sort(model.paths.begin(), model.paths.end(),
            [](const TimingPath& left, const TimingPath& right) {
              return left.index < right.index;
            });
  for (int index = 0; index < static_cast<int>(model.hops.size()); ++index) {
    const auto& hop = model.hops[index];
    if (hop.index != index || hop.round < 0 || hop.domain < 0 ||
        hop.lane < 0 || hop.ratio <= 0 || hop.latency < 0 ||
        hop.priority < 0 || hop.parent >= static_cast<int>(model.hops.size()) ||
        (hop.parent >= 0 && model.hops[hop.parent].round != hop.round)) {
      throw std::runtime_error("invalid hop record");
    }
  }
  std::set<int> priorities;
  for (const auto& hop : model.hops) priorities.insert(hop.priority);
  if (priorities.size() != model.hops.size()) {
    throw std::runtime_error("hop priorities are not unique");
  }
  for (int index = 0; index < static_cast<int>(model.paths.size()); ++index) {
    if (model.paths[index].index != index ||
        model.paths[index].period_ns <= 0.0 ||
        model.paths[index].fixed_ns < 0.0) {
      throw std::runtime_error("invalid timing path record");
    }
    for (int hop : model.paths[index].hops) {
      if (hop < 0 || hop >= static_cast<int>(model.hops.size())) {
        throw std::runtime_error("timing path hop is out of range");
      }
    }
  }
  if (model.hops.empty() || model.sinks.empty() || model.paths.empty()) {
    throw std::runtime_error("input model is empty");
  }
  return model;
}

double normalized_slack(const Model& model, double period, double slack) {
  if (slack >= 0.0) {
    return slack * period /
           (model.positive_scale_ns * model.maximum_period_ns);
  }
  return slack / (model.negative_scale_ns * period);
}

Schedule build_schedule(const Model& model, const std::vector<int>& priority) {
  Schedule result;
  const int hop_count = static_cast<int>(model.hops.size());
  int maximum_round = 0;
  int maximum_domain = 0;
  int maximum_lane = 0;
  int maximum_route = 0;
  for (const auto& hop : model.hops) {
    maximum_round = std::max(maximum_round, hop.round);
    maximum_domain = std::max(maximum_domain, hop.domain);
    maximum_lane = std::max(maximum_lane, hop.lane);
  }
  for (const auto& sink : model.sinks) {
    maximum_route = std::max(maximum_route, sink.route);
  }
  result.slots.assign(hop_count, -1);
  result.ready.assign(hop_count, -1);
  result.round_ready.assign(maximum_round + 1, -1);
  result.route_completion.assign(maximum_route + 1, -1);
  std::vector<std::vector<int>> children(hop_count);
  for (const auto& hop : model.hops) {
    if (hop.parent >= 0) children[hop.parent].push_back(hop.index);
  }
  const int lane_stride = maximum_lane + 1;
  const int lane_domains = (maximum_domain + 1) * lane_stride;
  std::vector<std::vector<unsigned char>> occupied(
      lane_domains, std::vector<unsigned char>(model.frame_slots, 0));

  for (int round = 0; round <= maximum_round; ++round) {
    int source_ready = 0;
    if (round > 0) {
      int prior_completion = -1;
      for (const auto& sink : model.sinks) {
        if (sink.round < round) {
          prior_completion =
              std::max(prior_completion, result.route_completion[sink.route]);
        }
      }
      if (prior_completion < 0) return result;
      source_ready = prior_completion + model.settle_slots;
    }
    if (round == 1 && model.planned_round_one_ready >= 0 &&
        source_ready > model.planned_round_one_ready) {
      return result;
    }
    result.round_ready[round] = source_ready;
    auto compare = [&](int left, int right) {
      return std::tie(priority[left], left) >
             std::tie(priority[right], right);
    };
    std::priority_queue<int, std::vector<int>, decltype(compare)> ready_queue(
        compare);
    int expected = 0;
    for (const auto& hop : model.hops) {
      if (hop.round != round) continue;
      ++expected;
      if (hop.parent < 0) ready_queue.push(hop.index);
    }
    int scheduled = 0;
    while (!ready_queue.empty()) {
      const int index = ready_queue.top();
      ready_queue.pop();
      const auto& hop = model.hops[index];
      const int ready =
          hop.parent < 0
              ? source_ready
              : result.slots[hop.parent] + model.hops[hop.parent].latency +
                    model.settle_slots;
      const int latest = std::min(
          ready + hop.ratio,
          model.frame_slots - model.runtime_barrier_slots - hop.latency);
      const int lane_domain = hop.domain * lane_stride + hop.lane;
      int slot = ready;
      while (slot < latest && occupied[lane_domain][slot]) ++slot;
      if (slot >= latest) return result;
      occupied[lane_domain][slot] = 1;
      result.slots[index] = slot;
      result.ready[index] = ready;
      ++scheduled;
      for (int child : children[index]) ready_queue.push(child);
    }
    if (scheduled != expected) return result;
    for (const auto& sink : model.sinks) {
      if (sink.round != round) continue;
      const int arrival = result.slots[sink.hop] + model.hops[sink.hop].latency;
      result.route_completion[sink.route] =
          std::max(result.route_completion[sink.route], arrival);
    }
  }

  result.worst_normalized_slack = std::numeric_limits<double>::infinity();
  for (const auto& path : model.paths) {
    double delay = path.fixed_ns;
    for (int index : path.hops) {
      const auto& hop = model.hops[index];
      delay += hop.base_ns +
               hop.beta_ns * (result.slots[index] - result.ready[index]);
    }
    const double slack = path.period_ns - delay;
    const double normalized = normalized_slack(model, path.period_ns, slack);
    if (normalized < result.worst_normalized_slack) {
      result.worst_normalized_slack = normalized;
      result.worst_path = path.index;
    }
  }
  result.completion_slot = *std::max_element(result.route_completion.begin(),
                                              result.route_completion.end());
  result.total_wait_slots = 0;
  for (int index = 0; index < hop_count; ++index) {
    result.total_wait_slots += result.slots[index] - result.ready[index];
  }
  result.feasible = true;
  return result;
}

bool better(const Schedule& left, const Schedule& right) {
  constexpr double epsilon = 1.0e-12;
  if (left.worst_normalized_slack >
      right.worst_normalized_slack + epsilon) {
    return true;
  }
  if (left.worst_normalized_slack + epsilon <
      right.worst_normalized_slack) {
    return false;
  }
  if (left.completion_slot != right.completion_slot) {
    return left.completion_slot < right.completion_slot;
  }
  if (left.total_wait_slots != right.total_wait_slots) {
    return left.total_wait_slots < right.total_wait_slots;
  }
  return left.slots < right.slots;
}

struct OptimizationResult {
  Schedule schedule;
  int iterations = 0;
  int accepted_moves = 0;
  int evaluated_moves = 0;
};

OptimizationResult optimize(const Model& model) {
  std::vector<int> priority(model.hops.size());
  for (const auto& hop : model.hops) priority[hop.index] = hop.priority;
  OptimizationResult result;
  result.schedule = build_schedule(model, priority);
  if (!result.schedule.feasible) {
    throw std::runtime_error("initial list schedule is infeasible");
  }
  for (int iteration = 0; iteration < model.max_iterations; ++iteration) {
    result.iterations = iteration + 1;
    if (result.schedule.worst_path < 0) break;
    std::set<std::pair<int, int>> candidates;
    const auto& worst = model.paths[result.schedule.worst_path];
    for (int critical : worst.hops) {
      if (result.schedule.slots[critical] <= result.schedule.ready[critical]) {
        continue;
      }
      const auto& critical_hop = model.hops[critical];
      int nearest = -1;
      int nearest_slot = -1;
      for (const auto& other : model.hops) {
        if (other.index == critical || other.round != critical_hop.round ||
            other.domain != critical_hop.domain ||
            other.lane != critical_hop.lane ||
            result.schedule.slots[other.index] >=
                result.schedule.slots[critical]) {
          continue;
        }
        if (result.schedule.slots[other.index] > nearest_slot) {
          nearest = other.index;
          nearest_slot = result.schedule.slots[other.index];
        }
      }
      if (nearest >= 0) candidates.insert(std::minmax(critical, nearest));
    }
    Schedule best = result.schedule;
    std::pair<int, int> best_move{-1, -1};
    for (const auto& move : candidates) {
      std::swap(priority[move.first], priority[move.second]);
      Schedule candidate = build_schedule(model, priority);
      ++result.evaluated_moves;
      std::swap(priority[move.first], priority[move.second]);
      if (candidate.feasible && better(candidate, best)) {
        best = std::move(candidate);
        best_move = move;
      }
    }
    if (best_move.first < 0) break;
    std::swap(priority[best_move.first], priority[best_move.second]);
    result.schedule = std::move(best);
    ++result.accepted_moves;
  }
  return result;
}

void write_result(const std::string& path, const OptimizationResult& result) {
  std::ofstream output(path);
  if (!output) throw std::runtime_error("cannot open output");
  output << "EMUFLOW_TDM_SLOT_OUTPUT_V1\n";
  for (int index = 0; index < static_cast<int>(result.schedule.slots.size());
       ++index) {
    output << "HOP " << index << ' ' << result.schedule.slots[index] << ' '
           << result.schedule.ready[index] << '\n';
  }
  output << "METRIC iterations " << result.iterations << '\n';
  output << "METRIC accepted_moves " << result.accepted_moves << '\n';
  output << "METRIC evaluated_moves " << result.evaluated_moves << '\n';
  output << std::setprecision(17);
  output << "METRIC worst_normalized_slack "
         << result.schedule.worst_normalized_slack << '\n';
  output << "METRIC completion_slot " << result.schedule.completion_slot
         << '\n';
  output << "METRIC total_wait_slots "
         << result.schedule.total_wait_slots << '\n';
}

void print_help() {
  std::cout << "Usage: emuflow_tdm_slot_optimizer INPUT OUTPUT\n"
            << "Timing-path-guided local search for a fixed TDM ratio/lane "
               "plan.\n";
}

}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc == 2 && std::string(argv[1]) == "--help") {
      print_help();
      return 0;
    }
    if (argc != 3) {
      print_help();
      return 2;
    }
    const Model model = read_model(argv[1]);
    const OptimizationResult result = optimize(model);
    write_result(argv[2], result);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_tdm_slot_optimizer: " << error.what() << '\n';
    return 1;
  }
}
