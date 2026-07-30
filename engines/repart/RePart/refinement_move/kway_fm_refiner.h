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

#include "../io/fpga_manager.h"
#include "../datastructure/hypergraph.h"
#include "../datastructure/fast_reset_flag_array.h"
#include "../datastructure/binary_heap.h"
#include "refiner_base.h"

using PrioQueueRefine = BinaryMaxHeap<int, int>;

class KWayFMRefiner{
    public:
    Hypergraph& hypergraph;
    std::vector<PrioQueueRefine> kpq;
    std::vector<int> edge_hop_distance_cost;
    FMRefinerBase refiner_base;

    FastResetFlagArray<int> activated_nodes;
    FastResetFlagArray<int> activated_hyperedges;
    FastResetFlagArray<int> outdated_rating;
    std::vector<int> &fpga_comm_weight_sum;


    KWayFMRefiner(Hypergraph& hg, 
                  std::vector<std::vector<int>>& fpga_resource_usage, 
                  std::vector<int>& fpga_comm_weight_sum) : 
        hypergraph(hg),
        edge_hop_distance_cost(hg.hyperedges.size()),
        activated_hyperedges(hg.hyperedges.size()),
        activated_nodes(hg.hypernodes.size()),
        refiner_base(hg, fpga_resource_usage),
        outdated_rating(hg.hypernodes.size()),
        fpga_comm_weight_sum(fpga_comm_weight_sum) {
            for (int i = 0; i < hypergraph.fpga_manager.fpga_num; ++i) {
                kpq.push_back(BinaryMaxHeap<int, int>(hg.hypernodes.size()));
            }
        }

