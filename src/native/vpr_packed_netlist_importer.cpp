// Copyright 2026 EmuFlow contributors
// SPDX-License-Identifier: Apache-2.0

#include "pugixml.hpp"

#include <algorithm>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct PbBlock {
  std::string path;
  std::string name;
  std::string instance;
  std::string mode;
  bool leaf = false;
};

struct Cluster {
  std::string id;
  std::string name;
  std::string instance;
  std::string block_type;
  std::string mode;
  std::vector<PbBlock> pb_blocks;
  std::set<std::string> input_nets;
  std::set<std::string> output_nets;
};

std::string attribute(
    const pugi::xml_node& node,
    const char* name,
    const std::string& fallback = "") {
  const auto value = node.attribute(name);
  return value ? value.value() : fallback;
}

std::string hex_encode(const std::string& value) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string result;
  result.reserve(value.size() * 2);
  for (const unsigned char character : value) {
    result.push_back(kHex[character >> 4]);
    result.push_back(kHex[character & 0x0f]);
  }
  return result;
}

std::vector<std::string> tokens(const pugi::xml_node& node) {
  std::vector<std::string> result;
  std::istringstream stream(node.child_value());
  std::string token;
  while (stream >> token) {
    result.push_back(token);
  }
  return result;
}

bool is_global_net(const std::string& token) {
  return token != "open" && token.find("->") == std::string::npos;
}

std::string block_type(const std::string& instance) {
  const auto bracket = instance.find('[');
  if (bracket == std::string::npos || bracket == 0) {
    throw std::runtime_error(
        "top-level packed block has malformed instance: " + instance);
  }
  return instance.substr(0, bracket);
}

std::vector<pugi::xml_node> used_children(const pugi::xml_node& block) {
  std::vector<pugi::xml_node> result;
  for (const auto child : block.children("block")) {
    if (attribute(child, "name") != "open") {
      result.push_back(child);
    }
  }
  return result;
}

void collect_leaf_outputs(
    const pugi::xml_node& block,
    std::set<std::string>& output_nets) {
  for (const auto port : block.child("outputs").children("port")) {
    for (const auto& token : tokens(port)) {
      if (is_global_net(token)) {
        output_nets.insert(token);
      }
    }
  }
}

void collect_pb_blocks(
    const pugi::xml_node& block,
    const std::string& parent_path,
    Cluster& cluster) {
  const std::string instance = attribute(block, "instance");
  if (instance.empty()) {
    throw std::runtime_error("packed pb block has no instance");
  }
  const std::string path = parent_path + "/" + instance;
  const auto children = used_children(block);
  const bool leaf = children.empty();
  cluster.pb_blocks.push_back(
      {path,
       attribute(block, "name"),
       instance,
       attribute(block, "mode"),
       leaf});
  if (leaf) {
    collect_leaf_outputs(block, cluster.output_nets);
  }
  for (const auto child : children) {
    collect_pb_blocks(child, path, cluster);
  }
}

void collect_cluster_inputs(
    const pugi::xml_node& block,
    Cluster& cluster) {
  for (const char* group_name : {"inputs", "clocks"}) {
    for (const auto port : block.child(group_name).children("port")) {
      for (const auto& token : tokens(port)) {
        if (is_global_net(token)) {
          cluster.input_nets.insert(token);
        }
      }
    }
  }
}

std::vector<Cluster> read_clusters(const pugi::xml_node& root) {
  std::vector<Cluster> clusters;
  std::set<std::string> ids;
  for (const auto block : root.children("block")) {
    if (attribute(block, "name") == "open") {
      continue;
    }
    Cluster cluster;
    cluster.instance = attribute(block, "instance");
    if (cluster.instance.empty()) {
      throw std::runtime_error("top-level packed block has no instance");
    }
    cluster.id = cluster.instance;
    if (!ids.insert(cluster.id).second) {
      throw std::runtime_error(
          "duplicate top-level packed block: " + cluster.id);
    }
    cluster.name = attribute(block, "name");
    cluster.block_type = block_type(cluster.instance);
    cluster.mode = attribute(block, "mode");
    collect_cluster_inputs(block, cluster);
    for (const auto child : used_children(block)) {
      collect_pb_blocks(child, cluster.instance, cluster);
    }
    clusters.push_back(std::move(cluster));
  }
  std::sort(
      clusters.begin(), clusters.end(),
      [](const Cluster& left, const Cluster& right) {
        return left.id < right.id;
      });
  return clusters;
}

