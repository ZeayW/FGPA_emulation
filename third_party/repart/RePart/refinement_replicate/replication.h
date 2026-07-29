#pragma once

#include <vector>
#include <unordered_map>
#include <limits>
#include <iostream>
#include <assert.h>

#include "../datastructure/hypergraph.h"
#include "../io/fpga_manager.h"
#include "../datastructure/binary_heap.h"

class Replication {
public:

    Hypergraph& hypergraph; 
    const FPGAManager& fpga_manager; 
    std::vector<std::vector<int>>& resource_usage; 
    std::vector<int>& communication_usage; 
    long long& total_cost; 
    std::vector<BinaryMaxHeap<int, int>> gain_priority_queues; 

    const long long top_k = 10000000; 
    int rep_num = 0; 

    Replication(Hypergraph& hypergraph, const FPGAManager& fpga_manager,
                std::vector<std::vector<int>>& current_resource_usage,
                std::vector<int>& current_communication_usage,
                long long& total_communication_cost)
        : hypergraph(hypergraph),
          fpga_manager(fpga_manager),
          resource_usage(current_resource_usage),
          communication_usage(current_communication_usage),
          total_cost(total_communication_cost) {
        for(int i = 0; i < fpga_manager.fpga_num; ++i) {
            gain_priority_queues.push_back(BinaryMaxHeap<int, int>(hypergraph.hypernodes.size()));
        }
    }

    ~Replication() = default;

    void Solve(int gain_threshold) {
        calculateGain();





        int max_fpga_id = 0;
        int max_node_id = 0;
        while(max_fpga_id != -1 && max_node_id != -1){
            int max_gain = -1;
            max_fpga_id = -1;
            max_node_id = -1;
            for (int fpga_id = 0; fpga_id < fpga_manager.fpga_num; ++fpga_id) {
                if (!gain_priority_queues[fpga_id].empty()) {
                    int node_id = gain_priority_queues[fpga_id].top();
                    int gain = gain_priority_queues[fpga_id].topKey();
                    if(gain > max_gain) {
                        max_gain = gain;
                        max_fpga_id = fpga_id;
                        max_node_id = node_id;
                    }
                }
            }

            if(max_fpga_id != -1 && max_node_id != -1) {
                gain_priority_queues[max_fpga_id].pop();
                
                if(max_gain < gain_threshold) {
                    break;
                }

                std::unordered_map<int, int> fpgas_comm_change;
                std::unordered_map<int, std::vector<int>> hyperedges_target_pins_in_part_new;
                if (canReplicate(max_node_id, max_fpga_id, fpgas_comm_change, hyperedges_target_pins_in_part_new)) {
                    replicateNode(max_node_id, max_fpga_id, max_gain, fpgas_comm_change, hyperedges_target_pins_in_part_new);
                    UpdateNeighbourNodes(max_node_id, max_fpga_id);
                    ++rep_num;
                    if(rep_num == top_k) {
                        break;
                    }
                }
            }
        }
    }

private:

    int calculateGain_Positive(int source_fpga, int target_fpga, int weight) {
        return weight * fpga_manager.hop_distances[source_fpga][target_fpga];
    }

    bool calculateGain_Negative(int node_id, int target_fpga, int& gain_neg) {
        const Hypernode& hn = hypergraph.hypernodes[node_id];

        for(auto he_id: hn.incident_nets_as_target) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            int source_part_id = hypergraph.get_source_part_id(he_id);
            auto& source_replication_fpga_labels = hypergraph.get_source_replication_fpga_labels(he_id);

            if(source_part_id == target_fpga) {
                continue;
            }
            if(source_replication_fpga_labels.find(target_fpga) != source_replication_fpga_labels.end()) {
                continue;
            }

            if(he.target_pins_in_part[target_fpga] != 0) {
                continue;
            }

            int hop_distance = fpga_manager.getHopDistance(source_part_id, target_fpga);
            if(hop_distance < 0) {
                return false;
            }
            gain_neg += he.weight * hop_distance;
        }

