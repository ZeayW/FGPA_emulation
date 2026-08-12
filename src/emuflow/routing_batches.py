"""Independent conflict-batch reconstruction for route refinement."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Set

from .platform import Platform
from .routing import _arc_key, build_directed_graph, demands_from_assignment
from .routing_candidates import ROUTE_MASTER_GENERATORS


def build_route_refinement_batches(
    assignment: Mapping[str, Any],
    platform: Platform,
    candidate_pool: Mapping[str, Any],
    timing_paths: Mapping[str, Any],
) -> Dict[str, Any]:
    """Color paths whose candidate-domain/path footprints do not conflict."""

    demands = demands_from_assignment(assignment, platform)
    demand_index = {demand["id"]: index for index, demand in enumerate(demands)}
    _adjacency, arcs, _capacities = build_directed_graph(
        platform, candidate_pool["constraints"]
    )
    domains_by_demand: Dict[int, Set[str]] = defaultdict(set)
    for candidate in candidate_pool["candidates"]:
        if candidate["generator"] not in {
            *ROUTE_MASTER_GENERATORS,
            "refined-final",
        }:
            continue
        index = demand_index[candidate["demand_id"]]
        for edge in candidate["tree_edges"]:
            key = _arc_key(edge["link"], edge["from"], edge["to"])
            domains_by_demand[index].add(arcs[key]["capacity_key"])

    net_to_demand = {demand["net"]: index for index, demand in enumerate(demands)}
    paths_by_demand: Dict[int, Set[int]] = defaultdict(set)
    path_demands: Dict[int, List[int]] = {}
    for path_index, path in enumerate(timing_paths["paths"]):
        selected = sorted({net_to_demand[net] for net in path["cut_nets"]})
        path_demands[path_index] = selected
        for demand in selected:
            paths_by_demand[demand].add(path_index)

    records = []
    for path_index, path in enumerate(timing_paths["paths"]):
        selected = path_demands[path_index]
        records.append(
            {
                "path_index": path_index,
                "path": path["id"],
                "normalized_slack": path["normalized_slack"],
                "demands": [demands[index]["id"] for index in selected],
                "capacity_domains": sorted(
                    {
                        domain
                        for demand in selected
                        for domain in domains_by_demand[demand]
                    }
                ),
                "affected_paths": sorted(
                    {
                        affected
                        for demand in selected
                        for affected in paths_by_demand[demand]
                    }
                ),
            }
        )
    records.sort(key=lambda item: (item["normalized_slack"], item["path_index"]))
    batches: List[List[Dict[str, Any]]] = []
    for record in records:
        domains = set(record["capacity_domains"])
        affected = set(record["affected_paths"])
        for batch in batches:
            if all(
                domains.isdisjoint(other["capacity_domains"])
                and affected.isdisjoint(other["affected_paths"])
                for other in batch
            ):
                batch.append(record)
                break
        else:
            batches.append([record])
    return {
        "batches": [
            [record["path_index"] for record in batch] for batch in batches
        ],
        "batch_count": len(batches),
        "maximum_parallel_batch": max(map(len, batches), default=0),
    }
