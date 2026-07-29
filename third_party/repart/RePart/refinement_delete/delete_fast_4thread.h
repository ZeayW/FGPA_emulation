#pragma once

#include <vector>
#include <unordered_map>
#include <queue>
#include <limits>
#include <iostream>

#include "../datastructure/hypergraph.h"
#include "../io/fpga_manager.h"
#include "../datastructure/binary_heap.h"

#include "../../boost_1_86_0/include/boost/thread/thread.hpp"
#include "../../boost_1_86_0/include/boost/thread/mutex.hpp"
#include "../../boost_1_86_0/include/boost/thread/shared_mutex.hpp"
#include "../../boost_1_86_0/include/boost/thread/barrier.hpp"

class Delete {
public:

    Hypergraph& hypergraph; 
    const FPGAManager& fpga_manager; 
    std::vector<std::vector<int>>& resource_usage; 
    std::vector<int>& communication_usage; 
    long long& total_cost; 
    std::vector<BinaryMaxHeap<int, int>> gain_priority_queues; 

    Delete(Hypergraph& hypergraph, const FPGAManager& fpga_manager,
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

    void checkGainPriorityQueues() {
        for(int i = 0; i < fpga_manager.fpga_num; ++i) {
            std::cout << "FPGA " << i+1 << " has " << gain_priority_queues[i].size() << " nodes in the priority queue" << std::endl;
            if(!gain_priority_queues[i].empty()) {
                std::cout << "Its top node is " << gain_priority_queues[i].top()+1 << " with gain " << gain_priority_queues[i].topKey() << std::endl;
            }
        }
    }

    void checkCommunicationUsage() {
        for(int i = 0; i < fpga_manager.fpga_num; ++i) {
            std::cout << fpga_manager.getName(i) << ": " << communication_usage[i] << std::endl;
        }
    }

    void checkResourceUsage() {
        for(int i = 0; i < fpga_manager.fpga_num; ++i) {
            std::cout << fpga_manager.getName(i) << ": ";
            for(int j = 0; j < 8; ++j) {
                std::cout << resource_usage[i][j] << " ";
            }
            std::cout << std::endl;
        }
    }

    void Solve(int gain_threshold) {
        calculateGain();


        int max_replication_fpga_id = 0;
        int max_master_id = 0;

        int count = 0;

        while(max_replication_fpga_id != -1 && max_master_id != -1 && count < 1000000){
            int max_gain = -10000000;
            max_replication_fpga_id = -1;
            max_master_id = -1;
            for(int fpga_id = 0; fpga_id < fpga_manager.fpga_num; ++fpga_id) {
                if(!gain_priority_queues[fpga_id].empty()) {
                    int master_id = gain_priority_queues[fpga_id].top();
                    int gain = gain_priority_queues[fpga_id].topKey();
                    if(gain > max_gain) {
                        max_gain = gain;
                        max_replication_fpga_id = fpga_id;
                        max_master_id = master_id;
                    }
                }
            }

            if(max_replication_fpga_id != -1 && max_master_id != -1) {
                gain_priority_queues[max_replication_fpga_id].pop();
                
                if(max_gain < gain_threshold) {
                    break;
                }

                std::unordered_map<int, int> fpgas_comm_change;
                std::unordered_map<int, std::vector<int>> hyperedges_target_pins_in_part_new;
                if (canDelete(max_master_id, max_replication_fpga_id, fpgas_comm_change, hyperedges_target_pins_in_part_new)) {
                    deleteNode(max_master_id, max_replication_fpga_id, max_gain, fpgas_comm_change, hyperedges_target_pins_in_part_new);
                    updateNeighbourNodes(max_master_id, max_replication_fpga_id);
                    ++count;
                }
            }
        }
    }

private:

    int calculateGain_Positive(int master_id, int replication_fpga_id) {
        int gain_pos = 0;
        const Hypernode& hn = hypergraph.hypernodes[master_id];

        for(const auto& he_id: hn.incident_nets_as_target) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            int source_part_id = hypergraph.get_source_part_id(he_id);
            auto& source_replication_fpga_labels = hypergraph.get_source_replication_fpga_labels(he_id);

            if(source_part_id == replication_fpga_id) {
                continue;
            }
            if(source_replication_fpga_labels.find(replication_fpga_id) != source_replication_fpga_labels.end()) {
                continue;
            }
            if(he.target_pins_in_part.at(replication_fpga_id) > 1) {
                continue;
            }
            gain_pos += he.weight * fpga_manager.hop_distances[source_part_id][replication_fpga_id];
        }
        return gain_pos;
    }