        return true;
    }

    void calculateGain_node(const Hypernode& hn) {

        int source_id = hn.id;
        int source_fpga = hn.label;


        std::unordered_map<int, int> fpgas_gain;
        std::unordered_set<int> visited_fpgas;
        std::unordered_set<int> fpgas_cannot_communicate;

        for(const auto& he_id: hn.incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];


            int target_pins_in_part_size = he.target_pins_in_part.size();
            for(int fpga_id = 0; fpga_id < target_pins_in_part_size; ++fpga_id) {
                if(he.target_pins_in_part[fpga_id] == 0) {
                    continue;
                }

                int target_fpga = fpga_id;

                if(target_fpga == source_fpga) {
                    continue;
                }

                if(fpgas_cannot_communicate.find(target_fpga) != fpgas_cannot_communicate.end()) {
                    continue;
                }

                if(hn.replication_fpga_labels.find(target_fpga) != hn.replication_fpga_labels.end()) {
                    continue;
                }


                int gain_pos = calculateGain_Positive(source_fpga, target_fpga, he.weight);


                if (fpgas_gain.find(target_fpga) == fpgas_gain.end()){
                    fpgas_gain[target_fpga] = gain_pos;
                }
                else {
                    fpgas_gain[target_fpga] += gain_pos;
                }

                if(visited_fpgas.find(target_fpga) == visited_fpgas.end()) {
                    visited_fpgas.insert(target_fpga);
                    int gain_neg = 0;
                    
                    if (!calculateGain_Negative(source_id, target_fpga, gain_neg))
                    {
                        fpgas_cannot_communicate.insert(target_fpga);
                        fpgas_gain.erase(target_fpga);
                        continue;
                    }
                    fpgas_gain[target_fpga] -= gain_neg;
                }
            }
        }

        for(const auto& fg: fpgas_gain) {
            int target_fpga = fg.first;
            int gain = fg.second;
            gain_priority_queues[target_fpga].push(source_id, gain);
        }
        
    }

    void calculateGain() {
        for(const auto& hn: hypergraph.hypernodes) {
            if(hn.deleted) {
                continue;
            }
            calculateGain_node(hn);
        }
    }

    bool checkCommunicationResource(int node_id, int target_fpga, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {

        fpgas_comm_change[target_fpga] = 0;
        int node_in_fpga = hypergraph.hypernodes[node_id].label;
        fpgas_comm_change[node_in_fpga] = 0;




        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_target) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            int source_part_id = hypergraph.get_source_part_id(he_id);
            auto& source_replication_fpga_labels = hypergraph.get_source_replication_fpga_labels(he_id);

            if(source_replication_fpga_labels.find(target_fpga) != source_replication_fpga_labels.end()) {
                assert(he.target_pins_in_part[target_fpga] == 0);
                continue;
            }

            hyperedges_target_pins_in_part_new[he_id] = he.target_pins_in_part;

            if(source_part_id == target_fpga) {
                ++hyperedges_target_pins_in_part_new[he_id][target_fpga];
                continue;
            }

            if(he.target_pins_in_part[target_fpga] != 0) {
                ++hyperedges_target_pins_in_part_new[he_id][target_fpga];
                continue;
            }

            hyperedges_target_pins_in_part_new[he_id][target_fpga] = 1;


            fpgas_comm_change[target_fpga] += he.weight;  

            if(fpgas_comm_change.find(source_part_id) == fpgas_comm_change.end()) {
                fpgas_comm_change[source_part_id] = 0;
            }
            bool flag_temp = true;
            int target_pins_in_part_size = he.target_pins_in_part.size();
            for(int fpga_id = 0; fpga_id < target_pins_in_part_size; ++fpga_id) {
                int num_in_part = he.target_pins_in_part[fpga_id];
                if (num_in_part != 0 && fpga_id != target_fpga && fpga_id != source_part_id){
                    flag_temp = false;
                    break;
                }
            }
            if(flag_temp) {
                fpgas_comm_change[source_part_id] += he.weight;
            }     
        }


        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            if(he.target_pins_in_part[target_fpga] != 0) {
                hyperedges_target_pins_in_part_new[he_id] = he.target_pins_in_part;
                hyperedges_target_pins_in_part_new[he_id][target_fpga] = 0;

                fpgas_comm_change[target_fpga] -= he.weight;
                bool flag_temp = true;
                int target_pins_in_part_size = he.target_pins_in_part.size();
                for(int fpga_id = 0; fpga_id < target_pins_in_part_size; ++fpga_id) {
                    int num_in_part = he.target_pins_in_part[fpga_id];
                    if (num_in_part != 0 && fpga_id != target_fpga && fpga_id != node_in_fpga){
                        flag_temp = false;
                        break;
                    }
                }
                if(flag_temp) {
                    fpgas_comm_change[node_in_fpga] -= he.weight;
                }
            }
        }

        for(const auto& fcc: fpgas_comm_change) {
            if(fcc.second + communication_usage[fcc.first] > fpga_manager.fpga_info[fcc.first][0]) {
                return false;
            }
        }

        return true;
    }

    bool canReplicate(int node_id, int target_fpga, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {
        if(hypergraph.hypernodes[node_id].replication_fpga_labels.find(target_fpga) != hypergraph.hypernodes[node_id].replication_fpga_labels.end()) {
            return false; 
        }
        const auto& fpga_info = fpga_manager.fpga_info[target_fpga];

        const Hypernode& node = hypergraph.hypernodes[node_id];
        for (size_t i = 0; i < node.weights.size(); ++i) {
            if (resource_usage[target_fpga][i] + node.weights[i] > fpga_info[i + 1]) {
                return false; 
            }
        }

        if (!checkCommunicationResource(node_id, target_fpga, fpgas_comm_change, hyperedges_target_pins_in_part_new)) {
            return false; 
        }

        return true; 
    }

    void replicateNode(int node_id, int target_fpga, int gain, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {
        Hypernode& node = hypergraph.hypernodes[node_id];
        for(auto& fcc: fpgas_comm_change) {
            communication_usage[fcc.first] += fcc.second;
        }
        for (size_t i = 0; i < node.weights.size(); ++i) {
            resource_usage[target_fpga][i] += node.weights[i]; 
        }

        for(const auto& target_pins_in_part_new: hyperedges_target_pins_in_part_new) {
            int he_id = target_pins_in_part_new.first;
            hypergraph.hyperedges[he_id].target_pins_in_part = target_pins_in_part_new.second;
        }

        total_cost -= gain;

        hypergraph.hypernodes[node_id].replication_fpga_labels.insert(target_fpga);

    }


    void updateGain_node(const Hypernode& hn) {

        int source_id = hn.id;
        int source_fpga = hn.label;


        std::unordered_map<int, int> fpgas_gain;
        std::unordered_set<int> visited_fpgas;
        std::unordered_set<int> fpgas_cannot_communicate;

        for(const auto& he_id: hn.incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];


            int target_pins_in_part_size = he.target_pins_in_part.size();
            for(int fpga_id = 0; fpga_id < target_pins_in_part_size; ++fpga_id) {
                if(he.target_pins_in_part[fpga_id] == 0) {
                    continue;
                }

                int target_fpga = fpga_id;

                if(target_fpga == source_fpga) {
                    continue;
                }

                if(fpgas_cannot_communicate.find(target_fpga) != fpgas_cannot_communicate.end()) {
                    continue;
                }

                if(hn.replication_fpga_labels.find(target_fpga) != hn.replication_fpga_labels.end()) {
                    continue;
                }


                int gain_pos = calculateGain_Positive(source_fpga, target_fpga, he.weight);


                if (fpgas_gain.find(target_fpga) == fpgas_gain.end()){
                    fpgas_gain[target_fpga] = gain_pos;
                }
                else {
                    fpgas_gain[target_fpga] += gain_pos;
                }

                if(visited_fpgas.find(target_fpga) == visited_fpgas.end()) {
                    visited_fpgas.insert(target_fpga);
                    int gain_neg = 0;
                    
                    if (!calculateGain_Negative(source_id, target_fpga, gain_neg))
                    {
                        fpgas_cannot_communicate.insert(target_fpga);
                        fpgas_gain.erase(target_fpga);
                        continue;
                    }
                    fpgas_gain[target_fpga] -= gain_neg;
                }
            }
        }

        for(const auto& fg: fpgas_gain) {
            int target_fpga = fg.first;
            int gain = fg.second;
            if(gain_priority_queues[target_fpga].contains(source_id)) {
                gain_priority_queues[target_fpga].updateKey(source_id, gain);
            } 
            else {
                gain_priority_queues[target_fpga].push(source_id, gain);
            }
        }
    }

    void UpdateNeighbourNodes(int node_id, int target_fpga) {

        std::unordered_set<int> visited_nodes;

        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_target) {
            const auto& he = hypergraph.hyperedges[he_id];
            const auto& source_neighbour_node = hypergraph.hypernodes[hypergraph._incidence_array[he._begin]];
            if(visited_nodes.find(source_neighbour_node.id) == visited_nodes.end()) {
                visited_nodes.insert(source_neighbour_node.id);
                updateGain_node(source_neighbour_node);
            }

            for (int i = he._begin+1; i < he._begin + he._size; ++i) {
                int neighbour_id = hypergraph._incidence_array[i];
                if(visited_nodes.find(neighbour_id) == visited_nodes.end()) {
                    visited_nodes.insert(neighbour_id);
                    if(gain_priority_queues[target_fpga].contains(neighbour_id)) {
                        const auto& neighbour_node = hypergraph.hypernodes[neighbour_id];
                        updateGain_node(neighbour_node);
                    }
                }
            }
        }

        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_source) {
            const auto& he = hypergraph.hyperedges[he_id];

            for (int i = he._begin+1; i < he._begin + he._size; ++i) {
                int neighbour_id = hypergraph._incidence_array[i];
                if(visited_nodes.find(neighbour_id) == visited_nodes.end()) {
                    visited_nodes.insert(neighbour_id);
                    const auto& neighbour_node = hypergraph.hypernodes[neighbour_id];
                    updateGain_node(neighbour_node);
                }
            }
        }
    }
};
