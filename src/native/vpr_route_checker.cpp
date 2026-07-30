#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

struct RrNode {
  bool present = false;
  unsigned capacity = 0;
  std::string type;
  int xlow = 0;
  int ylow = 0;
  int xhigh = 0;
  int yhigh = 0;
  int layer_low = 0;
  int layer_high = 0;
  int ptc = 0;
};

struct Edge {
  std::uint32_t src = 0;
  std::uint32_t sink = 0;
  std::uint32_t sw = 0;

  bool operator==(const Edge& other) const {
    return src == other.src && sink == other.sink && sw == other.sw;
  }
};

struct EdgeHash {
  std::size_t operator()(const Edge& edge) const {
    std::uint64_t pair =
        (static_cast<std::uint64_t>(edge.src) << 32) | edge.sink;
    pair ^= static_cast<std::uint64_t>(edge.sw) * 0x9e3779b97f4a7c15ULL;
    pair ^= pair >> 30;
    pair *= 0xbf58476d1ce4e5b9ULL;
    pair ^= pair >> 27;
    return static_cast<std::size_t>(pair ^ (pair >> 31));
  }
};

struct RrGraph {
  std::vector<RrNode> nodes;
  std::unordered_set<Edge, EdgeHash> edges;
  std::size_t grid_width = 0;
  std::size_t grid_height = 0;
};

struct RouteNet {
  unsigned id = 0;
  std::string name;
  bool global = false;
  bool local_only = false;
  std::size_t nodes = 0;
  std::size_t sinks = 0;
  std::size_t endpoints = 0;
};

struct RouteSummary {
  std::string placement_file;
  std::string placement_id;
  std::size_t width = 0;
  std::size_t height = 0;
  std::size_t route_nodes = 0;
  std::size_t route_edges = 0;
  std::size_t branch_restarts = 0;
  unsigned max_occupancy = 0;
  unsigned max_capacity = 0;
  std::vector<RouteNet> nets;
};

std::string trim(const std::string& value) {
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) return {};
  const auto last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}

std::string attribute(const std::string& line, const std::string& name,
                      bool required = true) {
  const std::string marker = name + "=\"";
  const auto begin = line.find(marker);
  if (begin == std::string::npos) {
    if (!required) return {};
    throw std::runtime_error("missing XML attribute " + name);
  }
  const auto value_begin = begin + marker.size();
  const auto end = line.find('"', value_begin);
  if (end == std::string::npos) {
    throw std::runtime_error("unterminated XML attribute " + name);
  }
  return line.substr(value_begin, end - value_begin);
}

unsigned unsigned_value(const std::string& value, const std::string& context) {
  std::size_t consumed = 0;
  const auto parsed = std::stoull(value, &consumed);
  if (consumed != value.size() || parsed > UINT32_MAX) {
    throw std::runtime_error("invalid unsigned value for " + context);
  }
  return static_cast<unsigned>(parsed);
}

int integer_value(const std::string& value, const std::string& context) {
  std::size_t consumed = 0;
  const auto parsed = std::stoll(value, &consumed);
  if (consumed != value.size() || parsed < INT32_MIN ||
      parsed > INT32_MAX) {
    throw std::runtime_error("invalid integer value for " + context);
  }
  return static_cast<int>(parsed);
}

std::string json_escape(const std::string& value) {
  std::string result;
  result.reserve(value.size() + 8);
  for (const char character : value) {
    switch (character) {
      case '\\':
        result += "\\\\";
        break;
      case '"':
        result += "\\\"";
        break;
      case '\n':
        result += "\\n";
        break;
      case '\r':
        result += "\\r";
        break;
      case '\t':
        result += "\\t";
        break;
      default:
        result += character;
    }
  }
  return result;
}

