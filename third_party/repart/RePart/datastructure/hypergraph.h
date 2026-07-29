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
#include <unordered_set>
#include <utility>
#include <vector>
#include <fstream>
#include <sstream>
#include <queue>
#include <map>

#include "./hash.h"
#include "./fast_reset_flag_array.h"
#include "../io/fpga_manager.h"




class Hyperedge {
public:
    int _begin;
    int _size;
    int weight;
    int id;
    long long hash;
    bool deleted; 
    int source_part_id; 
    std::vector<int> target_pins_in_part; 
    Hyperedge(int b, int s, int w, int i, int fpga_num):_begin(b),_size(s),weight(w),id(i),hash(0),deleted(0),source_part_id(-1) {
        target_pins_in_part.resize(fpga_num);
        for(int i=0;i<fpga_num;++i) {
            target_pins_in_part[i] = 0;
        }
    }
    Hyperedge(int b, int s, int w, int i, long long h, int fpga_num):_begin(b),_size(s),weight(w),id(i),hash(h),deleted(0),source_part_id(-1) {
        target_pins_in_part.resize(fpga_num);
        for(int i=0;i<fpga_num;++i) {
            target_pins_in_part[i] = 0;
        }
    }
};


class Hypernode {
  public:
    int id; 
    std::vector<int> vertex_ids; 
    std::vector<int> weights; 
    std::unordered_set<int> incident_nets_as_source;
    std::unordered_set<int> incident_nets_as_target;
    int label; 
    bool deleted; 
    bool replicable;
    std::unordered_set<int> replication_fpga_labels; 
    Hypernode():id(-1),vertex_ids(),weights(),incident_nets_as_source(),incident_nets_as_target(),label(-1),deleted(0),replicable(true) {}
    Hypernode(int i, std::vector<int> v_ids, std::vector<int> w):id(i),vertex_ids(v_ids),weights(w),incident_nets_as_source(),incident_nets_as_target(),label(-1),deleted(0),replicable(true) {};
};

class Hypergraph {
  public:

    struct Fingerprint {
        int id;
        long long hash;
        int source_id;
    };

    static constexpr int kInvalidID = std::numeric_limits<int>::max();

    FPGAManager fpga_manager;

    std::vector<Hypernode> hypernodes;
    std::vector<Hyperedge> hyperedges;

    std::vector<int> _incidence_array;

    std::vector<Fingerprint> _fingerprints;

    FastResetFlagArray<int> _contained_hypernodes;

    int _num_vertices_initial;
    int _num_vertices_current;
    int _num_hyperedges;



    Hypergraph():hypernodes(),hyperedges(),_incidence_array(),_num_vertices_initial(0),_num_vertices_current(0),_num_hyperedges(0),fpga_manager() {}
    Hypergraph(const Hypergraph& hg):hypernodes(hg.hypernodes),hyperedges(hg.hyperedges),_incidence_array(hg._incidence_array),_num_vertices_initial(hg._num_vertices_initial),_num_vertices_current(hg._num_vertices_current),_num_hyperedges(hg._num_hyperedges),_contained_hypernodes(hg._num_vertices_initial),fpga_manager(hg.fpga_manager) {}
    void operator=(const Hypergraph& hg) {
        hypernodes = hg.hypernodes;
        hyperedges = hg.hyperedges;
        _incidence_array = hg._incidence_array;
        _num_vertices_initial = hg._num_vertices_initial;
        _num_vertices_current = hg._num_vertices_current;
        _num_hyperedges = hg._num_hyperedges;
        _contained_hypernodes = FastResetFlagArray<int>(hg._num_vertices_initial);
        fpga_manager = hg.fpga_manager;
    }


    std::pair<std::vector<int>::const_iterator, std::vector<int>::const_iterator> pins(const int e) const {
        return std::make_pair(_incidence_array.cbegin() + hyperedges[e]._begin, _incidence_array.cbegin() + hyperedges[e]._begin + hyperedges[e]._size);
    }

    void changeNodePart(const int node, const int from_part, const int to_part) {
        if(hypernodes[node].label != from_part) {
        }
        hypernodes[node].label = to_part;
    }

    int pinCountInPart(const int he, const int part) {
        return hyperedges[he].target_pins_in_part.at(part);
    }

    void init_vertex_ids() {
        for(auto it=hypernodes.begin();it!=hypernodes.end();++it) {
            it->vertex_ids.clear();
        }
    }
    long long edgeHash(const int e) const {
        return hyperedges[e].hash;
    }
    int getSourceid(const int e) const {
        return _incidence_array[hyperedges[e]._begin];
    }



