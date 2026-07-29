#pragma once

#include <iostream>
#include <vector>
#include <map>
#include <set>
#include <cmath>
#include <algorithm>
#include <limits>
#include <unordered_set>
#include <assert.h>
#include <chrono>
#include <random>

#include "../datastructure/hypergraph.h"


class AssignmentSolver {
public:
    const std::vector<Hypernode>& hypernodes;        
    const std::vector<Hyperedge>& hyperedges;        
    const std::vector<int>& _incidence_array;         
    const std::vector<std::vector<int>>& fpga_info;   
    const std::vector<std::vector<int>>& comm_cost;   
    const int max_hop_distance;                       

    const int nodes_num;                              


    long long best_cost;                                 
    std::map<int, int> best_assignment;               
    std::vector<std::vector<int>> fpgas_resource_usage; 
    std::vector<int> fpgas_comm_weight_sum; 

    const int fpga_num;                 

    std::vector<int> sorted_hypernode_ids;           
    std::vector<int> sorted_fpga_ids;           

    const int cannot_communicate_penalty = 2*max_hop_distance; 



    int flag_finished = 0;                        
    const int when_finished = 256 / fpga_num;     
    const bool only_one_feasible_solution = false;         
    const double finished_threshold = 0.98; 

    std::chrono::steady_clock::time_point start_time; 
    const int time_limit_second; 
    const std::chrono::seconds time_limit = std::chrono::seconds(time_limit_second); 



    std::vector<int> node_sorted_index_backtrack_num = std::vector<int>(nodes_num, 0); 
    const int depth_exit_threshold = 128; 
    int depth_exit_count = 0; 

    std::mt19937 rng; 
    std::uniform_int_distribution<int> dist; 

    AssignmentSolver(const Hypergraph& hg, const FPGAManager& fpga_manager, const int time_limit_second)
        : hypernodes(hg.hypernodes), hyperedges(hg.hyperedges), _incidence_array(hg._incidence_array), nodes_num(hg._num_vertices_current),
          fpga_info(fpga_manager.fpga_info),comm_cost(fpga_manager.hop_distances), max_hop_distance(fpga_manager.max_hop_distance), fpga_num(fpga_info.size()),
          time_limit_second(time_limit_second) {
        best_cost = std::numeric_limits<long long>::max();

        best_assignment = {};

        std::random_device rd;
        rng = std::mt19937(42); 
        dist = std::uniform_int_distribution<int>(0, std::numeric_limits<int>::max());

        sort_hypernodes();
        sort_fpgas();
    }

    std::pair<std::map<int, int>, long long> solve() {
        std::map<int, int> assignment;                         
        std::vector<std::vector<int>> resource_usage(fpga_info.size(), std::vector<int>(8, 0)); 
        std::vector<int> comm_weight_sum(fpga_info.size(), 0); 
        long long total_cost = 0;                              

        std::vector<std::set<int>> fpga_contained_hyperedges(fpga_num, std::set<int>());  



        start_time = std::chrono::steady_clock::now();

        backtrack(0, assignment, resource_usage, comm_weight_sum, fpga_contained_hyperedges, total_cost);


        return {best_assignment, best_cost};
    }

private:
    int rate_connection(int u, int v) {
        assert(hypernodes[u].deleted == false && "rate_connection: hypernode u is deleted");
        assert(hypernodes[v].deleted == false && "rate_connection: hypernode v is deleted");
        int score = 0;
        for (const int he : hypernodes[u].incident_nets_as_target) {
            assert(hyperedges[he].deleted == false && "rate_connection: hyperedge is deleted");
            const Hyperedge& edge = hyperedges[he];
            if (edge.deleted) continue;
            assert(edge._begin >= 0 && edge._begin < _incidence_array.size());
            if(_incidence_array[edge._begin] == v) {
                score += edge.weight;
            }
        }
        for (const int he : hypernodes[v].incident_nets_as_target) {
            assert(hyperedges[he].deleted == false && "rate_connection: hyperedge is deleted");
            const Hyperedge& edge = hyperedges[he];
            if (edge.deleted) continue;
            assert(edge._begin >= 0 && edge._begin < _incidence_array.size());
            if(_incidence_array[edge._begin] == u) {
                score += edge.weight;
            }
        }
        return score;
    }


