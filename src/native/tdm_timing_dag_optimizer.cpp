// SPDX-License-Identifier: Apache-2.0
//
// Continuous timing-DAG TDM-ratio optimization.  The core update implements
// Eqs. (8), (13), (16), (17), (19), and (20) of Chen et al., ASP-DAC 2026.

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr double kEpsilon = 1.0e-12;

struct Domain {
  double capacity = 0.0;
};

struct Hop {
  int domain = -1;
};

struct Edge {
  int from = -1;
  int to = -1;
  int hop = -1;
  double base_delay_ns = 0.0;
  double beta_ns = 0.0;
};

struct Path {
  int terminal_edge = -1;
};

struct Model {
  int max_iterations = 0;
  double min_ratio = 1.0;
  double max_ratio = 1.0;
  double convergence = 0.0;
  int source = 0;
  int sink = -1;
  std::vector<Domain> domains;
  std::vector<Hop> hops;
  std::vector<Edge> edges;
  std::vector<Path> paths;
  std::vector<std::vector<int>> incoming;
  std::vector<std::vector<int>> outgoing;
  std::vector<int> topological_order;
};

struct Evaluation {
  std::vector<double> arrival;
  std::vector<double> edge_delay;
  double sink_arrival = 0.0;
};

struct DualState {
  std::vector<double> delay_cost;
  std::vector<double> node_delay_cost;
  std::vector<double> edge_mu;
  double maximum_conservation_error = 0.0;
};

struct DomainState {
  std::vector<double> lambda;
  std::vector<double> usage;
  int residual_scalings = 0;
};

struct Result {
  std::vector<double> ratios;
  std::vector<double> edge_mu;
  std::vector<double> path_mu;
  std::vector<double> lambda;
  std::vector<double> usage;
  int iterations = 0;
  int residual_scalings = 0;
  double sink_arrival_ns = 0.0;
  double maximum_conservation_error = 0.0;
  double maximum_capacity_error = 0.0;
};

void require_end(std::stringstream& record, const std::string& line) {
  std::string trailing;
  if (record >> trailing) {
    throw std::runtime_error("trailing input fields: " + line);
  }
  if (!record.eof()) {
    throw std::runtime_error("malformed input record: " + line);
  }
}