    int calculateGain_Negative(int master_id, int replication_fpga_id) {
        int gain_neg = 0;
        int master_fpga_id = hypergraph.hypernodes[master_id].label;
        const Hypernode& hn = hypergraph.hypernodes[master_id];

        for(auto he_id: hn.incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            for(int i=he._begin+1; i<he._begin+he._size; ++i) {
                const auto& target_hypernode = hypergraph.hypernodes[hypergraph._incidence_array[i]];
                int target_fpga = target_hypernode.label;
                if(target_fpga == replication_fpga_id || target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end()) {
                    gain_neg += he.weight * fpga_manager.hop_distances[master_fpga_id][replication_fpga_id];
                    break;
                }
            }
        }
        return gain_neg;
    }

    bool checkHopDistance(int master_id, int replication_fpga_id) {
        bool find_target_pin_in_replication_fpga = false;
        for(const auto& he_id: hypergraph.hypernodes[master_id].incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            for(int i=he._begin+1; i<he._begin+he._size && !find_target_pin_in_replication_fpga; ++i) {
                const auto& target_hypernode = hypergraph.hypernodes[hypergraph._incidence_array[i]];
                int target_fpga_id = target_hypernode.label;
                if(target_fpga_id == replication_fpga_id || target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end()) {
                    find_target_pin_in_replication_fpga = true;
                    break;
                }
            }
        }
        if(!find_target_pin_in_replication_fpga) {
            return true;
        }
        int master_fpga_id = hypergraph.hypernodes[master_id].label;
        return fpga_manager.getHopDistance(master_fpga_id, replication_fpga_id) >= 0;
    }

    void calculateGain() {
        const int num_threads = 4;
        int num_nodes = hypergraph.hypernodes.size();
        int nodes_per_thread = (num_nodes + num_threads - 1) / num_threads; 
        boost::barrier sync_barrier(num_threads); 

        auto thread_task = [&](int thread_id) {
            int start_index = thread_id * nodes_per_thread;
            int end_index = std::min(start_index + nodes_per_thread, num_nodes);
            
            for (int i = start_index; i < end_index; ++i) {
                const auto& hn = hypergraph.hypernodes[i];
                if (hn.deleted) continue;

                int master_id = hn.id;
                auto& hn_replication_fpga_labels = hn.replication_fpga_labels;

                std::unordered_map<int, int> fpgas_gain;
                for (const auto& replication_fpga_id : hn_replication_fpga_labels) {
                    if (!checkHopDistance(master_id, replication_fpga_id)) continue;
                    
                    int gain_pos = calculateGain_Positive(master_id, replication_fpga_id);
                    int gain_neg = calculateGain_Negative(master_id, replication_fpga_id);

                    fpgas_gain[replication_fpga_id] = gain_pos - gain_neg;
                }

                {
                    boost::mutex::scoped_lock lock(boost::mutex); 
                    for (const auto& fg : fpgas_gain) {
                        int target_fpga = fg.first;
                        int gain = fg.second;
                        gain_priority_queues[target_fpga].push(master_id, gain);
                    }
                }
            }

            sync_barrier.wait(); 
        };

        boost::thread_group threads;
        for (int i = 0; i < num_threads; ++i) {
            threads.create_thread([=] { thread_task(i); }); 
        }
        threads.join_all(); 
    }