void emit(const pugi::xml_node& root, const std::vector<Cluster>& clusters) {
  std::cout << "EMUFLOW_VPR_PACKED_NETLIST_EXTRACT_V1\n";
  std::cout << "ROOT\t" << hex_encode(attribute(root, "name")) << '\t'
            << hex_encode(attribute(root, "instance")) << '\t'
            << hex_encode(attribute(root, "architecture_id")) << '\t'
            << hex_encode(attribute(root, "atom_netlist_id")) << '\n';

  std::map<std::string, std::set<std::string>> drivers;
  std::map<std::string, std::set<std::string>> sinks;
  for (const auto& cluster : clusters) {
    std::cout << "CLUSTER\t" << hex_encode(cluster.id) << '\t'
              << hex_encode(cluster.name) << '\t'
              << hex_encode(cluster.instance) << '\t'
              << hex_encode(cluster.block_type) << '\t'
              << hex_encode(cluster.mode) << '\n';
    for (const auto& block : cluster.pb_blocks) {
      std::cout << "PB\t" << hex_encode(cluster.id) << '\t'
                << hex_encode(block.path) << '\t'
                << hex_encode(block.name) << '\t'
                << hex_encode(block.instance) << '\t'
                << hex_encode(block.mode) << '\t'
                << (block.leaf ? "1" : "0") << '\n';
    }
    for (const auto& net : cluster.output_nets) {
      drivers[net].insert(cluster.id);
    }
    for (const auto& net : cluster.input_nets) {
      sinks[net].insert(cluster.id);
    }
  }

  std::set<std::string> net_names;
  for (const auto& [net, unused] : drivers) {
    (void)unused;
    net_names.insert(net);
  }
  for (const auto& [net, unused] : sinks) {
    (void)unused;
    net_names.insert(net);
  }
  for (const auto& net : net_names) {
    const auto& net_drivers = drivers[net];
    if (net_drivers.size() > 1) {
      throw std::runtime_error(
          "packed net has drivers in multiple clusters: " + net);
    }
    std::set<std::string> net_sinks = sinks[net];
    if (!net_drivers.empty()) {
      net_sinks.erase(*net_drivers.begin());
    }
    const std::size_t endpoint_count =
        net_drivers.size() + net_sinks.size();
    if (endpoint_count < 2) {
      continue;
    }
    if (net_drivers.empty()) {
      throw std::runtime_error(
          "cross-cluster packed net has no driver: " + net);
    }
    std::cout << "NET\t" << hex_encode(net) << '\t'
              << hex_encode(*net_drivers.begin()) << '\n';
    for (const auto& sink : net_sinks) {
      std::cout << "SINK\t" << hex_encode(net) << '\t'
                << hex_encode(sink) << '\n';
    }
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2 || std::string(argv[1]) == "--help") {
    std::cerr << "usage: emuflow_vpr_packed_netlist_importer <packed.net>\n";
    return argc == 2 ? 0 : 2;
  }
  try {
    pugi::xml_document document;
    const auto result = document.load_file(argv[1]);
    if (!result) {
      throw std::runtime_error(
          std::string("cannot parse packed netlist XML: ") +
          result.description());
    }
    const auto root = document.child("block");
    if (!root || attribute(root, "instance").find("FPGA_packed_netlist") != 0) {
      throw std::runtime_error(
          "packed netlist root must be FPGA_packed_netlist");
    }
    const auto clusters = read_clusters(root);
    if (clusters.empty()) {
      throw std::runtime_error("packed netlist contains no used clusters");
    }
    emit(root, clusters);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "error: " << error.what() << '\n';
    return 1;
  }
}
