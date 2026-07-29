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
#include <queue>

#include "../datastructure/hypergraph.h"
#include "metric.h"
#include "../datastructure/fast_reset_flag_array.h"
#include "../datastructure/binary_heap.h"

using PrioQueue = BinaryMaxHeap<int, float>;

class LazyVertexPairCoarsener {
    public:
    Hypergraph& hypergraph;
    PrioQueue pq;
    std::vector<int> target;
    FastResetFlagArray<> outdated_rating;
    const FPGAManager& fpga_manager;
    
    LazyVertexPairCoarsener(Hypergraph& hg, const FPGAManager& fpga_manager) : 
        hypergraph(hg),
        pq((hg.hypernodes).size()), 
        target((hg.hypernodes).size()),
        outdated_rating((hg.hypernodes).size()),
        fpga_manager(fpga_manager) {}


    void rateAllHypernodes() {
        for (const Hypernode& hn : hypergraph.hypernodes) {
            if (hn.deleted == 1) {
                continue;
            }
            const VertexPairRating rating = rateOne(hypergraph, hn.id, fpga_manager);
            if (rating.valid) {
                assert(rating.target >= 0);

                pq.push(hn.id, rating.value);
                target[hn.id] = rating.target;
            }
        }
    }

    void coarsen(int contract_num) {
        pq.clear();
        int contracted_nodes = 0;
        rateAllHypernodes();

        while (!pq.empty()) {
            if (contracted_nodes == contract_num)
                break;

            const int rep_node = pq.top();

            if (outdated_rating[rep_node]) {
                updatePQandContractionTarget(rep_node, rateOne(hypergraph, rep_node, fpga_manager));
            } else {
                const int contracted_node = target[rep_node];
                hypergraph.contract(rep_node, contracted_node);
                contracted_nodes++;
                if (hypergraph._num_vertices_current % 1000 == 0) {
                    std::cout << "num_vertices_current:" << hypergraph._num_vertices_current << "\n";
                }

                if (pq.contains(contracted_node)) {
                    pq.remove(contracted_node);
                }
                invalidateAffectedHypernodes(rep_node);
                updatePQandContractionTarget(rep_node, rateOne(hypergraph, rep_node, fpga_manager));
            }
        }
    }


    void invalidateAffectedHypernodes(const int rep_node){
        for (const int he_idx : hypergraph.hypernodes[rep_node].incident_nets_as_source) {
            Hyperedge& he = hypergraph.hyperedges[he_idx];
            if (he._size > edgeSizeThreshold(hypergraph)) {
                continue;
            }
            for (int pin_idx = he._begin; pin_idx < he._begin + he._size; pin_idx++) {
                const int pin = hypergraph._incidence_array[pin_idx];
                outdated_rating.set(pin, true);
            }
        }
        for (const int he_idx : hypergraph.hypernodes[rep_node].incident_nets_as_target) {
            Hyperedge& he = hypergraph.hyperedges[he_idx];
            if (he._size > edgeSizeThreshold(hypergraph)) {
                continue;
            }
            for (int pin_idx = he._begin; pin_idx < he._begin + he._size; pin_idx++) {
                const int pin = hypergraph._incidence_array[pin_idx];
                outdated_rating.set(pin, true);
            }
        }
    }
    
    void updatePQandContractionTarget(const int hn, const VertexPairRating& rating) {
        outdated_rating.set(hn, false);
        if (rating.valid) {
            assert(pq.contains(hn));
            pq.updateKey(hn, rating.value);
            target[hn] = rating.target;
        } else {
            assert(pq.contains(hn));
            pq.remove(hn);
        }
    }

};