RrGraph read_rr_graph(const std::string& path) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("cannot open RR graph: " + path);

  RrGraph graph;
  std::string line;
  std::size_t line_number = 0;
  while (std::getline(stream, line)) {
    ++line_number;
    const std::string text = trim(line);
    try {
      if (text.rfind("<node ", 0) == 0) {
        const unsigned id = unsigned_value(attribute(text, "id"), "node id");
        if (id >= graph.nodes.size()) graph.nodes.resize(id + 1);
        if (graph.nodes[id].present) {
          throw std::runtime_error("duplicate RR node id");
        }
        RrNode node;
        node.present = true;
        node.capacity =
            unsigned_value(attribute(text, "capacity"), "node capacity");
        if (node.capacity == 0) {
          throw std::runtime_error("RR node has zero capacity");
        }
        node.type = attribute(text, "type");
        node.xlow = integer_value(attribute(text, "xlow"), "xlow");
        node.ylow = integer_value(attribute(text, "ylow"), "ylow");
        node.xhigh = integer_value(attribute(text, "xhigh"), "xhigh");
        node.yhigh = integer_value(attribute(text, "yhigh"), "yhigh");
        const std::string layer_low = attribute(text, "layer_low", false);
        const std::string layer_high = attribute(text, "layer_high", false);
        node.layer_low =
            layer_low.empty() ? 0 : integer_value(layer_low, "layer_low");
        node.layer_high =
            layer_high.empty() ? 0 : integer_value(layer_high, "layer_high");
        node.ptc = integer_value(attribute(text, "ptc"), "ptc");
        graph.nodes[id] = std::move(node);
      } else if (text.rfind("<edge ", 0) == 0) {
        Edge edge;
        edge.src = unsigned_value(attribute(text, "src_node"), "edge source");
        edge.sink =
            unsigned_value(attribute(text, "sink_node"), "edge sink");
        edge.sw = unsigned_value(attribute(text, "switch_id"), "edge switch");
        if (!graph.edges.insert(edge).second) {
          throw std::runtime_error("duplicate RR edge");
        }
      } else if (text.rfind("<grid_loc ", 0) == 0) {
        const int x = integer_value(attribute(text, "x"), "grid x");
        const int y = integer_value(attribute(text, "y"), "grid y");
        if (x < 0 || y < 0) {
          throw std::runtime_error("negative grid coordinate");
        }
        graph.grid_width =
            std::max(graph.grid_width, static_cast<std::size_t>(x + 1));
        graph.grid_height =
            std::max(graph.grid_height, static_cast<std::size_t>(y + 1));
      }
    } catch (const std::exception& error) {
      throw std::runtime_error(path + ":" + std::to_string(line_number) +
                               ": " + error.what());
    }
  }
  if (graph.nodes.empty() || graph.edges.empty()) {
    throw std::runtime_error("RR graph has no nodes or edges");
  }
  for (std::size_t id = 0; id < graph.nodes.size(); ++id) {
    if (!graph.nodes[id].present) {
      throw std::runtime_error("RR graph node IDs are not dense");
    }
  }
  for (const Edge& edge : graph.edges) {
    if (edge.src >= graph.nodes.size() || edge.sink >= graph.nodes.size()) {
      throw std::runtime_error("RR edge references an unknown node");
    }
  }
  return graph;
}

struct ParsedNode {
  unsigned id = 0;
  std::string type;
  int xlow = 0;
  int ylow = 0;
  int layer_low = 0;
  int xhigh = 0;
  int yhigh = 0;
  int layer_high = 0;
  int ptc = 0;
  int sw = -1;
  int net_pin_index = -1;
};

ParsedNode parse_route_node(const std::string& line) {
  static const std::regex pattern(
      R"(^Node:\s+(\d+)\s+([A-Z]+)\s+\((-?\d+),(-?\d+),(-?\d+)\)\s+(?:to\s+\((-?\d+),(-?\d+),(-?\d+)\)\s+)?(?:Pad|Pin|Track|Class|Index):\s+(-?\d+)\s+.*?Switch:\s+(-?\d+)(?:\s+Net_pin_index:\s+(\d+))?\s*$)");
  std::smatch match;
  if (!std::regex_match(line, match, pattern)) {
    throw std::runtime_error("malformed route node");
  }
  ParsedNode node;
  node.id = unsigned_value(match[1].str(), "route node id");
  node.type = match[2].str();
  node.xlow = integer_value(match[3].str(), "route xlow");
  node.ylow = integer_value(match[4].str(), "route ylow");
  node.layer_low = integer_value(match[5].str(), "route layer_low");
  node.xhigh =
      match[6].matched ? integer_value(match[6].str(), "route xhigh")
                       : node.xlow;
  node.yhigh =
      match[7].matched ? integer_value(match[7].str(), "route yhigh")
                       : node.ylow;
  node.layer_high =
      match[8].matched ? integer_value(match[8].str(), "route layer_high")
                       : node.layer_low;
  node.ptc = integer_value(match[9].str(), "route ptc");
  node.sw = integer_value(match[10].str(), "route switch");
  if (match[11].matched) {
    node.net_pin_index =
        integer_value(match[11].str(), "route net pin index");
  }
  return node;
}

