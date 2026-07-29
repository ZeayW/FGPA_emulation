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
#include <limits>
#include <queue>

#include "../datastructure/hypergraph.h"
#include "../datastructure/fast_reset_flag_array.h"
#include "../datastructure/binary_heap.h"
#include "kway_fm_refiner.h"

using Refiner = KWayFMRefiner;

class FullRefiner{
    public:
    std::vector<Hypergraph>& hypergraphs;

    FullRefiner(std::vector<Hypergraph>& hgs) : hypergraphs(hgs) {}

    int fullRefiner(int which_layer, 
                    std::vector<std::vector<int>>& fpga_resource_usage,
                    std::vector<int>& fpga_comm_weight_sum) {
        using namespace std;

        int refine_layer = which_layer;

        int refine_gain = 0;
        while (refine_layer >= 0)
        {
                if (refine_layer == 0)
                {
                    hypergraphs[refine_layer].initPinsInPart();
                    Refiner refiner(hypergraphs[refine_layer], fpga_resource_usage, fpga_comm_weight_sum);
                    int this_gain = refiner.refine();
                    refine_gain += this_gain;
                }
               

            
            
            
            if (refine_layer > 0)
                updataFormerLayer(hypergraphs, refine_layer);
            refine_layer--;
        }
        return refine_gain;
    }

};
