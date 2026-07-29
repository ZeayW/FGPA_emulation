#pragma once

#include <algorithm>
#include <bitset>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <assert.h>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>
#include <fstream>
#include <sstream>
#include <random>
#include <limits>
#include <queue>

#include "../datastructure/hypergraph.h"
#include "../datastructure/fast_reset_flag_array.h"
#include "../datastructure/binary_heap.h"
#include "refiner_base.h"

using PrioQueueRefine = BinaryMaxHeap<int, int>;

class FMCutRefiner {
    public:
    Hypergraph& hypergraph;
    PrioQueueRefine pq;
    std::vector<int> target;
    FMRefinerBase refiner_base;

    FastResetFlagArray<int> activated_nodes;
    FastResetFlagArray<int> activated_hyperedges;
    FastResetFlagArray<int> outdated_rating;
    std::vector<int> &fpga_comm_weight_sum;

    FMCutRefiner(Hypergraph& hg, 
                 std::vector<std::vector<int>>& fpga_resource_usage, 
                 std::vector<int>& fpga_comm_weight_sum) : 
        hypergraph(hg),
        pq((hg.hypernodes).size()), 
        target((hg.hypernodes).size()),
        activated_hyperedges(hg.hyperedges.size()),
        activated_nodes(hg.hypernodes.size()),
        outdated_rating(hg.hypernodes.size()),
        refiner_base(hg, fpga_resource_usage),
        fpga_comm_weight_sum(fpga_comm_weight_sum) {}
    

    int refine() {
        activated_nodes.reset();
        activated_hyperedges.reset();
        refiner_base.findBorderHyperedges(activated_hyperedges);
        refiner_base.findBorderNodes(activated_nodes, activated_hyperedges);

        int total_gain = 0;

        rateAllActivatedHypernodes();

        while (!pq.empty()) {
            const int max_gain_node = pq.top();

            if (outdated_rating[max_gain_node]) {
                updatePQandMovementTarget(max_gain_node, rateOne(hypergraph, max_gain_node, hypergraph.fpga_manager));
            }
            else{
                int from_part = hypergraph.hypernodes[max_gain_node].label;
                int to_part = target[max_gain_node];
                assert (from_part != to_part);
                if (refiner_base.moveIsFeasible(max_gain_node, from_part, to_part)) {
                    std::vector<int> comm_change(hypergraph.fpga_manager.fpga_num, 0);
                    if (!checkAndChangeComm(max_gain_node, from_part, to_part, comm_change)) {
                        pq.pop();
                        continue;
                    }

                    total_gain += pq.topKey();
                    moveHypernode(max_gain_node, from_part, to_part);
                    invalidateRating(max_gain_node);
                    updatePQandMovementTarget(max_gain_node, rateOne(hypergraph, max_gain_node, hypergraph.fpga_manager));
                
                }
                else {
                    pq.pop();
                }
            }
            
        }
        return total_gain;
    }
    
    bool checkAndChangeComm(const int u, const int from_part, const int to_part,
                            std::vector<int>& comm_change) {
        evaluateFPGACommChange(u, from_part, to_part, comm_change);
        bool valid = 1;
        for (int i = 0; i < hypergraph.fpga_manager.fpga_num; ++i) {
            if (fpga_comm_weight_sum[i] + comm_change[i] > hypergraph.fpga_manager.fpga_info[i][0]) {
                valid = 0;
                break;
            }
        }
        if (valid) {
            for (int i = 0; i < hypergraph.fpga_manager.fpga_num; ++i) {
                fpga_comm_weight_sum[i] += comm_change[i];
            }
        }
        return valid;
    }

    void invalidateRating(const int hn) {
        for (const int he_idx : hypergraph.hypernodes[hn].incident_nets_as_source){
            Hyperedge& he = hypergraph.hyperedges[he_idx];
            for (int pin_idx = he._begin; pin_idx < he._begin + he._size; ++pin_idx) {
                if (hypergraph._incidence_array[pin_idx] == hn)
                    continue;
                outdated_rating.set(hypergraph._incidence_array[pin_idx], true);
            }
        }
        for (const int he_idx : hypergraph.hypernodes[hn].incident_nets_as_target){
            Hyperedge& he = hypergraph.hyperedges[he_idx];
            for (int pin_idx = he._begin; pin_idx < he._begin + he._size; ++pin_idx) {
                if (hypergraph._incidence_array[pin_idx] == hn)
                    continue;
                outdated_rating.set(hypergraph._incidence_array[pin_idx], true);
            }
        }
    }

    void updatePQandMovementTarget(const int hn, const VertexMoveRating& rating) {
        outdated_rating.set(hn, false);
        if (rating.valid) {
            if (pq.contains(hn))
                pq.updateKey(hn, rating.value);
            else
                pq.push(hn, rating.value);
            target[hn] = rating.to_part;
        }
        else {
            if (pq.contains(hn))
                pq.remove(hn);
        }
    }