    bool checkCommunicationResource(int master_id, int replication_fpga_id, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {
        const int master_fpga_id = hypergraph.hypernodes[master_id].label;

        fpgas_comm_change[master_fpga_id] = 0;
        fpgas_comm_change[replication_fpga_id] = 0;

        for(const auto& he_id: hypergraph.hypernodes[master_id].incident_nets_as_target) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            int source_part_id = hypergraph.get_source_part_id(he_id);
            auto& source_replication_fpga_labels = hypergraph.get_source_replication_fpga_labels(he_id);
            if(source_replication_fpga_labels.find(replication_fpga_id) != source_replication_fpga_labels.end()) {
                continue;
            }
            hyperedges_target_pins_in_part_new[he_id] = he.target_pins_in_part;

            if(source_part_id == replication_fpga_id) {
                --hyperedges_target_pins_in_part_new[he_id][replication_fpga_id];
                continue;
            }
            
            if(he.target_pins_in_part.at(replication_fpga_id) > 1) {
                --hyperedges_target_pins_in_part_new[he_id][replication_fpga_id];
                continue;
            }

            hyperedges_target_pins_in_part_new[he_id][replication_fpga_id] = 0;

            if(fpgas_comm_change.find(source_part_id) == fpgas_comm_change.end()) {
                fpgas_comm_change[source_part_id] = 0;
            }
            fpgas_comm_change[replication_fpga_id] -= he.weight;

            bool flag_temp = true;
            for(int i=0;i<fpga_manager.fpga_num;++i) {
                if(he.target_pins_in_part[i] != 0 && i != source_part_id && i != replication_fpga_id) {
                    flag_temp = false;
                    break;
                }
            }
            if(flag_temp) {
                fpgas_comm_change[source_part_id] -= he.weight;
            }
        }

        for(const auto& he_id: hypergraph.hypernodes[master_id].incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            hyperedges_target_pins_in_part_new[he_id] = he.target_pins_in_part;
            bool update_comm_finished = false;
            for(int i=he._begin+1; i<he._begin+he._size; ++i) {
                const auto& target_hypernode = hypergraph.hypernodes[hypergraph._incidence_array[i]];
                int target_fpga_id = target_hypernode.label;
                if(target_fpga_id == replication_fpga_id || target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end()) {
                    ++hyperedges_target_pins_in_part_new[he_id][replication_fpga_id];
                    if(!update_comm_finished) {
                        fpgas_comm_change[replication_fpga_id] += he.weight;
                        bool flag_temp = true;
                        for(int i=0;i<fpga_manager.fpga_num;++i) {
                            if(he.target_pins_in_part[i] != 0 && i != replication_fpga_id && i != master_fpga_id) {
                                flag_temp = false;
                                break;
                            }
                        }
                        if(flag_temp) {
                            fpgas_comm_change[master_fpga_id] += he.weight;
                        }
                        update_comm_finished = true;
                    }
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


    bool canDelete(int master_id, int replication_fpga_id, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {
        if(hypergraph.hypernodes[master_id].replication_fpga_labels.find(replication_fpga_id) == hypergraph.hypernodes[master_id].replication_fpga_labels.end()) {
            return false; 
        }

        if (!checkCommunicationResource(master_id, replication_fpga_id, fpgas_comm_change, hyperedges_target_pins_in_part_new)) {
            return false; 
        }
        return true; 
    }

    void deleteNode(int master_id, int replication_fpga_id, int gain, std::unordered_map<int, int>& fpgas_comm_change, std::unordered_map<int, std::vector<int>>& hyperedges_target_pins_in_part_new) {
        const int master_fpga_id = hypergraph.hypernodes[master_id].label;
        Hypernode& node = hypergraph.hypernodes[master_id];
        for(auto& fcc: fpgas_comm_change) {
            communication_usage[fcc.first] += fcc.second;
        }
        for (int i = 0; i < node.weights.size(); ++i) {
            resource_usage[replication_fpga_id][i] -= node.weights[i]; 
        }
        for(const auto& target_pins_in_part_new: hyperedges_target_pins_in_part_new) {
            int he_id = target_pins_in_part_new.first;
            hypergraph.hyperedges[he_id].target_pins_in_part = target_pins_in_part_new.second;
        }
        total_cost -= gain;

        hypergraph.hypernodes[master_id].replication_fpga_labels.erase(replication_fpga_id);
    }


    void updateGain_deleted_node_as_source(const Hypernode& neighbour_hn, const Hyperedge& he, int replication_fpga_id) {
        int neighbour_master_id = neighbour_hn.id;
        int source_part_id = hypergraph.get_source_part_id(he.id);
        if(he.target_pins_in_part.at(replication_fpga_id) > 1) {
            return;
        }
        int gain_pos = he.weight * fpga_manager.hop_distances[source_part_id][replication_fpga_id];
        gain_priority_queues[replication_fpga_id].updateKeyBy(neighbour_master_id, gain_pos);
    }

    void updateGain_deleted_node_as_target_deleting_node_as_source(const Hypernode& neighbour_hn, const Hyperedge& he, int replication_fpga_id) {
        int neighbour_master_id = neighbour_hn.id;
        int source_part_id = hypergraph.get_source_part_id(he.id);
        bool find_target_pin_in_replication_fpga = false;
        for(int i=he._begin+1; i<he._begin+he._size; ++i) {
            const auto& target_hypernode = hypergraph.hypernodes[hypergraph._incidence_array[i]];
            if(target_hypernode.label == replication_fpga_id || target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end()) {
                find_target_pin_in_replication_fpga = true;
            }
        }
        if(find_target_pin_in_replication_fpga) {
            return;
        }
        int gain_pos = he.weight * fpga_manager.hop_distances[source_part_id][replication_fpga_id];
        gain_priority_queues[replication_fpga_id].updateKeyBy(neighbour_master_id, gain_pos);
    }

    void updateGain_deleted_node_as_target_deleting_node_as_target(const Hypernode& neighbour_hn, const Hyperedge& he, int replication_fpga_id) {
        int neighbour_master_id = neighbour_hn.id;
        const Hypernode& source_node = hypergraph.hypernodes[hypergraph._incidence_array[he._begin]];
        if(source_node.label == replication_fpga_id || source_node.replication_fpga_labels.find(replication_fpga_id) != source_node.replication_fpga_labels.end()) {
            return;
        }
        if(he.target_pins_in_part.at(replication_fpga_id) > 1) {
            return;
        }
        int gain_pos = he.weight * fpga_manager.hop_distances[source_node.label][replication_fpga_id];
        gain_priority_queues[replication_fpga_id].updateKeyBy(neighbour_master_id, gain_pos);
    }

    void updateNeighbourNodes(int master_id, int replication_fpga_id) {
        for(const auto& he_id: hypergraph.hypernodes[master_id].incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            for(int i=he._begin+1; i<he._begin+he._size; ++i) {
                int neighbour_master_id = hypergraph._incidence_array[i];
                const auto& target_hypernode = hypergraph.hypernodes[neighbour_master_id];
                if(target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end() && gain_priority_queues[replication_fpga_id].contains(neighbour_master_id)) {
                    updateGain_deleted_node_as_source(target_hypernode, he, replication_fpga_id);
                }
            }
        }
        for(const auto& he_id: hypergraph.hypernodes[master_id].incident_nets_as_target) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];
            int neighbour_master_id = hypergraph._incidence_array[he._begin];
            if(gain_priority_queues[replication_fpga_id].contains(neighbour_master_id)) {
                updateGain_deleted_node_as_target_deleting_node_as_source(hypergraph.hypernodes[neighbour_master_id], he, replication_fpga_id);
            }
            for(int i=he._begin; i<he._begin+he._size; ++i) {
                neighbour_master_id = hypergraph._incidence_array[i];
                const auto& target_hypernode = hypergraph.hypernodes[neighbour_master_id];
                if(target_hypernode.replication_fpga_labels.find(replication_fpga_id) != target_hypernode.replication_fpga_labels.end() && gain_priority_queues[replication_fpga_id].contains(neighbour_master_id)) {
                    updateGain_deleted_node_as_target_deleting_node_as_target(target_hypernode, he, replication_fpga_id);
                }
            }
        }
    }
};
