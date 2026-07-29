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

#include "../../boost_1_86_0/include/boost/thread/thread.hpp"
#include "../../boost_1_86_0/include/boost/thread/mutex.hpp"
#include "../../boost_1_86_0/include/boost/thread/shared_mutex.hpp"


using PrioQueue = BinaryMaxHeap<int, float>;
int thread_num = 4;

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
        using namespace std;
        vector<vector<pair<int, VertexPairRating>>> ratings(thread_num);
        auto rateAllHypernodesThread = [&](int thread_id)
        {
            for (int i = thread_id; i < hypergraph.hypernodes.size(); i += 4)
            {
                if (hypergraph.hypernodes[i].deleted)
                    continue;
                const VertexPairRating rating = rateOne(hypergraph, i, fpga_manager);
                ratings[thread_id].push_back({i, rating});
            }
        };

        std::vector<boost::thread> threads;
        for (int i = 0; i < thread_num; i++) {
            threads.push_back(boost::thread(rateAllHypernodesThread, i));
        }    

        for (int i = 0; i < thread_num; i++) {
            threads[i].join();
        }
        for (int i = 0; i < thread_num; i++) {
            for (int j = 0; j < ratings[i].size(); j++) {
                const int hn = ratings[i][j].first;
                const VertexPairRating& rating = ratings[i][j].second;
                if (rating.valid) {
                    pq.push(hn, rating.value);
                    target[hn] = rating.target;
                }
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
            pq.updateKey(hn, rating.value);
            target[hn] = rating.target;
        } else {
            pq.remove(hn);
        }
    }

};