    void rateAllActivatedHypernodes() {
        int rated_nodes = 0;
        for (const Hypernode& hn : hypergraph.hypernodes) {
            if (hn.deleted == 1 || activated_nodes[hn.id] == 0) {
                continue;
            }
            rated_nodes++;
            const VertexMoveRating rating = rateOne(hypergraph, hn.id, hypergraph.fpga_manager);
            if (rating.valid) {
                assert(rating.to_part >= 0);
                pq.push(hn.id, rating.value);
                target[hn.id] = rating.to_part;
            }
        }
    }


    void moveHypernode(const int hn, const int from_part, const int to_part) {
        assert(hypergraph.hypernodes[hn].label == from_part);
        refiner_base.moveHypernode(hn, from_part, to_part);

        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_source){
            Hyperedge& edge = hypergraph.hyperedges[he];
            assert(edge.source_part_id == from_part);
            edge.source_part_id = to_part;
        }
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_target){
            Hyperedge& edge = hypergraph.hyperedges[he];
            int source_node = hypergraph._incidence_array[edge._begin];
            bool source_rep_in_from_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();
            bool source_rep_in_to_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();
            
            if (source_rep_in_from_part and source_rep_in_to_part) {
                continue;
            }
            else if (source_rep_in_from_part and !source_rep_in_to_part) {
                edge.target_pins_in_part[to_part]++;
            }
            else if (!source_rep_in_from_part and source_rep_in_to_part) {
                edge.target_pins_in_part[from_part]--;
            }
            else {
                assert(edge.target_pins_in_part[from_part] > 0);
                edge.target_pins_in_part[from_part]--;
                edge.target_pins_in_part[to_part]++;
            }
        }
        for (int i = 0; i < 8; ++i) {
            refiner_base._fpga_resource_usage[to_part][i] += hypergraph.hypernodes[hn].weights[i];
            assert(refiner_base._fpga_resource_usage[to_part][i] <= hypergraph.fpga_manager.fpga_info[to_part][i+1]);
            refiner_base._fpga_resource_usage[from_part][i] -= hypergraph.hypernodes[hn].weights[i];
        }

        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_source){
            activated_hyperedges.set(he, true);
        }
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_target){
            activated_hyperedges.set(he, true);
        }

    }

    void evaluateFPGACommChange(const int hn, const int from_part, const int to_part, 
                                std::vector<int>& comm_change) {
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_source) {
            bool in_edge = 1;
            int out_target_num = 0;
            int the_out_target = -1;
            for (auto p : hypergraph.hyperedges[he].target_pins_in_part) {
                if (p.second > 0 and p.first != from_part) {
                    in_edge = 0;
                    out_target_num++;
                    the_out_target = p.first;
                }
            }
            if (in_edge) {
                comm_change[from_part] += hypergraph.hyperedges[he].weight;
                comm_change[to_part] += hypergraph.hyperedges[he].weight;
            }
            else {
                if (hypergraph.hyperedges[he].target_pins_in_part[to_part] == 0) {
                    comm_change[to_part] += hypergraph.hyperedges[he].weight;
                }
                if (hypergraph.hyperedges[he].target_pins_in_part[from_part] == 0) {
                    comm_change[from_part] -= hypergraph.hyperedges[he].weight;
                    if (out_target_num == 1 and to_part == the_out_target) {
                        comm_change[to_part] -= hypergraph.hyperedges[he].weight;
                    }
                }
            }
        }
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_target) {
            bool in_edge = 1;
            int out_target_num = 0;
            int source_part = hypergraph.hyperedges[he].source_part_id;
            for (auto p : hypergraph.hyperedges[he].target_pins_in_part) {
                if (p.second > 0 and p.first != source_part) {
                    in_edge = 0;
                    out_target_num++;
                }
            }
            if (in_edge) {
                comm_change[from_part] += hypergraph.hyperedges[he].weight;
                comm_change[to_part] += hypergraph.hyperedges[he].weight;
            }
            else {
                if (hypergraph.hyperedges[he].target_pins_in_part[to_part] == 0 and to_part != source_part) {
                    comm_change[to_part] += hypergraph.hyperedges[he].weight;
                }
                if (hypergraph.hyperedges[he].target_pins_in_part[from_part] == 1 and from_part != source_part) {
                    comm_change[from_part] -= hypergraph.hyperedges[he].weight;
                    if (out_target_num == 1 and to_part == source_part) {
                        comm_change[to_part] -= hypergraph.hyperedges[he].weight;
                    }
                }
            }
        }
    }


    VertexMoveRating rateOne(const Hypergraph& hypergraph, const int u, const FPGAManager& fpga_manager) {
        std::unordered_map<int, int> tmp_ratings;

        int from_part = hypergraph.hypernodes[u].label;
        int to_part = -1;
        
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
            if (!activated_hyperedges[he])
                continue;
            const Hyperedge& edge = hypergraph.hyperedges[he];
            for (auto tmp_to : edge.target_pins_in_part) {
                if (tmp_to.second > 0 and tmp_to.first != from_part)
                    tmp_ratings[tmp_to.first] += 0;
            }
        }
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target){
            if (!activated_hyperedges[he]) 
                continue;
            const Hyperedge& edge = hypergraph.hyperedges[he];
            tmp_ratings[edge.source_part_id] += 0;
            for (auto tmp_to : edge.target_pins_in_part) {
                if (tmp_to.second > 0 and tmp_to.first != from_part)
                    tmp_ratings[tmp_to.first] += 0;
            }
        }

        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
            const Hyperedge& edge = hypergraph.hyperedges[he];
            int init_cost = refiner_base.hopDistanceCost(hypergraph, he, fpga_manager);

            for (auto tmp_to : tmp_ratings)
            {
                if (!refiner_base.moveIsFeasible(u, from_part, tmp_to.first)) {
                    continue;
                }
                if (tmp_to.first == from_part)
                    continue;
                bool hop_distance_valid = 1;
                int to_part = tmp_to.first; 
                int hop_distance = 0;
                for (auto p : edge.target_pins_in_part) {
                    if (p.second > 0){
                        if (fpga_manager.getHopDistance(to_part, p.first) >= 0) {
                            hop_distance += fpga_manager.getHopDistance(to_part, p.first);
                        }
                        else {
                            hop_distance_valid = 0;
                            tmp_ratings[to_part] -= 10000;
                            break;  
                        }
                    }
                }
                if (hop_distance_valid)
                    tmp_ratings[to_part] += init_cost - hop_distance * edge.weight;
            }
        }

        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target){
            const Hyperedge& edge = hypergraph.hyperedges[he];
            int init_cost = refiner_base.hopDistanceCost(hypergraph, he, fpga_manager);
            int source_part = edge.source_part_id;
            int source_node = hypergraph._incidence_array[edge._begin];
            bool source_rep_in_from_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();


            for (auto tmp_to : tmp_ratings) {
                if (!refiner_base.moveIsFeasible(u, from_part, tmp_to.first)) 
                    continue;
                if (tmp_to.first == from_part)
                    continue;
                int to_part = tmp_to.first; 
                
                bool source_rep_in_to_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();

                int hop_distance = 0;
                if (source_rep_in_from_part and source_rep_in_to_part) {
                    assert(edge.target_pins_in_part.at(from_part) == 0);
                    assert(edge.target_pins_in_part.at(to_part) == 0);
                    continue;
                }
                else if (source_rep_in_from_part and !source_rep_in_to_part) {
                    assert(edge.target_pins_in_part.at(from_part) == 0);
                    if (edge.target_pins_in_part.at(to_part) == 0) {
                        if (fpga_manager.getHopDistance(source_part, to_part) < 0) {
                            tmp_ratings[to_part] -= 10000;
                            continue;
                        }
                        hop_distance += fpga_manager.getHopDistance(source_part, to_part);
                    }
                }
                else if (!source_rep_in_from_part and source_rep_in_to_part) {
                    assert(edge.target_pins_in_part.at(to_part) == 0);
                    if (edge.target_pins_in_part.at(from_part) == 1) {
                        hop_distance -= fpga_manager.getHopDistance(source_part, from_part);
                    }
                }
                else {
                    if (edge.target_pins_in_part.at(to_part) == 0) {
                        if (fpga_manager.getHopDistance(source_part, to_part) < 0) {
                            tmp_ratings[to_part] -= 10000;
                            continue;
                        }
                        hop_distance += fpga_manager.getHopDistance(source_part, to_part);
                    }
                    if (edge.target_pins_in_part.at(from_part) == 1) {
                        hop_distance -= fpga_manager.getHopDistance(source_part, from_part);
                    }
                }
                
                tmp_ratings[to_part] += 0 - hop_distance * edge.weight;
                
            }
        }

        int max_rating = -1;
        int max_to_part = -1;
        for (auto it = tmp_ratings.begin(); it != tmp_ratings.end(); ++it) {
            const int tmp_to_part = it->first;
            const int tmp_rating = it->second;
            if (tmp_rating > max_rating || (tmp_rating == max_rating && rand()%2)) {
                max_rating = tmp_rating;
                max_to_part = tmp_to_part;
            }
        }

        VertexMoveRating result;
        if (max_rating > 0) {
            result.from_part = from_part;
            result.to_part = max_to_part;
            result.value = max_rating;
            result.valid = 1;
        }
        tmp_ratings.clear();
        return result;
    }

};
