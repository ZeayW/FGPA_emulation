#pragma once

#include <algorithm>
#include <assert.h>
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
#include "../io/fpga_manager.h"

int edgesizeThresholdScale = 100;
double penaltyPower = 0.5;

int dynamicEdgeSizeThreshold(const Hypergraph& hypergraph) {
    float num_0 = hypergraph._num_vertices_initial;
    float num_1 = hypergraph._num_vertices_current;
    return std::max(500.0f, num_0/num_1 * edgesizeThresholdScale);
}

int edgeSizeThreshold(const Hypergraph& hypergraph) {
    return std::max(500, dynamicEdgeSizeThreshold(hypergraph));
}

float heavyEdgeScore(const Hypergraph& hypergraph, const int hyperedgeID) {
    Hyperedge edge = hypergraph.hyperedges[hyperedgeID];
    return (float(edge.weight)) / (edge._size - 1);
}


float heavyNodePenalty(const Hypergraph& hypergraph, const int u, const int v, const FPGAManager& fpga_manager) {
    using namespace std;
    const vector<double>& resource_averages = fpga_manager.resource_averages;
    vector<float> u_weight_p(hypergraph.hypernodes[u].weights.size());
    transform(hypergraph.hypernodes[u].weights.begin(), 
              hypergraph.hypernodes[u].weights.end(), 
              resource_averages.begin(), 
              u_weight_p.begin(), 
              [](float a, double b){return static_cast<float>((b == 0)? 0 : (1000*a/b/b));});
    
    double penalty_sum = inner_product(u_weight_p.begin(), u_weight_p.end(), hypergraph.hypernodes[v].weights.begin(), 0.0);
    return (penalty_sum == 0.0) ? 1 : std::pow(penalty_sum, penaltyPower);
}

bool belowMinWeightFPGA(const Hypergraph& hypergraph, const int u, const int v, const FPGAManager& fpga_manager) {
    using namespace std;
    const vector<double>& resource_minimums = fpga_manager.resource_averages;
    const vector<int>& u_weight = hypergraph.hypernodes[u].weights;
    const vector<int>& v_weight = hypergraph.hypernodes[v].weights;

    vector<int> sum_weight(u_weight.size());
    transform(u_weight.begin(), u_weight.end(), v_weight.begin(), sum_weight.begin(), [](int a, int b){return a+b;});
    vector<int> below_min(u_weight.size());
    transform(sum_weight.begin(), sum_weight.end(), resource_minimums.begin(), below_min.begin(), [](int a, int b){return int(a>b);});

    return (accumulate(below_min.begin(), below_min.end(), 0) == 0);
}

struct VertexPairRating {
    int target; 
    float value;
    bool valid; 

    VertexPairRating(int t, double v):target(t), value(v), valid(1) {}
    VertexPairRating():target(-1), value(-1), valid(0) {}
};


VertexPairRating rateOne(Hypergraph& hypergraph, const int u, const FPGAManager& fpga_manager) {
    using namespace std;
    unordered_map<int, float> tmp_ratings;

    for (const int he : hypergraph.hypernodes[u].incident_nets_as_source){
        const Hyperedge& edge = hypergraph.hyperedges[he];

        if (edge._size > edgeSizeThreshold(hypergraph))
        {
            continue;
        }
        float score = heavyEdgeScore(hypergraph, he);
        for (int v_index = edge._begin; v_index < edge._begin+edge._size; v_index++)
        {
            int v = hypergraph._incidence_array[v_index];
            if (v != u)
                tmp_ratings[v] += score;
        }
    }

    for (const int he : hypergraph.hypernodes[u].incident_nets_as_target){
        const Hyperedge& edge = hypergraph.hyperedges[he];

        if (edge._size > edgeSizeThreshold(hypergraph))
        {
            continue;
        }
        float score = heavyEdgeScore(hypergraph, he);
        for (int v_index = edge._begin; v_index < edge._begin+edge._size; v_index++)
        {
            int v = hypergraph._incidence_array[v_index];
            if (v != u)
                tmp_ratings[v] += score;
        }
    }


    float max_rating = -1;
    int max_target = -1;
    for (auto it = tmp_ratings.begin(); it != tmp_ratings.end(); ++it) {
        const int tmp_target = it->first;
        if (!belowMinWeightFPGA(hypergraph, u, tmp_target, fpga_manager))
        {
            continue;
        }
        float penalty = heavyNodePenalty(hypergraph, u, tmp_target, fpga_manager);
        float the_tmp_rating = it->second / penalty;
        if (the_tmp_rating > max_rating || (the_tmp_rating == max_rating)) {
            max_rating = the_tmp_rating;
            max_target = tmp_target;
        }
    }

    VertexPairRating result;
    if (max_rating > 0)
    {
        result.target = max_target;
        result.value = max_rating;
        result.valid = 1;
    }
    tmp_ratings.clear();

    return result;
}