    void contract(const int u, const int v) {
        hypernodes[u].replicable =
            hypernodes[u].replicable && hypernodes[v].replicable;
        for(int i=0;i<8;++i) {
            hypernodes[u].weights[i] += hypernodes[v].weights[i];
        }
        if(hypernodes[u].vertex_ids.empty()) {
            hypernodes[u].vertex_ids.push_back(u);
        }
        if(hypernodes[v].vertex_ids.empty()) {
            hypernodes[u].vertex_ids.push_back(v);
        } else {
            for(auto it=hypernodes[v].vertex_ids.begin();it!=hypernodes[v].vertex_ids.end();++it) {
                hypernodes[u].vertex_ids.push_back(*it);
            }
        }
        hypernodes[v].deleted = 1;
        for(const int he : hypernodes[v].incident_nets_as_source) {
            const int pins_begin = hyperedges[he]._begin;
            const int pins_end = hyperedges[he]._begin + hyperedges[he]._size;
            _incidence_array[pins_begin] = u;
            hypernodes[u].incident_nets_as_source.insert(he);
            hyperedges[he].hash += (Hash(u) - Hash(v));
            for(int i = pins_begin + 1; i < pins_end; ++i) {
                if(_incidence_array[i] == u) {
                    _incidence_array[i] = _incidence_array[pins_end-1];
                    _incidence_array[pins_end-1] = u;
                    hypernodes[u].incident_nets_as_target.erase(he);
                    hyperedges[he]._size -= 1;
                    hyperedges[he].hash -= Hash(u);
                    break;
                }
            }
        }


        for(const int he : hypernodes[v].incident_nets_as_target) {
            const int pins_begin = hyperedges[he]._begin;
            const int pins_end = hyperedges[he]._begin + hyperedges[he]._size;
            int u_loc = pins_end;
            int v_loc = pins_end;
            for(int i=pins_begin;i<pins_end;++i) {
                if(_incidence_array[i] == v) {
                    v_loc = i;
                } else if(_incidence_array[i] == u) {
                    u_loc = i;
                }
            }
            if(u_loc == pins_end) { 
                _incidence_array[v_loc] = u;
                hyperedges[he].hash += (Hash(u) - Hash(v));
                hypernodes[u].incident_nets_as_target.insert(he);
            } else { 
                _incidence_array[v_loc] = _incidence_array[pins_end-1];
                _incidence_array[pins_end-1] = v;
                hyperedges[he]._size -= 1;
                hyperedges[he].hash -= Hash(v);
            }
        }
        _num_vertices_current -= 1;
        removeSingleNodeHyperedges(u, v);
        removeParallelHyperedges(u, v);
    }



    void removeIncidentEdgeFromHypernode(const int he, const int hn) {
        if(hypernodes[hn].deleted == 1) return;
        if(!hypernodes[hn].incident_nets_as_source.empty() && hypernodes[hn].incident_nets_as_source.find(he) != hypernodes[hn].incident_nets_as_source.end()) {
            hypernodes[hn].incident_nets_as_source.erase(he);
        }
        
        if(!hypernodes[hn].incident_nets_as_target.empty() && hypernodes[hn].incident_nets_as_target.find(he) != hypernodes[hn].incident_nets_as_target.end()) {
            hypernodes[hn].incident_nets_as_target.erase(he);
        }
    }

    void removeEdge(const int he) {
        if(hyperedges[he].deleted) return;
        const int pins_begin = hyperedges[he]._begin;
        const int pins_end = hyperedges[he]._begin + hyperedges[he]._size;
        for(int i=pins_begin;i<pins_end;++i) {
            const int hn = _incidence_array[i];
            removeIncidentEdgeFromHypernode(he, hn);
        }
        hyperedges[he].deleted = 1;
    }

    void removeSingleNodeHyperedges(const int u, const int v) {
        auto begin_it = hypernodes[u].incident_nets_as_source.begin();
        auto next_it = begin_it;
        for(auto it=begin_it;it!=hypernodes[u].incident_nets_as_source.end();) {
            const int he = *it;
            next_it = std::next(it);
            if(hyperedges[he]._size == 1) {
            removeEdge(he);
            --_num_hyperedges;
            }
            it = next_it;
        }
        begin_it = hypernodes[u].incident_nets_as_target.begin();
        for(auto it=begin_it;it!=hypernodes[u].incident_nets_as_target.end();) {
            const int he = *it;
            next_it = std::next(it);
            if(hyperedges[he]._size == 1) {
                removeEdge(he);
                --_num_hyperedges;
            }
            it = next_it;
        }
    }