void validate_node(const ParsedNode& routed, const RrNode& rr) {
  if (routed.type != rr.type) {
    throw std::runtime_error("route node type does not match RR graph");
  }
  if (routed.xlow != rr.xlow || routed.ylow != rr.ylow ||
      routed.xhigh != rr.xhigh || routed.yhigh != rr.yhigh ||
      routed.layer_low != rr.layer_low ||
      routed.layer_high != rr.layer_high || routed.ptc != rr.ptc) {
    throw std::runtime_error(
        "route node coordinates/PTC do not match RR graph");
  }
}

RouteSummary check_route(const std::string& path, const RrGraph& graph) {
  std::ifstream stream(path);
  if (!stream) throw std::runtime_error("cannot open route file: " + path);

  RouteSummary summary;
  std::string line;
  if (!std::getline(stream, line)) {
    throw std::runtime_error("route file is empty");
  }
  static const std::regex placement_header(
      R"(^Placement_File:\s+(\S+)\s+Placement_ID:\s+(\S+)\s*$)");
  std::smatch match;
  if (!std::regex_match(line, match, placement_header)) {
    throw std::runtime_error("malformed placement header");
  }
  summary.placement_file = match[1].str();
  summary.placement_id = match[2].str();

  if (!std::getline(stream, line)) {
    throw std::runtime_error("route file has no array-size header");
  }
  static const std::regex array_header(
      R"(^Array size:\s+(\d+)\s+x\s+(\d+)\s+logic blocks\.\s*$)");
  if (!std::regex_match(line, match, array_header)) {
    throw std::runtime_error("malformed array-size header");
  }
  summary.width = unsigned_value(match[1].str(), "route width");
  summary.height = unsigned_value(match[2].str(), "route height");
  if (graph.grid_width && summary.width != graph.grid_width) {
    throw std::runtime_error("route width does not match RR graph grid");
  }
  if (graph.grid_height && summary.height != graph.grid_height) {
    throw std::runtime_error("route height does not match RR graph grid");
  }

  static const std::regex net_header(
      R"(^Net\s+(\d+)\s+\((.*)\)(:\s+global net connecting:)?\s*$)");
  std::vector<unsigned> occupancy(graph.nodes.size(), 0);
  std::unordered_set<unsigned> net_ids;
  RouteNet* current = nullptr;
  std::unordered_set<unsigned> current_nodes;
  std::unordered_set<int> sink_pin_indexes;
  ParsedNode previous;
  bool have_previous = false;
  std::size_t line_number = 2;

  auto finish_net = [&]() {
    if (current == nullptr) return;
    if (!current->global && !current->local_only) {
      if (current->nodes == 0) {
        throw std::runtime_error("routed net has no route nodes");
      }
      if (!have_previous || previous.type != "SINK") {
        throw std::runtime_error("routed net does not end at a SINK");
      }
      if (current->sinks == 0) {
        throw std::runtime_error("routed net has no SINK");
      }
    }
    current = nullptr;
    current_nodes.clear();
    sink_pin_indexes.clear();
    have_previous = false;
  };

  while (std::getline(stream, line)) {
    ++line_number;
    const std::string text = trim(line);
    if (text.empty() || text == "Routing:") continue;
    try {
      if (std::regex_match(text, match, net_header)) {
        finish_net();
        RouteNet net;
        net.id = unsigned_value(match[1].str(), "net id");
        net.name = match[2].str();
        net.global = match[3].matched;
        if (!net_ids.insert(net.id).second) {
          throw std::runtime_error("duplicate route net id");
        }
        summary.nets.push_back(std::move(net));
        current = &summary.nets.back();
        continue;
      }
      if (current == nullptr) {
        if (text[0] == '#') continue;
        throw std::runtime_error("content appears outside a net");
      }
      if (current->global) {
        if (text.rfind("Block ", 0) != 0) {
          throw std::runtime_error("malformed global-net endpoint");
        }
        ++current->endpoints;
        continue;
      }
      if (text == "Used in local cluster only, reserved one CLB pin") {
        current->local_only = true;
        continue;
      }
      if (text.rfind("Node:", 0) != 0) {
        if (text[0] == '#') continue;
        throw std::runtime_error("malformed routed-net content");
      }

      const ParsedNode routed = parse_route_node(text);
      if (routed.id >= graph.nodes.size()) {
        throw std::runtime_error("route references unknown RR node");
      }
      validate_node(routed, graph.nodes[routed.id]);
      if (current->nodes == 0 && routed.type != "SOURCE") {
        throw std::runtime_error("first route node is not SOURCE");
      }

      if (have_previous) {
        if (previous.type == "SINK") {
          if (!current_nodes.count(routed.id)) {
            throw std::runtime_error(
                "route branch does not restart at an existing tree node");
          }
          ++summary.branch_restarts;
        } else {
          if (previous.sw < 0) {
            throw std::runtime_error("route edge has a negative switch id");
          }
          const Edge edge{previous.id, routed.id,
                          static_cast<unsigned>(previous.sw)};
          if (!graph.edges.count(edge)) {
            throw std::runtime_error(
                "route edge/switch does not exist in RR graph");
          }
          ++summary.route_edges;
        }
      }

      if (current_nodes.insert(routed.id).second) {
        const unsigned used = ++occupancy[routed.id];
        summary.max_occupancy = std::max(summary.max_occupancy, used);
        summary.max_capacity =
            std::max(summary.max_capacity, graph.nodes[routed.id].capacity);
        if (used > graph.nodes[routed.id].capacity) {
          throw std::runtime_error("RR node capacity is exceeded");
        }
      }
      if (routed.type == "SINK") {
        if (routed.net_pin_index < 0) {
          throw std::runtime_error("SINK has no net pin index");
        }
        if (!sink_pin_indexes.insert(routed.net_pin_index).second) {
          throw std::runtime_error("duplicate SINK net pin index");
        }
        ++current->sinks;
      } else if (routed.net_pin_index >= 0) {
        throw std::runtime_error("non-SINK has a net pin index");
      }
      ++current->nodes;
      ++summary.route_nodes;
      previous = routed;
      have_previous = true;
    } catch (const std::exception& error) {
      throw std::runtime_error(path + ":" + std::to_string(line_number) +
                               ": " + error.what());
    }
  }
  finish_net();
  if (summary.nets.empty()) {
    throw std::runtime_error("route file contains no nets");
  }
  return summary;
}

