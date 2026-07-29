#pragma once

#include <iostream>
#include <vector>
#include <unordered_map>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include "../datastructure/hypergraph.h"
#include "../io/fpga_manager.h"

void read_in(const std::string& are_file,
             const std::string& net_file,
             const std::string& replicability_file,
             std::unordered_map<std::string,int>& vertex_name_to_id,
             std::unordered_map<int,std::string>& vertex_id_to_name,
             Hypergraph& hypergraph_init,
             const FPGAManager& fpga_manager) {
    hypergraph_init.fpga_manager = fpga_manager;

    std::ifstream are(are_file);
    int id=0;
    std::vector<int> weights(8,0);
    std::string name;
    while(are >> name) {
        for(int i=0;i<8;++i) are >> weights[i];
        hypergraph_init.hypernodes.emplace_back(id, std::vector<int>(0), weights);
        vertex_name_to_id[name] = id;
        vertex_id_to_name[id++] = name;
    }
    are.close();
    hypergraph_init._num_vertices_initial = id;
    hypergraph_init._num_vertices_current = id;

    std::ifstream replicability(replicability_file);
    if(replicability.good()) {
        for(auto& node : hypergraph_init.hypernodes) {
            node.replicable = false;
        }
        std::unordered_set<int> seen_vertices;
        std::string vertex_name;
        int may_replicate = 0;
        while(replicability >> vertex_name >> may_replicate) {
            const auto vertex = vertex_name_to_id.find(vertex_name);
            if(vertex == vertex_name_to_id.end()) {
                throw std::runtime_error(
                    "replicability file references unknown vertex " +
                    vertex_name
                );
            }
            if(!seen_vertices.insert(vertex->second).second) {
                throw std::runtime_error(
                    "replicability file repeats vertex " + vertex_name
                );
            }
            hypergraph_init.hypernodes[vertex->second].replicable =
                may_replicate != 0;
        }
        if(seen_vertices.size() != hypergraph_init.hypernodes.size()) {
            throw std::runtime_error(
                "replicability file does not cover every vertex"
            );
        }
    }

    std::ifstream net(net_file);
    std::string line;
    id = 0;
    int weight;
    std::string source;
    std::string s;
    int _begin = 0;
    int hash = 0;
    int source_id = -1;
    int target_id = -1;
    while(getline(net, line)) {
        if(line.empty()) break;
        std::stringstream ss(line);
        ss >> source;
        ss >> weight;
        source_id = vertex_name_to_id[source];
        hash = Hash(source_id);
        hypergraph_init._incidence_array.push_back(source_id);
        int cnt = 1;
        hypergraph_init.hypernodes[source_id].incident_nets_as_source.insert(id);
        while(ss >> s) {
            target_id = vertex_name_to_id[s];
            hash += Hash(target_id);
            hypergraph_init.hypernodes[target_id].incident_nets_as_target.insert(id);
            hypergraph_init._incidence_array.push_back(target_id);
            ++cnt;
        }
        hypergraph_init.hyperedges.emplace_back(_begin, cnt, weight, id++, hash, fpga_manager.fpga_num);
        _begin += cnt;
    }
    net.close();
    hypergraph_init._num_hyperedges = id;
}
