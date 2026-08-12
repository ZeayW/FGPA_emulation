"""Provider-neutral candidate-tree contract for system-level routing."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Dict, Mapping

from .errors import ValidationError
from .platform import Platform
from .routing import (
    _arc_key,
    _validate_route_tree,
    build_directed_graph,
    demands_from_assignment,
    normalize_route_constraints,
    route_link_delay_ns,
)


ROUTE_CANDIDATE_POOL_SCHEMA = "emuflow.route-candidate-pool/v1"
ROUTE_CANDIDATE_POOL_PROVIDER = "native-route-candidate-pool-v1"
ROUTE_CANDIDATE_GENERATORS = (
    "shortest-path-tree",
    "delay-demand-balanced",
    "nearest-terminal-steiner",
    "refined-final",
)


def validate_route_candidate_pool(
    assignment: Mapping[str, Any],
    platform: Platform,
    pool: Mapping[str, Any],
) -> Dict[str, Any]:
    if pool.get("schema") != ROUTE_CANDIDATE_POOL_SCHEMA:
        raise ValidationError(
            "route candidate pool has an unsupported schema"
        )
    if pool.get("provider") != ROUTE_CANDIDATE_POOL_PROVIDER:
        raise ValidationError(
            "route candidate pool has an unsupported provider"
        )
    if pool.get("design") != assignment.get("design"):
        raise ValidationError("route candidate pool design does not match")
    if pool.get("platform") != platform.name:
        raise ValidationError("route candidate pool platform does not match")

    constraints = normalize_route_constraints(
        pool.get("constraints"), platform
    )
    demands = demands_from_assignment(assignment, platform)
    if pool.get("demands") != demands:
        raise ValidationError(
            "route candidate pool demands do not match partition cuts"
        )
    demand_by_id = {demand["id"]: demand for demand in demands}
    _adjacency, arcs, _capacities = build_directed_graph(
        platform, constraints
    )

    locks = pool.get("direction_locks")
    if not isinstance(locks, list):
        raise ValidationError("route candidate pool locks must be an array")
    lock_by_link = {}
    link_by_id = {link.id: link for link in platform.links}
    for index, lock in enumerate(locks):
        if not isinstance(lock, dict):
            raise ValidationError(
                f"route candidate pool lock {index} is invalid"
            )
        link = link_by_id.get(lock.get("link"))
        direction = (lock.get("from"), lock.get("to"))
        if (
            link is None
            or link.direction != "half_duplex"
            or set(direction) != set(link.endpoints)
            or link.id in lock_by_link
        ):
            raise ValidationError(
                "route candidate pool contains an invalid direction lock"
            )
        lock_by_link[link.id] = direction
    expected_locked = {
        link.id for link in platform.links if link.direction == "half_duplex"
    }
    if set(lock_by_link) != expected_locked:
        raise ValidationError(
            "route candidate pool direction-lock coverage is not exact"
        )

    raw_candidates = pool.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValidationError(
            "route candidate pool candidates must be non-empty"
        )
    seen_ids = set()
    seen_pairs = set()
    coverage = defaultdict(set)
    generator_counts = defaultdict(int)
    maximum_hops = 0
    for index, candidate in enumerate(raw_candidates):
        if not isinstance(candidate, dict):
            raise ValidationError(
                f"route candidate pool candidate {index} is invalid"
            )
        demand_id = candidate.get("demand_id")
        generator = candidate.get("generator")
        demand = demand_by_id.get(demand_id)
        if demand is None or generator not in ROUTE_CANDIDATE_GENERATORS:
            raise ValidationError(
                f"route candidate pool candidate {index} identity is invalid"
            )
        candidate_id = f"{demand_id}:{generator}"
        if candidate.get("id") != candidate_id or candidate_id in seen_ids:
            raise ValidationError(
                "route candidate pool candidate IDs are not canonical/unique"
            )
        seen_ids.add(candidate_id)
        pair = (demand_id, generator)
        if pair in seen_pairs:
            raise ValidationError(
                "route candidate pool duplicates a demand/generator pair"
            )
        seen_pairs.add(pair)
        for field in ("net", "source", "sinks", "width_bits"):
            if candidate.get(field) != demand[field]:
                raise ValidationError(
                    f"candidate {candidate_id}.{field} does not match demand"
                )
        if candidate.get("selected") != (generator == "refined-final"):
            raise ValidationError(
                f"candidate {candidate_id}.selected is inconsistent"
            )

        edge_keys, latency, hops = _validate_route_tree(candidate, arcs)
        hop_limit = constraints.get("max_route_hops")
        if hop_limit is not None and hops > hop_limit:
            raise ValidationError(
                f"candidate {candidate_id} exceeds maximum route hops"
            )
        if candidate.get("max_latency_cycles") != latency:
            raise ValidationError(
                f"candidate {candidate_id} latency was not reconstructed"
            )
        for edge in edge_keys:
            locked = lock_by_link.get(edge[0])
            if locked is not None and locked != edge[1:]:
                raise ValidationError(
                    f"candidate {candidate_id} violates a direction lock"
                )

        graph = defaultdict(list)
        for link, source, sink in edge_keys:
            graph[source].append((sink, link))
        delay = {candidate["source"]: 0.0}
        queue = deque([candidate["source"]])
        while queue:
            source = queue.popleft()
            for sink, link in graph[source]:
                delay[sink] = delay[source] + route_link_delay_ns(
                    platform, link, source, sink, constraints
                )
                queue.append(sink)
        predicted = max(delay[sink] for sink in candidate["sinks"])
        reported = candidate.get("predicted_max_delay_ns")
        if (
            isinstance(reported, bool)
            or not isinstance(reported, (int, float))
            or not math.isfinite(float(reported))
            or abs(float(reported) - predicted) > 1.0e-9
        ):
            raise ValidationError(
                f"candidate {candidate_id} delay was not reconstructed"
            )
        coverage[demand_id].add(generator)
        generator_counts[generator] += 1
        maximum_hops = max(maximum_hops, hops)

    expected_demand_ids = set(demand_by_id)
    if set(coverage) != expected_demand_ids:
        raise ValidationError(
            "route candidate pool demand coverage is not exact"
        )
    if any("refined-final" not in coverage[item] for item in expected_demand_ids):
        raise ValidationError(
            "route candidate pool lacks one selected candidate per demand"
        )
    generator_coverage = {
        generator: count
        for generator, count in sorted(generator_counts.items())
    }
    expected_metrics = {
        "demands": len(demands),
        "candidates": len(raw_candidates),
        "generators": len(generator_coverage),
        "candidates_by_generator": generator_coverage,
        "max_route_hops_observed": maximum_hops,
    }
    if pool.get("metrics") != expected_metrics:
        raise ValidationError(
            "route candidate pool metrics were not independently reconstructed"
        )
    return {"status": "pass", **expected_metrics}