    void setEdgeWeight(const int representative, const int to_remove) {
        hyperedges[representative].weight += hyperedges[to_remove].weight;
    }


    bool isParallelHyperedge(const int he) {
        bool is_parallel = true;
        auto pins_it = pins(he);
        ++pins_it.first;
        for(auto pin_it = pins_it.first;pin_it != pins_it.second;++pin_it) {
            if(!_contained_hypernodes[*pin_it]) {
            is_parallel = false;
            break;
            }
        }
        return is_parallel;
    }

    void fillProbeBitset(const int he) {
        _contained_hypernodes.reset();
        auto pins_it = pins(he);
        ++pins_it.first; 
        for(auto pin_it = pins_it.first;pin_it != pins_it.second;++pin_it) {
            _contained_hypernodes.set(*pin_it, true);
        }
    }

    void removeParallelHyperedge(const int representative, const int to_remove) {
        setEdgeWeight(representative, to_remove);
        removeEdge(to_remove);
        --_num_hyperedges;
    }

    void createFingerprints(const int u) {
        _fingerprints.clear();
        for(auto he = hypernodes[u].incident_nets_as_source.begin();he != hypernodes[u].incident_nets_as_source.end();++he) {
            if(hyperedges[*he].deleted) continue;
            _fingerprints.emplace_back(Fingerprint { *he, edgeHash(*he), u });
        }
        for(auto he = hypernodes[u].incident_nets_as_target.begin();he != hypernodes[u].incident_nets_as_target.end();++he) {
            if(hyperedges[*he].deleted) continue;
            _fingerprints.emplace_back(Fingerprint { *he, edgeHash(*he), getSourceid(*he) });
        }
    }

    void removeParallelHyperedges(const int u, const int v) {
        createFingerprints(u);
        std::sort(_fingerprints.begin(), _fingerprints.end(), [](const Fingerprint& a, const Fingerprint& b) { 
            if(a.hash < b.hash) return true;
            if(a.hash == b.hash) return a.source_id < b.source_id;
            return false;
        });

        int i = 0;
        bool filled_probe_bitset = false;
        while(i < _fingerprints.size()) {
            int j = i + 1;
            if(_fingerprints[i].id != kInvalidID) {
                if(hyperedges[_fingerprints[i].id].deleted) {
                    ++i;
                    continue;
                }
                while(j < _fingerprints.size() && _fingerprints[i].hash == _fingerprints[j].hash && _fingerprints[i].source_id == _fingerprints[j].source_id) {
                    if(_fingerprints[j].id != kInvalidID && hyperedges[_fingerprints[i].id]._size == hyperedges[_fingerprints[j].id]._size) {
                    if(hyperedges[_fingerprints[j].id].deleted) {
                        ++j;
                        continue;
                    }
                    if(!filled_probe_bitset) {
                        fillProbeBitset(_fingerprints[i].id);
                        filled_probe_bitset = true;
                    }
                    if(isParallelHyperedge(_fingerprints[j].id)) {
                        removeParallelHyperedge(_fingerprints[i].id, _fingerprints[j].id);
                        _fingerprints[j].id = kInvalidID;
                    }
                    }
                    ++j;
                }
            }
            filled_probe_bitset = false;
            ++i;
        }
    }

    void initPinsInPart() {
        for(auto& he : hyperedges) {
            int he_id = he.id;
            if(he.deleted) {
                continue;
            }
            he.source_part_id = hypernodes[_incidence_array[he._begin]].label;
            const auto& source_replication_fpga_labels = hypernodes[_incidence_array[he._begin]].replication_fpga_labels;

            for(int i = he._begin + 1; i < he._begin + he._size; ++i) {
                int hn_id = _incidence_array[i];
                auto& hn = hypernodes[hn_id];

                int target_fpga = hn.label;

                if(source_replication_fpga_labels.find(target_fpga) == source_replication_fpga_labels.end()) {
                    he.target_pins_in_part[target_fpga]++;
                }
                for(auto target_fpga_replication: hn.replication_fpga_labels) {
                    if(source_replication_fpga_labels.find(target_fpga_replication) == source_replication_fpga_labels.end()) {
                        he.target_pins_in_part[target_fpga_replication]++;
                    }
                }
            }
        }
    }


