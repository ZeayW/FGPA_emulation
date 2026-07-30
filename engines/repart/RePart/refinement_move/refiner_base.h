#pragma once

#include <limits>
#include <vector>
#include <map>

#include "../datastructure/hypergraph.h"
#include "../io/fpga_manager.h"
#include "../datastructure/fast_reset_flag_array.h"

struct VertexMoveRating {
    int from_part;
    int to_part;
    int value;
    bool valid;
    VertexMoveRating(int f, int t, int v):from_part(f), to_part(t), value(v), valid(1) {}
    VertexMoveRating():from_part(-1), to_part(-1), value(-1), valid(0) {}
};

class FMRefinerBase{
    public:
    Hypergraph& _hg;
    std::vector<std::vector<int>>& _fpga_resource_usage;

    FMRefinerBase(Hypergraph& hypergraph, 
        std::vector<std::vector<int>>& fpga_resource_usage):
        _hg(hypergraph), _fpga_resource_usage(fpga_resource_usage) {}

    
    void initActivateNodesEdges(FastResetFlagArray<int>& border_hyperedges, FastResetFlagArray<int>& border_nodes)
    {
        findBorderHyperedges(border_hyperedges);
        findBorderNodes(border_nodes, border_hyperedges);
    }

    void initPinsInPart() {
        for(int i = 0; i < _hg.hyperedges.size(); ++i) {
            if(_hg.hyperedges[i].deleted) 
                continue;
            _hg.hyperedges[i].source_part_id = _hg.hypernodes[_hg._incidence_array[_hg.hyperedges[i]._begin]].label;
            for(auto hn = _hg._incidence_array.begin()+_hg.hyperedges[i]._begin+1; hn != _hg._incidence_array.begin()+_hg.hyperedges[i]._begin+_hg.hyperedges[i]._size; ++hn) {
                _hg.hyperedges[i].target_pins_in_part[_hg.hypernodes[*hn].label]++;
            }
        }
    }
   
    void findBorderHyperedges(FastResetFlagArray<int>& border_hyperedges) {
        for(int i = 0; i < _hg.hyperedges.size(); ++i) {
            if(_hg.hyperedges[i].deleted) 
                continue;
            if(_hg.hyperedges[i].source_part_id != -1) {
                for(int j = 0; j < _hg.fpga_manager.fpga_num; ++j) {
                    if(j == _hg.hyperedges[i].source_part_id) 
                        continue;
                    if(_hg.hyperedges[i].target_pins_in_part[j] > 0) {
                        border_hyperedges.set(i, true);
                        break;
                    }
                }
            }
        }
    }

    void findBorderNodes(FastResetFlagArray<int>& border_nodes, const FastResetFlagArray<int>& border_hyperedges) {
        for(int i = 0; i < _hg.hyperedges.size(); ++i) {
            if(_hg.hyperedges[i].deleted) 
                continue;
            if(border_hyperedges[i]) {
                for(auto hn = _hg._incidence_array.begin()+_hg.hyperedges[i]._begin; hn != _hg._incidence_array.begin()+_hg.hyperedges[i]._begin+_hg.hyperedges[i]._size; ++hn) {
                    border_nodes.set(*hn, true);
                }
            }
        }
    }


    bool hypernodeIsConnectedToPart(const int pin, const int part) const {
        for(const int he : _hg.hypernodes[pin].incident_nets_as_source) {
            if(_hg.pinCountInPart(he,part) > 0) {
                return true;
            }
        }
        for(const int he : _hg.hypernodes[pin].incident_nets_as_target) {
            if(_hg.hyperedges[he].source_part_id == part || _hg.pinCountInPart(he,part) > 0) {
                return true;
            }
        }
        return false;
    }


    bool moveIsFeasible(const int max_gain_node, const int from_part, const int to_part) {
        for (int i = 0; i < 8; ++i) {
            if (_fpga_resource_usage[to_part][i] + _hg.hypernodes[max_gain_node].weights[i] > _hg.fpga_manager.fpga_info[to_part][i+1]) 
            {
                return false;
            }
        }
        return true;
    }

    void moveHypernode(const int hn, const int from_part, const int to_part) {
        _hg.hypernodes[hn].label = to_part;
    }

    int hopDistanceCost(const Hypergraph& hypergraph, const int he, const FPGAManager& fpga_manager) {
        const Hyperedge& edge = hypergraph.hyperedges[he];
        int from_part = edge.source_part_id;
        int hop_distance = 0;

        for (int p = 0; p < fpga_manager.fpga_num; ++p) {
            if (edge.target_pins_in_part[p] > 0) {
                int a = fpga_manager.getHopDistance(from_part, p);
                if (a < 0) {
                }
                hop_distance += a;
            }
        }
        return hop_distance * edge.weight;
    }


};
