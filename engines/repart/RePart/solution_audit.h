#pragma once

#include <stdexcept>
#include <unordered_set>

#include "./datastructure/hypergraph.h"
#include "./io/fpga_manager.h"

// Recompute the contest objective from the materialized solution instead of
// trusting the refiners' incrementally maintained gain/cost state.  A logic
// replica of the source serves every sink located on that FPGA.  Sink replicas
// are additional target locations and therefore also participate in the cost.
inline long long recomputeTotalHopDistance(
    const Hypergraph& hypergraph,
    const FPGAManager& fpga_manager
) {
    long long total_cost = 0;

    for (const Hyperedge& edge : hypergraph.hyperedges) {
        if (edge.deleted) {
            continue;
        }

        const int source_node_id =
            hypergraph._incidence_array[edge._begin];
        const Hypernode& source_node = hypergraph.hypernodes[source_node_id];
        const int source_fpga = source_node.label;

        std::unordered_set<int> target_fpgas;
        for (int pin = edge._begin + 1;
             pin < edge._begin + edge._size;
             ++pin) {
            const Hypernode& target_node =
                hypergraph.hypernodes[hypergraph._incidence_array[pin]];
            target_fpgas.insert(target_node.label);
            target_fpgas.insert(
                target_node.replication_fpga_labels.begin(),
                target_node.replication_fpga_labels.end()
            );
        }

        target_fpgas.erase(source_fpga);
        for (const int replica_fpga : source_node.replication_fpga_labels) {
            target_fpgas.erase(replica_fpga);
        }

        for (const int target_fpga : target_fpgas) {
            const int hop_distance =
                fpga_manager.getHopDistance(source_fpga, target_fpga);
            if (hop_distance < 0) {
                throw std::runtime_error(
                    "final solution violates the maximum-hop constraint"
                );
            }
            total_cost +=
                static_cast<long long>(edge.weight) * hop_distance;
        }
    }

    return total_cost;
}