    void checkHypergraph(bool print_hypernodes=true, bool print_hyperedges=true) const {
        std::cout << "Hypergraph Information:\n";
        
        if(print_hypernodes) {
            std::cout << "\nNumber of Hypernodes: " << _num_vertices_current << "\n";
            for (size_t i = 0; i < hypernodes.size(); ++i) {
                const Hypernode& node = hypernodes[i];
                if(node.deleted) continue;
                std::cout << "\nHypernode ID: " << node.id << "\n";
                std::cout << "\nLabel: " << node.label << "\n";
            }
        }
        

        if(print_hyperedges) {
            for (size_t i = 0; i < hyperedges.size(); ++i) {
                const Hyperedge& edge = hyperedges[i];
                if(edge.deleted) continue;
                std::cout << "Hyperedge ID: " << edge.id << "\n";
            }
        }


        std::cout << "Number of Vertices: " << _num_vertices_current << "\n";
        std::cout << "Number of Hyperedges: " << _num_hyperedges << "\n";
    }

    std::unordered_set<int>& get_source_replication_fpga_labels(const int he_id) {
        return hypernodes[_incidence_array[hyperedges[he_id]._begin]].replication_fpga_labels;
    }
    
    int get_source_part_id(const int he_id) {
        return hypernodes[_incidence_array[hyperedges[he_id]._begin]].label;
    }
};




void findOriginalNodes(
    std::vector<Hypergraph>& hypergraphs,
    int hypergraph_idx,
    int hypernode_idx,
    int fpga_id
) {
    if(hypergraphs[hypergraph_idx].hypernodes[hypernode_idx].label != fpga_id) {
        hypergraphs[hypergraph_idx].hypernodes[hypernode_idx].label = fpga_id;
    }
    if (hypergraph_idx == 0) {
        return;
    }

    const Hypernode& node = hypergraphs[hypergraph_idx].hypernodes[hypernode_idx];
    if (node.vertex_ids.empty()) {
        findOriginalNodes(hypergraphs, hypergraph_idx - 1, hypernode_idx, fpga_id);
    } else {
        for (int prev_node_idx : node.vertex_ids) {
            if(!hypergraphs[hypergraph_idx-1].hypernodes[prev_node_idx].deleted)
                findOriginalNodes(hypergraphs, hypergraph_idx - 1, prev_node_idx, fpga_id);
        }
    }
}

void getOriginalNodesFromLastLayer(std::vector<Hypergraph>& hypergraphs, int last_hypergraph_idx, const std::map<int, int>& best_assignment) {
    for(int hypernode_idx = 0; hypernode_idx < hypergraphs[last_hypergraph_idx].hypernodes.size(); ++hypernode_idx) {
        const Hypernode& node = hypergraphs[last_hypergraph_idx].hypernodes[hypernode_idx];
        
        if(!node.deleted) {
            findOriginalNodes(hypergraphs, last_hypergraph_idx, hypernode_idx, best_assignment.at(node.id));
        }
    }
}

void updateLastLayer(std::vector<Hypergraph>& hypergraphs, int last_hypergraph_idx, const std::map<int, int>& best_assignment) {
    for(int hypernode_idx = 0; hypernode_idx < hypergraphs[last_hypergraph_idx].hypernodes.size(); ++hypernode_idx) {
        Hypernode& node = hypergraphs[last_hypergraph_idx].hypernodes[hypernode_idx];
        if(!node.deleted) {
            node.label = best_assignment.at(node.id);
        }
    }
}

void updataFormerLayer(std::vector<Hypergraph>& hypergraphs, int hypergraph_idx) {
    for(int hypernode_idx = 0; hypernode_idx < hypergraphs[hypergraph_idx].hypernodes.size(); ++hypernode_idx) {
        Hypernode& node = hypergraphs[hypergraph_idx].hypernodes[hypernode_idx];
        if(!node.deleted) {
            if(node.vertex_ids.empty()) {
                hypergraphs[hypergraph_idx-1].hypernodes[hypernode_idx].label = node.label;
                hypergraphs[hypergraph_idx-1].hypernodes[hypernode_idx].replication_fpga_labels = node.replication_fpga_labels;
            } else {
                for(int prev_node_idx : node.vertex_ids) {
                    if(!hypergraphs[hypergraph_idx-1].hypernodes[prev_node_idx].deleted)
                        hypergraphs[hypergraph_idx-1].hypernodes[prev_node_idx].label = node.label;
                        hypergraphs[hypergraph_idx-1].hypernodes[prev_node_idx].replication_fpga_labels = node.replication_fpga_labels;
                }
            }
            
        }
    }
}
