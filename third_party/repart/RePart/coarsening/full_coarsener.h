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
#include "lazy_vertex_pair_coarsener.h"
#include "ml_coarsener.h"
#include "metric.h"

extern int edgesizeThresholdScale;
extern double penaltyPower;

using Coarsener1 = LazyVertexPairCoarsener;
using Coarsener2 = MLCoarsener;
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
        return 3*log(2)/log(hypergraphs[0]._num_vertices_current/num_final_nodes);
    }

    std::vector<Hypergraph>& fullCoarsener(int num_final_nodes) {
        int coarsen_mode = 1;
        double penalty_power_gain = penaltyPowerGain(num_final_nodes);
        while (1)
        {
            if (hypergraphs.back()._num_vertices_current <= num_final_nodes) {
                break;
            }
            std::cout << "Coarsening !!!!!!!!!!!!!!!" << std::endl;
            hypergraphs.emplace_back(hypergraphs.back());

            int coarsen_num = 0;
            if (hypergraphs.back()._num_vertices_current < 4 * num_final_nodes)
            {
                coarsen_num = hypergraphs.back()._num_vertices_current - num_final_nodes;
                std::cout << "Last Coarsening" << std::endl;
            }
            else
                coarsen_num = hypergraphs.back()._num_vertices_current/2;
            
            if (coarsen_mode == 1)
            {
                Coarsener1 coarsener(hypergraphs.back(), fpga_manager);
                coarsener.coarsen(coarsen_num);
            }
            else
            {
                Coarsener2 coarsener(hypergraphs.back(), fpga_manager);
                coarsener.coarsen(coarsen_num);
            }
            std::cout << hypergraphs.back()._num_vertices_current << std::endl;
            

            if (checkCoarsenSame(hypergraphs)) {
                hypergraphs.pop_back();
                std::cout << "Useless Coarsening!" << std::endl;
                if (dynamicEdgeSizeThreshold(hypergraphs.back()) > hypergraphs.back()._num_vertices_current) {
                    break;
                }
                else
                {
                    edgesizeThresholdScale = 2*edgesizeThresholdScale;
                    coarsen_mode = 1;
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
            std::cout << "Hypergraph " << i << " has " << hypergraphs[i]._num_vertices_current << " vertices" << std::endl;
        }
    }
        
};