    void sort_hypernodes() {
        std::vector<std::pair<int, int>> connection_counts; 
        for (const auto& hn : hypernodes) {
            if (hn.deleted) continue;
            int count = hn.incident_nets_as_target.size();
            for(auto& he : hn.incident_nets_as_source) {
                assert(hyperedges[he].deleted == false);
                count += (hyperedges[he]._size - 1);
            }

            connection_counts.emplace_back(hn.id, count);
        }

        std::sort(connection_counts.begin(), connection_counts.end(),
                  [](const std::pair<int, int>& a, const std::pair<int, int>& b) -> bool {
                      return a.second > b.second;
                  });



        assert(nodes_num == connection_counts.size() && "nodes_num != connection_counts.size()");
        std::unordered_set<int> hypernode_used;
        for(int i = 0; i < nodes_num; ++i) {
            if(i == 0){
                sorted_hypernode_ids.push_back(connection_counts[0].first);
                hypernode_used.insert(connection_counts[0].first);
            }
            else{
                int max_connection = -1;
                int max_connection_id = -1;
                for(int j = 0; j < nodes_num; ++j) {
                    if(hypernode_used.find(connection_counts[j].first) != hypernode_used.end()) {
                        continue;
                    }
                    int connection = rate_connection(sorted_hypernode_ids.back(), connection_counts[j].first);
                    if(connection > max_connection) {
                        max_connection = connection;
                        max_connection_id = j;
                    }
                }

                if(max_connection_id != -1) {
                    hypernode_used.insert(connection_counts[max_connection_id].first);
                    sorted_hypernode_ids.push_back(connection_counts[max_connection_id].first);
                }
                else{
                    for(int j = 0; j < nodes_num; ++j) {
                        if(hypernode_used.find(connection_counts[j].first) != hypernode_used.end()) {
                            continue;
                        }
                        sorted_hypernode_ids.push_back(connection_counts[j].first);
                        hypernode_used.insert(connection_counts[j].first);
                    }
                }
            }
        }
        




        assert(nodes_num == sorted_hypernode_ids.size());
    }

    void sort_fpgas() {
        std::vector<std::pair<int, int>> connection_counts; 
        for (int i = 0; i < fpga_num; ++i) {
            int count = 0;
            for(int j = 0; j < fpga_num; j++) {
                if (i == j) continue;
                if (comm_cost[i][j] != -1) {
                    count += comm_cost[i][j];
                }
                else{
                    count += cannot_communicate_penalty;
                }
            }
            connection_counts.emplace_back(i, count);
        }

        std::sort(connection_counts.begin(), connection_counts.end(),
                  [](const std::pair<int, int>& a, const std::pair<int, int>& b) -> bool {
                      return a.second < b.second;
                  });

        
        std::vector<bool> fpga_used(fpga_num, false);
        for(int i = 0; i < fpga_num; ++i) {
            if(i == 0){
                sorted_fpga_ids.push_back(connection_counts[0].first);
                fpga_used[connection_counts[i].first] = true;
            }
            else{
                int min_distance = std::numeric_limits<int>::max();
                int min_distance_id = -1;
                for(int j = 0; j < fpga_num; ++j) {
                    if(fpga_used[connection_counts[j].first]) {
                        continue;
                    }
                    if(comm_cost[sorted_fpga_ids.back()][connection_counts[j].first] < min_distance && comm_cost[sorted_fpga_ids.back()][connection_counts[j].first] != -1) {
                        min_distance = comm_cost[sorted_fpga_ids.back()][connection_counts[j].first];
                        min_distance_id = j;
                    }
                }
                if(min_distance_id != -1) {
                    fpga_used[connection_counts[min_distance_id].first] = true;
                    sorted_fpga_ids.push_back(connection_counts[min_distance_id].first);
                }
                else{
                    for(int j = 0; j < fpga_num; ++j) {
                        if(fpga_used[connection_counts[j].first]) {
                            continue;
                        }
                        sorted_fpga_ids.push_back(connection_counts[j].first);
                        fpga_used[connection_counts[j].first] = true;
                    }
                }
            }
        }

        assert(sorted_fpga_ids.size() == fpga_num);
    }

