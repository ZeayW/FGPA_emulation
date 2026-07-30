#pragma once

#include <algorithm>
#include <bitset>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>
#include <fstream>
#include <sstream>
#include <random>
#include <queue>

#include "../datastructure/hypergraph.h"
#include "lazy_vertex_pair_coarsener_4th.h"
#include "metric.h"

extern int edgesizeThresholdScale;
extern double penaltyPower;

using Coarsener1 = LazyVertexPairCoarsener;
class FullCoarsener{
    public:
    std::vector<Hypergraph> hypergraphs;
    const FPGAManager& fpga_manager;

    FullCoarsener(Hypergraph& hg, const FPGAManager& fpga_manager) : 
        fpga_manager(fpga_manager) {
        Hypergraph new_hypergraph = hg;
        hypergraphs.push_back(std::move(new_hypergraph));
    }

    bool checkCoarsenSame(const std::vector<Hypergraph>& hgs) {
        if (hgs.size() < 2) {
            return false;
        }
        else {
            if (hgs.back()._num_vertices_current == hgs[hgs.size()-2]._num_vertices_current) {
                return true;
            }            
        }
        return false;
    }

    double penaltyPowerGain(int num_final_nodes) {
        return 0.5*log(2)/log((hypergraphs[0]._num_vertices_current+1)/num_final_nodes);
    }

    std::vector<Hypergraph>& fullCoarsener(int num_final_nodes) {
        double penalty_power_gain = penaltyPowerGain(num_final_nodes);
        while (1)
        {
            if (hypergraphs.back()._num_vertices_current <= num_final_nodes) {
                break;
            }
            hypergraphs.emplace_back(hypergraphs.back());

            int coarsen_num = 0;
            if (hypergraphs.back()._num_vertices_current < 4 * num_final_nodes)
            {
                coarsen_num = hypergraphs.back()._num_vertices_current - num_final_nodes;
            }
            else
                coarsen_num = hypergraphs.back()._num_vertices_current/2;
            
            Coarsener1 coarsener(hypergraphs.back(), fpga_manager);
            coarsener.coarsen(coarsen_num);
            

            if (checkCoarsenSame(hypergraphs)) {
                hypergraphs.pop_back();
                if (dynamicEdgeSizeThreshold(hypergraphs.back()) > hypergraphs.back()._num_vertices_current) {
                    break;
                }
                else
                {
                    edgesizeThresholdScale = 2*edgesizeThresholdScale;
                }
            }
            else
            {
                penaltyPower = penaltyPower + penalty_power_gain;
            }
        }

        return hypergraphs;
    }

    void printCoarsenSize() {
        for (int i = 0; i < hypergraphs.size(); i++) {
        }
    }
        
};