    int refine()
    {
        int total_gain = 0;
        while(1) {
            activated_hyperedges.reset();
            activated_nodes.reset();
            outdated_rating.reset();
            refiner_base.initActivateNodesEdges(activated_hyperedges, activated_nodes);
            computeAllEdgeHopDistanceCost();

            rateAllActivatedHypernodes();
            
            int this_gain = 0;
            while (1)
            {
                int max_to_part = getMaxGainNode();
                if (max_to_part == -1)
                    break;
                int max_gain_node = kpq[max_to_part].top();
                int max_from_part = hypergraph.hypernodes[max_gain_node].label;


                if (outdated_rating[max_gain_node]) {
                    rateOneNodeToAllParts(max_gain_node);
                }
                else {
                    if (refiner_base.moveIsFeasible(max_gain_node, hypergraph.hypernodes[max_gain_node].label, max_to_part))
                    {
                        std::vector<int> comm_change(hypergraph.fpga_manager.fpga_num, 0);
                        if (!checkAndChangeComm(max_gain_node, max_from_part, max_to_part, comm_change)) {
                            kpq[max_to_part].pop();
                            continue;
                        }

                        this_gain += kpq[max_to_part].topKey();
                        moveHypernode(max_gain_node, max_from_part, max_to_part);

                        updateGainAndCost(max_gain_node, max_from_part, max_to_part);
                        invalidateRating(max_gain_node);
                        rateOneNodeToAllParts(max_gain_node);
                    }
                    else {
                        kpq[max_to_part].pop();
                    }
                }

            }
            if (this_gain <= 0) {
                break;
            }
            total_gain += this_gain;
        }

        return total_gain;
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

    void moveHypernode(const int u, const int from_part, const int to_part) {
        refiner_base.moveHypernode(u, from_part, to_part);
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source) {
            hypergraph.hyperedges[he].source_part_id = to_part;
        }
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target) {
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
                edge.target_pins_in_part[from_part]--;
                edge.target_pins_in_part[to_part]++;
            }
        }

        for (int i = 0; i < 8; ++i) {
            refiner_base._fpga_resource_usage[to_part][i] += hypergraph.hypernodes[u].weights[i];
            refiner_base._fpga_resource_usage[from_part][i] -= hypergraph.hypernodes[u].weights[i];
        }

        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
            activated_hyperedges.set(he, true);
        }
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target){
            activated_hyperedges.set(he, true);
        }

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

    void evaluateFPGACommChange(const int hn, const int from_part, const int to_part,
                                std::vector<int>& comm_change) {
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_source) {
            const Hyperedge& edge = hypergraph.hyperedges[he];
            bool in_edge = 1;
            int out_target_num = 0;
            int the_out_target = -1;

            bool source_rep_in_from_part = hypergraph.hypernodes[hn].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[hn].replication_fpga_labels.end();
            bool source_rep_in_to_part = hypergraph.hypernodes[hn].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[hn].replication_fpga_labels.end();

            for (int p = 0; p < hypergraph.fpga_manager.fpga_num; ++p) {
                if (edge.target_pins_in_part[p] > 0 and p != from_part) {
                    in_edge = 0;
                    out_target_num++;
                    the_out_target = p;
                }
            }
            if (in_edge) {
                comm_change[from_part] += edge.weight;
                comm_change[to_part] += edge.weight;
            }
            else {
                if (edge.target_pins_in_part[to_part] == 0) {
                    comm_change[to_part] += edge.weight;
                }
                if (edge.target_pins_in_part[from_part] == 0) {
                    comm_change[from_part] -= edge.weight;
                    if (out_target_num == 1 and to_part == the_out_target) {
                        comm_change[to_part] -= edge.weight;
                    }
                }
            }
        }
        for (const int he : hypergraph.hypernodes[hn].incident_nets_as_target) {
            const Hyperedge& edge = hypergraph.hyperedges[he];
            bool in_edge = 1;
            int out_target_num = 0;
            int source_part = edge.source_part_id;
            int source_node = hypergraph._incidence_array[edge._begin];
            for (int p = 0; p < hypergraph.fpga_manager.fpga_num; ++p) {
                if (edge.target_pins_in_part[p] > 0 and p != source_part) {
                    in_edge = 0;
                    out_target_num++;
                }
            }
            bool source_rep_in_from_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();
            bool source_rep_in_to_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();

            if (source_rep_in_from_part and source_rep_in_to_part) {
                continue;
            }
            else if (source_rep_in_from_part and !source_rep_in_to_part) {
                if (edge.target_pins_in_part[to_part] == 0 and to_part != source_part) {
                    comm_change[to_part] += edge.weight;
                    if (in_edge) {
                        comm_change[source_part] += edge.weight;
                    }
                }
            }
            else if (!source_rep_in_from_part and source_rep_in_to_part) {
                if (edge.target_pins_in_part[from_part] == 1 and from_part != source_part) {
                    comm_change[from_part] -= edge.weight;
                    if (out_target_num == 1) {
                        comm_change[source_part] -= edge.weight;
                    }
                }
            }
            else {
                if (in_edge) {
                    comm_change[from_part] += edge.weight;
                    comm_change[to_part] += edge.weight;
                }
                else {
                    if (edge.target_pins_in_part[to_part] == 0 and to_part != source_part) {
                        comm_change[to_part] += edge.weight;
                    }
                    if (edge.target_pins_in_part[from_part] == 1 and from_part != source_part) {
                        comm_change[from_part] -= edge.weight;
                        if (out_target_num == 1 and to_part == source_part) {
                            comm_change[to_part] -= edge.weight;
                        }
                    }
                }
            }
        }
    }


    void updateGainAndCost(const int u, const int from_part, const int to_part) {
        
        FPGAManager& fpga_manager = hypergraph.fpga_manager;
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source) {
            Hyperedge& edge = hypergraph.hyperedges[he];
            int new_cost = refiner_base.hopDistanceCost(hypergraph, he, fpga_manager);
            edge_hop_distance_cost[he] = new_cost;
        }

        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target) {
            Hyperedge& edge = hypergraph.hyperedges[he];
            int source_node = hypergraph._incidence_array[edge._begin];
            int source_part = edge.source_part_id;
            bool source_rep_in_from_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();
            bool source_rep_in_to_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();

            int delta_hop_distance = 0;
            if (source_rep_in_from_part and source_rep_in_to_part) {
                continue;
            }
            else if (source_rep_in_from_part and !source_rep_in_to_part) {
                if (edge.target_pins_in_part[to_part] == 1) {
                    delta_hop_distance += fpga_manager.getHopDistance(source_part, to_part);
                }
            }
            else if (!source_rep_in_from_part and source_rep_in_to_part) {
                if (edge.target_pins_in_part[from_part] == 0) {
                    delta_hop_distance -= fpga_manager.getHopDistance(source_part, from_part);
                }
            }
            else {
                if (edge.target_pins_in_part[to_part] == 1) {
                    delta_hop_distance += fpga_manager.getHopDistance(source_part, to_part);
                }
                if (edge.target_pins_in_part[from_part] == 0) {
                    delta_hop_distance -= fpga_manager.getHopDistance(source_part, from_part);
                }
            }
            edge_hop_distance_cost[he] += delta_hop_distance * edge.weight;
        }
    }

    int getMaxGainNode() {
        int max_gain = -std::numeric_limits<int>::max();
        int max_to_part = -1;
        for (int i = 0; i < kpq.size(); ++i) {
            if (kpq[i].empty())
                continue;
            int tmp_gain = kpq[i].topKey();
            if (tmp_gain > max_gain || (tmp_gain == max_gain && rand()%2)) {
                max_gain = tmp_gain;
                max_to_part = i;
            }
        }
        return max_to_part;
    }

    void rateAllActivatedHypernodes() {
        for (const Hypernode& hn : hypergraph.hypernodes) {
            if (hn.deleted || activated_nodes[hn.id] == 0)
                continue;
            rateOneNodeToAllParts(hn.id);
        }
    }

    void rateOneNodeToAllParts(const int u) {
        std::unordered_map<int, int> tmp_ratings;
        FPGAManager& fpga_manager = hypergraph.fpga_manager;

        int from_part = hypergraph.hypernodes[u].label;
        int to_part = -1;
        
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
            if (!activated_hyperedges[he])
                continue;
            const Hyperedge& edge = hypergraph.hyperedges[he];
            for (int tmp_to = 0; tmp_to < fpga_manager.fpga_num; ++tmp_to) {
                if (edge.target_pins_in_part[tmp_to] > 0 and tmp_to != from_part)
                    tmp_ratings[tmp_to] += 0;
            }
        }
        for (const int he : hypergraph.hypernodes[u].incident_nets_as_target){
            if (!activated_hyperedges[he]) 
                continue;
            const Hyperedge& edge = hypergraph.hyperedges[he];
            bool u_rep_in_source_part = hypergraph.hypernodes[u].replication_fpga_labels.find(edge.source_part_id) != hypergraph.hypernodes[u].replication_fpga_labels.end();
            if (edge.source_part_id != from_part and !u_rep_in_source_part)
                tmp_ratings[edge.source_part_id] += 0;
            for (int tmp_to = 0; tmp_to < fpga_manager.fpga_num; ++tmp_to) {
                bool u_rep_in_target_part = hypergraph.hypernodes[u].replication_fpga_labels.find(tmp_to) != hypergraph.hypernodes[u].replication_fpga_labels.end();
                if (edge.target_pins_in_part[tmp_to] > 0 and tmp_to != from_part and !u_rep_in_target_part)
                    tmp_ratings[tmp_to] += 0;
            }
        }

        for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
            const Hyperedge& edge = hypergraph.hyperedges[he];
            int init_cost = edge_hop_distance_cost[he];

            for (auto tmp_to : tmp_ratings)
            {
                if (!refiner_base.moveIsFeasible(u, from_part, tmp_to.first))
                    continue;

                bool hop_distance_valid = 1;
                int to_part = tmp_to.first; 
                int hop_distance = 0;

                for (int p = 0; p < fpga_manager.fpga_num; ++p) {
                    if (edge.target_pins_in_part[p] > 0) {
                        if (fpga_manager.getHopDistance(to_part, p) >= 0) {
                            hop_distance += fpga_manager.getHopDistance(to_part, p);
                        }
                        else {
                            hop_distance_valid = 0;
                            tmp_ratings[to_part] -= 100000;
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

            int source_part = edge.source_part_id;
            int source_node = hypergraph._incidence_array[edge._begin];
            bool source_rep_in_from_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(from_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();

            for (auto tmp_to : tmp_ratings) {
                if (!refiner_base.moveIsFeasible(u, from_part, tmp_to.first)) 
                    continue;

                int to_part = tmp_to.first; 
                bool source_rep_in_to_part = hypergraph.hypernodes[source_node].replication_fpga_labels.find(to_part) != hypergraph.hypernodes[source_node].replication_fpga_labels.end();

                int hop_distance = 0;
                if (source_rep_in_from_part and source_rep_in_to_part) {
                    continue;
                }
                else if (source_rep_in_from_part and !source_rep_in_to_part) {
                    if (edge.target_pins_in_part.at(to_part) == 0) {
                        if (fpga_manager.getHopDistance(source_part, to_part) < 0) {
                            tmp_ratings[to_part] -= 100000;
                            continue;
                        }
                        hop_distance += fpga_manager.getHopDistance(source_part, to_part);
                    }
                }
                else if (!source_rep_in_from_part and source_rep_in_to_part) {
                    if (edge.target_pins_in_part.at(from_part) == 1) {
                        hop_distance -= fpga_manager.getHopDistance(source_part, from_part);
                    }
                }
                else {
                    if (edge.target_pins_in_part.at(to_part) == 0) {
                        if (fpga_manager.getHopDistance(source_part, to_part) < 0) {
                            tmp_ratings[to_part] -= 100000;
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

        outdated_rating.set(u, false);
        for (int i = 0; i < kpq.size(); ++i) {
            if (kpq[i].contains(u)) {
                kpq[i].remove(u);
            }
        }
        for (auto p : tmp_ratings) {
            if (p.second > 0) {
                kpq[p.first].push(u, p.second);
            }

        }
    }

    void computeAllEdgeHopDistanceCost() {
        for (int i = 0; i < hypergraph.hyperedges.size(); ++i) {
            if (hypergraph.hyperedges[i].deleted) 
                continue;
            edge_hop_distance_cost[i] = refiner_base.hopDistanceCost(hypergraph, i, hypergraph.fpga_manager);
        }
    }    



};