Model read_model(const std::string& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open input: " + path);
  std::string line;
  std::getline(input, line);
  if (line != "EMUFLOW_TDM_TIMING_DAG_INPUT_V1") {
    throw std::runtime_error("invalid input header");
  }
  Model model;
  bool have_parameters = false;
  while (std::getline(input, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::stringstream record(line);
    std::string kind;
    record >> kind;
    if (kind == "PARAM") {
      record >> model.max_iterations >> model.min_ratio >> model.max_ratio >>
          model.convergence >> model.source >> model.sink;
      have_parameters = true;
    } else if (kind == "DOMAIN") {
      int index = -1;
      Domain domain;
      record >> index >> domain.capacity;
      if (index != static_cast<int>(model.domains.size())) {
        throw std::runtime_error("DOMAIN indices must be contiguous");
      }
      model.domains.push_back(domain);
    } else if (kind == "HOP") {
      int index = -1;
      Hop hop;
      record >> index >> hop.domain;
      if (index != static_cast<int>(model.hops.size())) {
        throw std::runtime_error("HOP indices must be contiguous");
      }
      model.hops.push_back(hop);
    } else if (kind == "EDGE") {
      int index = -1;
      Edge edge;
      record >> index >> edge.from >> edge.to >> edge.hop >>
          edge.base_delay_ns >> edge.beta_ns;
      if (index != static_cast<int>(model.edges.size())) {
        throw std::runtime_error("EDGE indices must be contiguous");
      }
      model.edges.push_back(edge);
    } else if (kind == "PATH") {
      int index = -1;
      Path timing_path;
      record >> index >> timing_path.terminal_edge;
      if (index != static_cast<int>(model.paths.size())) {
        throw std::runtime_error("PATH indices must be contiguous");
      }
      model.paths.push_back(timing_path);
    } else {
      throw std::runtime_error("unknown input record: " + kind);
    }
    if (!record) throw std::runtime_error("malformed input record: " + line);
    require_end(record, line);
  }
  if (!have_parameters || model.max_iterations <= 0 ||
      model.min_ratio < 1.0 || model.max_ratio < model.min_ratio ||
      model.convergence <= 0.0 ||
      model.domains.empty() || model.hops.empty() || model.edges.empty() ||
      model.paths.empty() || model.source < 0 || model.sink < 0 ||
      model.source == model.sink) {
    throw std::runtime_error("incomplete timing-DAG input");
  }
  int maximum_node = std::max(model.source, model.sink);
  for (const auto& domain : model.domains) {
    if (!std::isfinite(domain.capacity) || domain.capacity <= 0.0) {
      throw std::runtime_error("invalid domain capacity");
    }
  }
  for (const auto& hop : model.hops) {
    if (hop.domain < 0 ||
        hop.domain >= static_cast<int>(model.domains.size())) {
      throw std::runtime_error("invalid hop domain");
    }
  }
  for (const auto& edge : model.edges) {
    maximum_node = std::max({maximum_node, edge.from, edge.to});
    if (edge.from < 0 || edge.to < 0 || edge.from == edge.to ||
        !std::isfinite(edge.base_delay_ns) || edge.base_delay_ns < 0.0 ||
        !std::isfinite(edge.beta_ns) || edge.beta_ns < 0.0 ||
        edge.hop < -1 || edge.hop >= static_cast<int>(model.hops.size()) ||
        (edge.hop < 0 && edge.beta_ns != 0.0) ||
        (edge.hop >= 0 && edge.beta_ns <= 0.0)) {
      throw std::runtime_error("invalid timing-DAG edge");
    }
  }
  model.incoming.assign(maximum_node + 1, {});
  model.outgoing.assign(maximum_node + 1, {});
  std::vector<int> indegree(maximum_node + 1, 0);
  for (int index = 0; index < static_cast<int>(model.edges.size()); ++index) {
    const auto& edge = model.edges[index];
    model.outgoing[edge.from].push_back(index);
    model.incoming[edge.to].push_back(index);
    ++indegree[edge.to];
  }
  std::priority_queue<int, std::vector<int>, std::greater<int>> ready;
  for (int node = 0; node <= maximum_node; ++node) {
    if (indegree[node] == 0) ready.push(node);
  }
  while (!ready.empty()) {
    const int node = ready.top();
    ready.pop();
    model.topological_order.push_back(node);
    for (int edge_index : model.outgoing[node]) {
      const int sink = model.edges[edge_index].to;
      if (--indegree[sink] == 0) ready.push(sink);
    }
  }
  if (model.topological_order.size() != model.incoming.size()) {
    throw std::runtime_error("timing graph is cyclic");
  }
  if (!model.incoming[model.source].empty() ||
      !model.outgoing[model.sink].empty() ||
      model.outgoing[model.source].empty() ||
      model.incoming[model.sink].empty()) {
    throw std::runtime_error("source/sink topology is invalid");
  }
  std::vector<unsigned char> terminal_edges(model.edges.size(), 0);
  for (const auto& timing_path : model.paths) {
    if (timing_path.terminal_edge < 0 ||
        timing_path.terminal_edge >= static_cast<int>(model.edges.size()) ||
        model.edges[timing_path.terminal_edge].to != model.sink ||
        terminal_edges[timing_path.terminal_edge]) {
      throw std::runtime_error("invalid or duplicate PATH terminal edge");
    }
    terminal_edges[timing_path.terminal_edge] = 1;
  }
  return model;
}

double edge_delay(const Edge& edge, const std::vector<double>& ratios) {
  return edge.base_delay_ns +
      (edge.hop >= 0 ? edge.beta_ns * (ratios[edge.hop] - 1.0) : 0.0);
}

Evaluation evaluate(const Model& model, const std::vector<double>& ratios) {
  Evaluation result;
  result.arrival.assign(model.incoming.size(),
                        -std::numeric_limits<double>::infinity());
  result.edge_delay.resize(model.edges.size());
  result.arrival[model.source] = 0.0;
  for (int node : model.topological_order) {
    if (!std::isfinite(result.arrival[node])) continue;
    for (int edge_index : model.outgoing[node]) {
      const auto& edge = model.edges[edge_index];
      const double delay = edge_delay(edge, ratios);
      result.edge_delay[edge_index] = delay;
      result.arrival[edge.to] =
          std::max(result.arrival[edge.to], result.arrival[node] + delay);
    }
  }
  if (!std::isfinite(result.arrival[model.sink])) {
    throw std::runtime_error("timing-DAG sink is unreachable");
  }
  result.sink_arrival = result.arrival[model.sink];
  return result;
}

DualState backward_delay_cost_flow(const Model& model,
                                   const Evaluation& evaluation,
                                   const std::vector<double>& path_mu) {
  DualState result;
  result.delay_cost.assign(model.edges.size(), 0.0);
  result.node_delay_cost.assign(model.incoming.size(), 0.0);
  result.edge_mu.assign(model.edges.size(), 0.0);
  for (int node : model.topological_order) {
    const double split = model.outgoing[node].empty()
        ? 0.0
        : result.node_delay_cost[node] / model.outgoing[node].size();
    for (int edge_index : model.outgoing[node]) {
      const auto& edge = model.edges[edge_index];
      const double cost = split + evaluation.edge_delay[edge_index];
      result.delay_cost[edge_index] = std::max(cost, kEpsilon);
      result.node_delay_cost[edge.to] += result.delay_cost[edge_index];
    }
  }
  if (path_mu.size() != model.paths.size()) {
    throw std::runtime_error("path multiplier coverage is incomplete");
  }
  for (int path = 0; path < static_cast<int>(model.paths.size()); ++path) {
    result.edge_mu[model.paths[path].terminal_edge] = path_mu[path];
  }
  for (auto position = model.topological_order.rbegin();
       position != model.topological_order.rend(); ++position) {
    const int node = *position;
    if (node == model.sink) continue;
    const double outflow = std::accumulate(
        model.outgoing[node].begin(), model.outgoing[node].end(), 0.0,
        [&](double sum, int edge) { return sum + result.edge_mu[edge]; });
    if (model.incoming[node].empty()) continue;
    const double denominator = result.node_delay_cost[node];
    if (denominator <= 0.0) {
      const double share = outflow / model.incoming[node].size();
      for (int edge : model.incoming[node]) result.edge_mu[edge] = share;
    } else {
      for (int edge : model.incoming[node]) {
        result.edge_mu[edge] = outflow * result.delay_cost[edge] / denominator;
      }
    }
  }
  for (int node : model.topological_order) {
    if (node == model.source || node == model.sink) continue;
    double incoming = 0.0;
    double outgoing = 0.0;
    for (int edge : model.incoming[node]) incoming += result.edge_mu[edge];
    for (int edge : model.outgoing[node]) outgoing += result.edge_mu[edge];
    result.maximum_conservation_error = std::max(
        result.maximum_conservation_error, std::abs(incoming - outgoing));
  }
  return result;
}

double domain_usage(const Model& model, const std::vector<double>& ratios,
                    int domain) {
  double usage = 0.0;
  for (int hop = 0; hop < static_cast<int>(model.hops.size()); ++hop) {
    if (model.hops[hop].domain == domain) usage += 1.0 / ratios[hop];
  }
  return usage;
}

void residual_capacity_scale(const Model& model, int domain,
                             std::vector<double>& ratios,
                             DomainState& state) {
  const double capacity = model.domains[domain].capacity;
  for (int pass = 0; pass < static_cast<int>(model.hops.size()); ++pass) {
    std::vector<int> active;
    int fixed = 0;
    double active_usage = 0.0;
    for (int hop = 0; hop < static_cast<int>(model.hops.size()); ++hop) {
      if (model.hops[hop].domain != domain) continue;
      if (ratios[hop] <= model.min_ratio + 1.0e-10) {
        ratios[hop] = model.min_ratio;
        ++fixed;
      } else {
        active.push_back(hop);
        active_usage += 1.0 / ratios[hop];
      }
    }
    const double residual = capacity - fixed / model.min_ratio;
    if (active.empty() || residual <= 0.0 ||
        fixed / model.min_ratio + active_usage >=
            capacity - 1.0e-10) {
      break;
    }
    const double gamma = active_usage / residual;
    bool saturated = false;
    for (int hop : active) {
      ratios[hop] *= gamma;
      if (ratios[hop] <= model.min_ratio) {
        ratios[hop] = model.min_ratio;
        saturated = true;
      }
    }
    ++state.residual_scalings;
    if (!saturated) break;
  }
}

DomainState update_ratios(const Model& model, const DualState& dual,
                          std::vector<double>& ratios) {
  DomainState state;
  state.lambda.assign(model.domains.size(), 0.0);
  state.usage.assign(model.domains.size(), 0.0);
  std::vector<double> weights(model.hops.size(), 0.0);
  for (int edge = 0; edge < static_cast<int>(model.edges.size()); ++edge) {
    const int hop = model.edges[edge].hop;
    if (hop >= 0) {
      weights[hop] += model.edges[edge].beta_ns * dual.edge_mu[edge];
    }
  }
  for (int domain = 0; domain < static_cast<int>(model.domains.size());
       ++domain) {
    std::vector<int> members;
    double minimum_usage = 0.0;
    double maximum_usage = 0.0;
    double root_sum = 0.0;
    for (int hop = 0; hop < static_cast<int>(model.hops.size()); ++hop) {
      if (model.hops[hop].domain != domain) continue;
      members.push_back(hop);
      minimum_usage += 1.0 / model.max_ratio;
      maximum_usage += 1.0 / model.min_ratio;
      root_sum += std::sqrt(std::max(weights[hop], kEpsilon));
    }
    const double capacity = model.domains[domain].capacity;
    if (minimum_usage > capacity + 1.0e-10) {
      throw std::runtime_error("domain is infeasible at maximum ratio");
    }
    if (maximum_usage <= capacity + 1.0e-10) {
      for (int hop : members) ratios[hop] = model.min_ratio;
      state.lambda[domain] = 0.0;
      state.usage[domain] = maximum_usage;
      continue;
    }
    double lambda = std::pow(root_sum / capacity, 2.0);
    auto usage_for_lambda = [&](double candidate) {
      double usage = 0.0;
      for (int hop : members) {
        const double ratio = std::clamp(
            std::sqrt(candidate / std::max(weights[hop], kEpsilon)),
            model.min_ratio, model.max_ratio);
        usage += 1.0 / ratio;
      }
      return usage;
    };
    if (usage_for_lambda(lambda) > capacity + 1.0e-10) {
      double low = lambda;
      double high = std::max(1.0, lambda);
      while (usage_for_lambda(high) > capacity) high *= 2.0;
      for (int iteration = 0; iteration < 80; ++iteration) {
        const double middle = (low + high) * 0.5;
        if (usage_for_lambda(middle) > capacity) {
          low = middle;
        } else {
          high = middle;
        }
      }
      lambda = high;
    }
    state.lambda[domain] = lambda;
    for (int hop : members) {
      ratios[hop] = std::clamp(
          std::sqrt(lambda / std::max(weights[hop], kEpsilon)),
          model.min_ratio, model.max_ratio);
    }
    residual_capacity_scale(model, domain, ratios, state);
    state.usage[domain] = domain_usage(model, ratios, domain);
  }
  return state;
}

Result optimize(const Model& model) {
  std::vector<double> ratios(model.hops.size(), model.min_ratio);
  std::vector<double> best_ratios = ratios;
  std::vector<double> path_mu(model.paths.size(), 1.0 / model.paths.size());
  std::vector<double> best_path_mu = path_mu;
  double best_arrival = std::numeric_limits<double>::infinity();
  int completed_iterations = 0;
  int residual_scalings = 0;
  for (int iteration = 0; iteration < model.max_iterations; ++iteration) {
    const Evaluation before = evaluate(model, ratios);
    const DualState dual =
        backward_delay_cost_flow(model, before, path_mu);
    const std::vector<double> previous = ratios;
    DomainState domains = update_ratios(model, dual, ratios);
    residual_scalings += domains.residual_scalings;
    const Evaluation after = evaluate(model, ratios);
    if (after.sink_arrival < best_arrival) {
      best_arrival = after.sink_arrival;
      best_ratios = ratios;
      best_path_mu = path_mu;
    }
    std::vector<double> costs(model.paths.size(), 0.0);
    double maximum = -std::numeric_limits<double>::infinity();
    double minimum = std::numeric_limits<double>::infinity();
    for (int path = 0; path < static_cast<int>(model.paths.size()); ++path) {
      const int edge_index = model.paths[path].terminal_edge;
      const auto& edge = model.edges[edge_index];
      costs[path] = after.arrival[edge.from] + after.edge_delay[edge_index];
      maximum = std::max(maximum, costs[path]);
      minimum = std::min(minimum, costs[path]);
    }
    const double rate = 0.2 * std::pow(0.5, 0.01 * iteration);
    const double range = std::max(1.0, maximum - minimum);
    double multiplier_sum = 0.0;
    for (int path = 0; path < static_cast<int>(model.paths.size()); ++path) {
      const double exponent = std::clamp(
          8.0 * rate * (costs[path] - maximum) / range, -60.0, 0.0);
      path_mu[path] =
          std::max(kEpsilon, path_mu[path] * std::exp(exponent));
      multiplier_sum += path_mu[path];
    }
    for (double& multiplier : path_mu) multiplier /= multiplier_sum;
    double maximum_change = 0.0;
    for (int hop = 0; hop < static_cast<int>(ratios.size()); ++hop) {
      maximum_change =
          std::max(maximum_change, std::abs(ratios[hop] - previous[hop]));
    }
    completed_iterations = iteration + 1;
    if (maximum_change <= model.convergence) break;
  }
  const Evaluation final_evaluation = evaluate(model, best_ratios);
  const DualState final_dual = backward_delay_cost_flow(
      model, final_evaluation, best_path_mu);
  std::vector<double> final_ratios = best_ratios;
  DomainState final_domains =
      update_ratios(model, final_dual, final_ratios);
  const Evaluation updated_evaluation = evaluate(model, final_ratios);
  if (updated_evaluation.sink_arrival <= final_evaluation.sink_arrival) {
    best_ratios = final_ratios;
  }
  const Evaluation reported = evaluate(model, best_ratios);
  const DualState reported_dual =
      backward_delay_cost_flow(model, reported, best_path_mu);
  std::vector<double> report_copy = best_ratios;
  DomainState reported_domains =
      update_ratios(model, reported_dual, report_copy);
  Result result;
  result.ratios = std::move(best_ratios);
  result.edge_mu = reported_dual.edge_mu;
  result.path_mu = best_path_mu;
  result.lambda = reported_domains.lambda;
  result.iterations = completed_iterations;
  result.residual_scalings =
      residual_scalings + final_domains.residual_scalings;
  result.sink_arrival_ns = reported.sink_arrival;
  result.maximum_conservation_error =
      reported_dual.maximum_conservation_error;
  result.usage.resize(model.domains.size(), 0.0);
  for (int domain = 0; domain < static_cast<int>(model.domains.size());
       ++domain) {
    result.usage[domain] = domain_usage(model, result.ratios, domain);
    result.maximum_capacity_error = std::max(
        result.maximum_capacity_error,
        std::max(0.0, result.usage[domain] - model.domains[domain].capacity));
  }
  return result;
}

void write_result(const std::string& path, const Result& result) {
  std::ofstream output(path);
  if (!output) throw std::runtime_error("cannot open output: " + path);
  output << "EMUFLOW_TDM_TIMING_DAG_OUTPUT_V1\n" << std::setprecision(17);
  for (int hop = 0; hop < static_cast<int>(result.ratios.size()); ++hop) {
    output << "HOP " << hop << ' ' << result.ratios[hop] << '\n';
  }
  for (int edge = 0; edge < static_cast<int>(result.edge_mu.size()); ++edge) {
    output << "EDGE " << edge << ' ' << result.edge_mu[edge] << '\n';
  }
  for (int path_index = 0;
       path_index < static_cast<int>(result.path_mu.size()); ++path_index) {
    output << "PATH " << path_index << ' ' << result.path_mu[path_index]
           << '\n';
  }
  for (int domain = 0; domain < static_cast<int>(result.lambda.size());
       ++domain) {
    output << "DOMAIN " << domain << ' ' << result.lambda[domain] << ' '
           << result.usage[domain] << '\n';
  }
  output << "METRIC iterations " << result.iterations << '\n';
  output << "METRIC residual_scalings " << result.residual_scalings << '\n';
  output << "METRIC sink_arrival_ns " << result.sink_arrival_ns << '\n';
  output << "METRIC max_flow_conservation_error "
         << result.maximum_conservation_error << '\n';
  output << "METRIC max_capacity_error "
         << result.maximum_capacity_error << '\n';
}

void print_help() {
  std::cout << "Usage: emuflow_tdm_timing_dag_optimizer INPUT OUTPUT\n"
            << "Continuous timing-DAG TDM optimization using ASP-DAC 2026 "
               "Eqs. 8, 13, 16, 17, 19, and 20.\n";
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
    const Result result = optimize(model);
    write_result(argv[2], result);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_tdm_timing_dag_optimizer: " << error.what() << '\n';
    return 1;
  }
}
