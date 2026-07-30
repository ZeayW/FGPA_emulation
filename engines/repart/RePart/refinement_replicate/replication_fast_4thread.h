#pragma once

#include <vector>
#include <unordered_map>
#include <limits>
#include <iostream>

#include "../datastructure/hypergraph.h"
#include "../io/fpga_manager.h"
#include "../datastructure/binary_heap.h"

#include <boost/thread/thread.hpp>
#include <boost/thread/mutex.hpp>
#include <boost/thread/shared_mutex.hpp>

class Replication {
public:

    Hypergraph& hypergraph; 
    const FPGAManager& fpga_manager; 
    std::vector<std::vector<int>>& resource_usage; 
    std::vector<int>& communication_usage; 
    long long& total_cost; 
    std::vector<BinaryMaxHeap<int, int>> gain_priority_queues; 
    boost::mutex gain_queue_mutex;

    const long long top_k = 10000000; 
    int rep_num = 0; 

    const int thread_num = 4;

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

    void Solve(int gain_threshold, bool& replication_flag) {
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
                    replication_flag = true;
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
        if(!hn.replicable) {
            return;
        }

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

        {
            boost::mutex::scoped_lock lock(gain_queue_mutex);
            for(const auto& fg: fpgas_gain) {
                int target_fpga = fg.first;
                int gain = fg.second;
                gain_priority_queues[target_fpga].push(source_id, gain);
            }
        }
    }

    void calculateGain() {
        const int num_threads = thread_num;
        int num_nodes = hypergraph.hypernodes.size();
        int nodes_per_thread = (num_nodes + num_threads - 1) / num_threads;

        std::vector<boost::thread> threads;

        auto thread_task = [&](int thread_id) {
            int start_index = thread_id * nodes_per_thread;
            int end_index = std::min(start_index + nodes_per_thread, num_nodes);

            for (int i = start_index; i < end_index; ++i) {
                const auto& hn = hypergraph.hypernodes[i];
                if (hn.deleted) continue;

                calculateGain_node(hn);
            }
        };

        for(int thread_id = 0; thread_id < num_threads; ++thread_id) {
            threads.emplace_back(boost::thread(thread_task, thread_id));
        }

        for(auto& thread: threads) {
            thread.join();
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
        if(!hypergraph.hypernodes[node_id].replicable) {
            return false;
        }
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
        if(!hn.replicable) {
            return;
        }

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

        std::unordered_set<int> visited_source_nodes;

        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_target) {
            const auto& he = hypergraph.hyperedges[he_id];
            const auto& source_neighbour_node = hypergraph.hypernodes[hypergraph._incidence_array[he._begin]];
            updateGain_net_as_target_source_node(source_neighbour_node, he, target_fpga, visited_source_nodes);
        }

        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_target) {
            const auto& he = hypergraph.hyperedges[he_id];
            for (int i = he._begin+1; i < he._begin + he._size; ++i) {
                int neighbour_id = hypergraph._incidence_array[i];
                const auto& neighbour_node = hypergraph.hypernodes[neighbour_id];

                if(visited_source_nodes.find(neighbour_id) == visited_source_nodes.end()) {
                    updateGain_net_as_target_target_node(neighbour_node, he, target_fpga);
                }
            }
        }

        for(const auto& he_id: hypergraph.hypernodes[node_id].incident_nets_as_source) {
            const auto& he = hypergraph.hyperedges[he_id];

            for (int i = he._begin+1; i < he._begin + he._size; ++i) {
                int neighbour_id = hypergraph._incidence_array[i];
                const auto& neighbour_node = hypergraph.hypernodes[neighbour_id];
                updateGain_net_as_source_target_node(neighbour_node, target_fpga);
            }
        }
    }


    void updateGain_net_as_target_source_node(const Hypernode& hn, const Hyperedge& he, int target_fpga_rep_node, std::unordered_set<int>& visited_source_nodes) {

        int source_id = hn.id;
        int source_fpga = hn.label;

        if(target_fpga_rep_node != source_fpga && hn.replication_fpga_labels.find(target_fpga_rep_node) == hn.replication_fpga_labels.end()) {
            if(gain_priority_queues[target_fpga_rep_node].contains(source_id)) {
                if(he.target_pins_in_part[target_fpga_rep_node] == 1) {
                    int fpgas_gain_change = calculateGain_Positive(source_fpga, target_fpga_rep_node, he.weight);
                    gain_priority_queues[target_fpga_rep_node].updateKeyBy(source_id, fpgas_gain_change);
                }
            }
            else{
                int fpgas_gain = 0;

                int gain_pos = calculateGain_Positive(source_fpga, target_fpga_rep_node, he.weight);
                fpgas_gain += gain_pos;

                int gain_neg = 0;
                bool can_comm_flag = calculateGain_Negative(source_id, target_fpga_rep_node, gain_neg);
                if(!can_comm_flag) {
                    return;
                }
                    
                fpgas_gain -= gain_neg;
                gain_priority_queues[target_fpga_rep_node].push(source_id, fpgas_gain);

                visited_source_nodes.insert(source_id);
            }
        }
    }


    void updateGain_net_as_target_target_node(const Hypernode& hn, const Hyperedge& he, int target_fpga_rep_node) {

        int will_rep_id = hn.id;

        int net_source_node_id = hypergraph._incidence_array[he._begin];
        int net_source_node_fpga = hypergraph.hypernodes[net_source_node_id].label;
        auto& net_source_node = hypergraph.hypernodes[net_source_node_id];

        if(target_fpga_rep_node == hn.label || hn.replication_fpga_labels.find(target_fpga_rep_node) != hn.replication_fpga_labels.end()) {
            return;
        }

        if(net_source_node.label == target_fpga_rep_node || net_source_node.replication_fpga_labels.find(target_fpga_rep_node) != net_source_node.replication_fpga_labels.end()) {
            return;
        }
        
        if(he.target_pins_in_part[target_fpga_rep_node] > 1) {
            return;
        }

        if(gain_priority_queues[target_fpga_rep_node].contains(will_rep_id)) {
            int fpgas_gain_change = calculateGain_Positive(net_source_node_fpga, target_fpga_rep_node, he.weight);
            gain_priority_queues[target_fpga_rep_node].updateKeyBy(will_rep_id, fpgas_gain_change);
        }
    }


    void updateGain_net_as_source_target_node(const Hypernode& hn, int target_fpga_rep) {

        int source_id = hn.id;
        int source_fpga = hn.label;


        std::unordered_map<int, int> fpgas_gain;
        std::unordered_set<int> visited_fpgas;
        std::unordered_set<int> fpgas_cannot_communicate;

        for(const auto& he_id: hn.incident_nets_as_source) {
            const Hyperedge& he = hypergraph.hyperedges[he_id];


            if(he.target_pins_in_part[target_fpga_rep] == 0) {
                continue;
            }

            int target_fpga = target_fpga_rep;

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
    
};