void write_report(const std::string& path, const RrGraph& graph,
                  const RouteSummary& route) {
  std::ofstream stream(path);
  if (!stream) throw std::runtime_error("cannot write report: " + path);
  std::size_t global_nets = 0;
  std::size_t routed_nets = 0;
  for (const auto& net : route.nets) {
    global_nets += net.global;
    routed_nets += !net.global && !net.local_only;
  }
  stream << "{\n"
         << "  \"schema\": \"emuflow.vpr-route-core-check/v1\",\n"
         << "  \"status\": \"pass\",\n"
         << "  \"placement_file\": \""
         << json_escape(route.placement_file) << "\",\n"
         << "  \"placement_id\": \"" << json_escape(route.placement_id)
         << "\",\n"
         << "  \"array\": {\"width\": " << route.width
         << ", \"height\": " << route.height << "},\n"
         << "  \"rr_nodes\": " << graph.nodes.size() << ",\n"
         << "  \"rr_edges\": " << graph.edges.size() << ",\n"
         << "  \"nets_total\": " << route.nets.size() << ",\n"
         << "  \"routed_nets\": " << routed_nets << ",\n"
         << "  \"global_nets\": " << global_nets << ",\n"
         << "  \"route_nodes\": " << route.route_nodes << ",\n"
         << "  \"route_edges\": " << route.route_edges << ",\n"
         << "  \"branch_restarts\": " << route.branch_restarts << ",\n"
         << "  \"max_occupancy\": " << route.max_occupancy << ",\n"
         << "  \"max_capacity\": " << route.max_capacity << ",\n"
         << "  \"nets\": [\n";
  for (std::size_t index = 0; index < route.nets.size(); ++index) {
    const auto& net = route.nets[index];
    stream << "    {\"id\": " << net.id << ", \"name\": \""
           << json_escape(net.name) << "\", \"global\": "
           << (net.global ? "true" : "false") << ", \"local_only\": "
           << (net.local_only ? "true" : "false") << ", \"nodes\": "
           << net.nodes << ", \"sinks\": " << net.sinks
           << ", \"endpoints\": " << net.endpoints << "}";
    if (index + 1 != route.nets.size()) stream << ",";
    stream << "\n";
  }
  stream << "  ]\n}\n";
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "usage: " << argv[0]
              << " <route-file> <rr-graph.xml> <report.json>\n";
    return 2;
  }
  try {
    const RrGraph graph = read_rr_graph(argv[2]);
    const RouteSummary route = check_route(argv[1], graph);
    write_report(argv[3], graph, route);
    std::cout << "EMUFLOW_VPR_ROUTE_CORE_CHECK_V1"
              << " nets=" << route.nets.size()
              << " route_nodes=" << route.route_nodes
              << " rr_nodes=" << graph.nodes.size()
              << " rr_edges=" << graph.edges.size() << "\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "emuflow_vpr_route_checker: " << error.what() << "\n";
    return 1;
  }
}