    const Hypernode& get_hypernode_by_id(int id) const {
        return hypernodes[id];
    }

    void backtrack(int node_sorted_index,
                   std::map<int, int>& assignment,
                   std::vector<std::vector<int>>& resource_usage,
                   std::vector<int>& comm_weight_sum,
                   std::vector<std::set<int>>& fpga_contained_hyperedges,
                   long long total_cost) {

        assert(node_sorted_index >= 0 && node_sorted_index <= nodes_num && "node_sorted_index out of range in backtrack");

        if(flag_finished == when_finished) {
            return;
        }

        if (node_sorted_index == nodes_num) {
            if((double)total_cost > (double)best_cost * finished_threshold) {
                ++flag_finished;
            }
            else{
                flag_finished = 0;
            }

            if (total_cost < best_cost) {
                best_cost = total_cost;
                best_assignment = assignment;
                fpgas_resource_usage = resource_usage;
                fpgas_comm_weight_sum = comm_weight_sum;

                if(only_one_feasible_solution) {
                    flag_finished = when_finished;
                }
            }

            auto now = std::chrono::steady_clock::now();
            if (now - start_time > time_limit) {
                flag_finished = when_finished;
            }
            return;
        }

        if(node_sorted_index_backtrack_num[node_sorted_index] > depth_exit_threshold || flag_finished > 0) {
            node_sorted_index_backtrack_num[node_sorted_index] = 0;
            if(flag_finished > 0) {
                flag_finished = 0;
            }
            depth_exit_count = dist(rng) % ((node_sorted_index+2) / 2);


            auto now = std::chrono::steady_clock::now();
            if (now - start_time > time_limit) {
                flag_finished = when_finished;
            }
            return;
        }

        if(node_sorted_index < nodes_num) {
            ++node_sorted_index_backtrack_num[node_sorted_index];
        }


        assert(node_sorted_index < sorted_hypernode_ids.size() && "node_sorted_index exceeds sorted_hypernode_ids size");
        int hypernode_id = sorted_hypernode_ids[node_sorted_index];
        assert(hypernodes[hypernode_id].deleted == false && "backtrack: hypernode is deleted");
        const Hypernode& hn = get_hypernode_by_id(hypernode_id);
        

        for (auto f_id : sorted_fpga_ids) {

            assert(f_id >= 0 && f_id < fpga_num && "f_id out of range in sorted_fpga_ids");

            bool feasible = true;
            for (int i = 0; i < 8; ++i) {
                if (resource_usage[f_id][i] + hn.weights[i] > fpga_info[f_id][i + 1]) { 
                    feasible = false;
                    break;
                }
            }
            if (!feasible) {
                continue; 
            }

            long long incremental_cost = 0;
            bool communication_feasible = true;
            std::map<int, int> added_comm_weights; 

            std::vector<std::set<int>> fpga_contained_hyperedges_new = fpga_contained_hyperedges;

            for (const auto& he_id : hn.incident_nets_as_source) {
                assert(fpga_contained_hyperedges_new[f_id].find(he_id) == fpga_contained_hyperedges_new[f_id].end());
                const Hyperedge& he = hyperedges[he_id];
                if (he.deleted) continue;

                for (int i = 1; i < he._size; ++i) {
                    int target_node = _incidence_array[he._begin + i];
                    auto it = assignment.find(target_node);
                    if (it != assignment.end()) {
                        int target_fpga_id = it->second;
                        if(fpga_contained_hyperedges_new[target_fpga_id].find(he_id) != fpga_contained_hyperedges_new[target_fpga_id].end()) {
                            continue;
                        }

                        if(target_fpga_id == f_id) {
                            continue;
                        }

                        int C = comm_cost[f_id][target_fpga_id];
                        if (C == -1) {
                            communication_feasible = false;
                            break; 
                        }

                        fpga_contained_hyperedges_new[target_fpga_id].insert(he_id);

                        if(fpga_contained_hyperedges_new[f_id].find(he_id) == fpga_contained_hyperedges_new[f_id].end()) {
                            fpga_contained_hyperedges_new[f_id].insert(he_id);
                            added_comm_weights[f_id] += he.weight;
                        }
                        
                        incremental_cost += static_cast<int>(he.weight) * C;

                        fpga_contained_hyperedges_new[target_fpga_id].insert(he_id);
                        added_comm_weights[target_fpga_id] += he.weight;
                    }
                }
                if (!communication_feasible) {
                    break;
                }
            }

            if (!communication_feasible) {
                continue; 
            }

            for (const auto& he_id : hn.incident_nets_as_target) {
                if(fpga_contained_hyperedges_new[f_id].find(he_id) != fpga_contained_hyperedges_new[f_id].end()) {
                    continue;
                }
                const Hyperedge& he = hyperedges[he_id];
                if (he.deleted) continue;

                int source_node = _incidence_array[he._begin];
                auto it = assignment.find(source_node);
                if (it != assignment.end()) {
                    int source_fpga_id = it->second;

                    if(source_fpga_id == f_id) {
                        continue;
                    }

                    int C = comm_cost[source_fpga_id][f_id];
                    if (C == -1) {
                        communication_feasible = false;
                        break; 
                    }
                    incremental_cost += static_cast<long long>(he.weight) * C;

                    if(fpga_contained_hyperedges_new[source_fpga_id].find(he_id) == fpga_contained_hyperedges_new[source_fpga_id].end()) {
                        fpga_contained_hyperedges_new[source_fpga_id].insert(he_id);
                        added_comm_weights[source_fpga_id] += he.weight;
                    }
                    fpga_contained_hyperedges_new[f_id].insert(he_id);
                    added_comm_weights[f_id] += he.weight;
                }
            }

            if (!communication_feasible) {
                continue; 
            }

            bool exceeded = false;
            for (const auto& [f, w] : added_comm_weights) {
                if (comm_weight_sum[f] + w > fpga_info[f][0]) { 
                    exceeded = true;
                    break;
                }
            }
            if (exceeded) {
                continue; 
            }

            long long new_total_cost = total_cost + incremental_cost;

            if (new_total_cost >= best_cost) {
                continue;
            }

            std::vector<std::vector<int>> new_resource_usage = resource_usage;
            for (int i = 0; i < 8; ++i) {
                new_resource_usage[f_id][i] += hn.weights[i];
            }

            std::vector<int> new_comm_weight_sum = comm_weight_sum;
            for (const auto& [f, w] : added_comm_weights) {
                new_comm_weight_sum[f] += w;
            }

            assignment[hypernode_id] = f_id;

            backtrack(node_sorted_index + 1, assignment, new_resource_usage, new_comm_weight_sum, fpga_contained_hyperedges_new, new_total_cost);
            
            if(flag_finished == when_finished) {
                return;
            }

            assignment.erase(hypernode_id);

            if(depth_exit_count > 0){
                --depth_exit_count;
                return;
            }
        }
        
    }
};



void AssignmentInitialPartition(std::vector<Hypergraph>& Hypergraphs, const FPGAManager& fpga_manager, const int every_layer_time_limit_second,
                                long long& best_cost, std::map<int, int>& best_assignment,  
                                std::vector<std::vector<int>>& fpgas_resource_usage, std::vector<int>& fpgas_comm_weight_sum, int& which_layer) {
    for(int i = Hypergraphs.size()-1; i >= 0; --i) {
        const Hypergraph &hg = Hypergraphs[i];

        int node_nums = hg._num_vertices_current;
        int fpga_nums = fpga_manager.fpga_num;



        AssignmentSolver ass_solver(hg, fpga_manager, every_layer_time_limit_second);
        std::pair<std::map<int, int>, long long> result = ass_solver.solve();

        best_assignment = result.first;
        best_cost = result.second;

        if (!best_assignment.empty()) {
            fpgas_resource_usage = ass_solver.fpgas_resource_usage;
            fpgas_comm_weight_sum = ass_solver.fpgas_comm_weight_sum;
            which_layer = i;
            break;
        }
    }
    return;
}
