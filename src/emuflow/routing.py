import heapq
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ValidationError
from .io import read_json
from .partition import PARTITION_ASSIGNMENT_SCHEMA
from .platform import BoardLink, Platform


SYSTEM_ROUTES_SCHEMA = "emuflow.system-routes/v1"
SYSTEM_ROUTE_CONSTRAINTS_SCHEMA = "emuflow.system-route-constraints/v1"
ArcKey = Tuple[str, str, str]


def normalize_route_constraints(
    value: Optional[Mapping[str, Any]],
    platform: Platform,
    frame_slots: Optional[int] = None,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    raw: Mapping[str, Any] = value or {}
    if raw and raw.get("schema") != SYSTEM_ROUTE_CONSTRAINTS_SCHEMA:
        raise ValidationError(
            "route constraints.schema: expected "
            f"{SYSTEM_ROUTE_CONSTRAINTS_SCHEMA!r}, "
            f"got {raw.get('schema')!r}"
        )

    raw_frame_slots = raw.get("frame_slots", 32)
    if frame_slots is not None:
        raw_frame_slots = frame_slots
    if (
        isinstance(raw_frame_slots, bool)
        or not isinstance(raw_frame_slots, int)
        or raw_frame_slots <= 0
    ):
        raise ValidationError(
            "route constraints.frame_slots: expected a positive integer"
        )

    raw_iterations = raw.get("max_iterations", 20)
    if max_iterations is not None:
        raw_iterations = max_iterations
    if (
        isinstance(raw_iterations, bool)
        or not isinstance(raw_iterations, int)
        or raw_iterations <= 0
    ):
        raise ValidationError(
            "route constraints.max_iterations: expected a positive integer"
        )

    raw_unavailable = raw.get("unavailable_links", [])
    if not isinstance(raw_unavailable, list) or not all(
        isinstance(link_id, str) for link_id in raw_unavailable
    ):
        raise ValidationError(
            "route constraints.unavailable_links: expected an array of strings"
        )
    link_ids = {link.id for link in platform.links}
    unknown = sorted(set(raw_unavailable) - link_ids)
    if unknown:
        raise ValidationError(
            f"route constraints.unavailable_links: unknown links {unknown}"
        )

    return {
        "schema": SYSTEM_ROUTE_CONSTRAINTS_SCHEMA,
        "frame_slots": raw_frame_slots,
        "max_iterations": raw_iterations,
        "unavailable_links": sorted(set(raw_unavailable)),
    }


def load_route_constraints(
    path: Optional[Path],
    platform: Platform,
    frame_slots: Optional[int] = None,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    value = read_json(path) if path is not None else None
    return normalize_route_constraints(
        value,
        platform,
        frame_slots=frame_slots,
        max_iterations=max_iterations,
    )


def _arc_key(link_id: str, source: str, sink: str) -> ArcKey:
    return (link_id, source, sink)


def _capacity_key(link: BoardLink, source: str, sink: str) -> str:
    if link.direction == "half_duplex":
        return f"{link.id}:shared"
    return f"{link.id}:{source}->{sink}"


def build_directed_graph(
    platform: Platform,
    constraints: Mapping[str, Any],
) -> Tuple[
    Dict[str, List[Dict[str, Any]]],
    Dict[ArcKey, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:
    unavailable = set(constraints["unavailable_links"])
    adjacency: Dict[str, List[Dict[str, Any]]] = {
        fpga.id: [] for fpga in platform.fpgas
    }
    arcs: Dict[ArcKey, Dict[str, Any]] = {}
    capacity_records: Dict[str, Dict[str, Any]] = {}

    for link in platform.links:
        if link.id in unavailable:
            continue
        left, right = link.endpoints
        directions = [(left, right)]
        if link.direction in {"full_duplex", "half_duplex"}:
            directions.append((right, left))
        for source, sink in directions:
            capacity_key = _capacity_key(link, source, sink)
            arc = {
                "link": link.id,
                "from": source,
                "to": sink,
                "latency_cycles": link.latency_cycles,
                "capacity_key": capacity_key,
            }
            arcs[_arc_key(link.id, source, sink)] = arc
            adjacency[source].append(arc)
            if capacity_key not in capacity_records:
                capacity_records[capacity_key] = {
                    "key": capacity_key,
                    "link": link.id,
                    "direction": (
                        "shared"
                        if link.direction == "half_duplex"
                        else f"{source}->{sink}"
                    ),
                    "capacity_bits": (
                        link.data_lanes_per_direction
                        * constraints["frame_slots"]
                    ),
                }

    for source in adjacency:
        adjacency[source].sort(
            key=lambda arc: (arc["to"], arc["link"], arc["from"])
        )
    return adjacency, arcs, capacity_records


def demands_from_assignment(
    assignment: Mapping[str, Any],
    platform: Platform,
) -> List[Dict[str, Any]]:
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError(
            f"assignment.schema: expected {PARTITION_ASSIGNMENT_SCHEMA!r}, "
            f"got {assignment.get('schema')!r}"
        )
    if assignment.get("platform") != platform.name:
        raise ValidationError(
            f"assignment.platform: expected {platform.name!r}, "
            f"got {assignment.get('platform')!r}"
        )
    fpga_ids = {fpga.id for fpga in platform.fpgas}
    raw_cuts = assignment.get("cut_nets")
    if not isinstance(raw_cuts, list):
        raise ValidationError("assignment.cut_nets: expected an array")

    demands: List[Dict[str, Any]] = []
    demand_ids: Set[str] = set()
    for index, cut in enumerate(raw_cuts):
        if not isinstance(cut, dict):
            raise ValidationError(f"assignment.cut_nets[{index}]: expected an object")
        net_id = cut.get("net")
        sources = cut.get("source_fpgas")
        sinks = cut.get("sink_fpgas")
        if not isinstance(net_id, str) or not net_id:
            raise ValidationError(
                f"assignment.cut_nets[{index}].net: expected a non-empty string"
            )
        if net_id in demand_ids:
            raise ValidationError(
                f"assignment.cut_nets[{index}].net: duplicate {net_id!r}"
            )
        demand_ids.add(net_id)
        if not isinstance(sources, list) or len(sources) != 1:
            raise ValidationError(
                f"assignment.cut_nets[{index}].source_fpgas: "
                "expected exactly one source FPGA"
            )
        if (
            not isinstance(sinks, list)
            or not sinks
            or not all(isinstance(sink, str) for sink in sinks)
        ):
            raise ValidationError(
                f"assignment.cut_nets[{index}].sink_fpgas: "
                "expected a non-empty string array"
            )
        source = sources[0]
        unknown = sorted(({source} | set(sinks)) - fpga_ids)
        if unknown:
            raise ValidationError(
                f"assignment.cut_nets[{index}]: unknown FPGAs {unknown}"
            )
        normalized_sinks = sorted(set(sinks) - {source})
        if not normalized_sinks:
            raise ValidationError(
                f"assignment.cut_nets[{index}]: no remote sink FPGA"
            )
        demands.append(
            {
                "id": f"d{index:06d}",
                "net": net_id,
                "source": source,
                "sinks": normalized_sinks,
                "width_bits": 1,
            }
        )
    return sorted(demands, key=lambda demand: demand["net"])


def _shortest_path_tree(
    source: str,
    sinks: Sequence[str],
    width_bits: int,
    adjacency: Mapping[str, Sequence[Mapping[str, Any]]],
    usage: Mapping[str, int],
    history: Mapping[str, float],
    capacities: Mapping[str, Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    distance: Dict[str, float] = {source: 0.0}
    predecessor: Dict[str, Mapping[str, Any]] = {}
    queue: List[Tuple[float, str]] = [(0.0, source)]

    while queue:
        current_distance, node = heapq.heappop(queue)
        if current_distance != distance.get(node):
            continue
        for arc in adjacency.get(node, []):
            capacity_key = arc["capacity_key"]
            capacity = capacities[capacity_key]["capacity_bits"]
            projected = usage.get(capacity_key, 0) + width_bits
            present_cost = projected / capacity
            edge_cost = (
                1.0
                + float(arc["latency_cycles"])
                + present_cost
                + history.get(capacity_key, 0.0)
            )
            sink = arc["to"]
            candidate = current_distance + edge_cost
            if candidate < distance.get(sink, float("inf")):
                distance[sink] = candidate
                predecessor[sink] = arc
                heapq.heappush(queue, (candidate, sink))

    missing = sorted(set(sinks) - set(distance))
    if missing:
        raise ValidationError(
            f"system routing: source {source!r} cannot reach sinks {missing}"
        )

    tree_edges: Dict[ArcKey, Dict[str, Any]] = {}
    max_latency = 0
    for sink in sinks:
        node = sink
        latency = 0
        seen: Set[str] = set()
        while node != source:
            if node in seen:
                raise ValidationError(
                    f"system routing: predecessor cycle while routing {source}->{sink}"
                )
            seen.add(node)
            arc = predecessor[node]
            key = _arc_key(arc["link"], arc["from"], arc["to"])
            tree_edges[key] = {
                "link": arc["link"],
                "from": arc["from"],
                "to": arc["to"],
            }
            latency += arc["latency_cycles"]
            node = arc["from"]
        max_latency = max(max_latency, latency)

    return (
        sorted(
            tree_edges.values(),
            key=lambda edge: (edge["link"], edge["from"], edge["to"]),
        ),
        max_latency,
    )


def _link_utilization_records(
    capacity_records: Mapping[str, Mapping[str, Any]],
    usage: Mapping[str, int],
) -> List[Dict[str, Any]]:
    records = []
    for key in sorted(capacity_records):
        capacity = capacity_records[key]
        used_bits = usage.get(key, 0)
        records.append(
            {
                **capacity,
                "used_bits": used_bits,
                "utilization": used_bits / capacity["capacity_bits"],
            }
        )
    return records


def route_system(
    assignment: Mapping[str, Any],
    platform: Platform,
    constraints: Mapping[str, Any],
) -> Dict[str, Any]:
    demands = demands_from_assignment(assignment, platform)
    adjacency, arcs, capacities = build_directed_graph(platform, constraints)
    history = {key: 0.0 for key in capacities}
    final_routes: List[Dict[str, Any]] = []
    final_usage: Dict[str, int] = {}
    completed_iteration = 0

    for iteration in range(1, constraints["max_iterations"] + 1):
        usage = {key: 0 for key in capacities}
        routes: List[Dict[str, Any]] = []
        for demand in demands:
            tree_edges, max_latency = _shortest_path_tree(
                demand["source"],
                demand["sinks"],
                demand["width_bits"],
                adjacency,
                usage,
                history,
                capacities,
            )
            for edge in tree_edges:
                arc = arcs[_arc_key(edge["link"], edge["from"], edge["to"])]
                usage[arc["capacity_key"]] += demand["width_bits"]
            routes.append(
                {
                    **demand,
                    "tree_edges": tree_edges,
                    "max_latency_cycles": max_latency,
                }
            )

        overflow = {
            key: max(0, usage[key] - capacities[key]["capacity_bits"])
            for key in capacities
        }
        final_routes = routes
        final_usage = usage
        completed_iteration = iteration
        if not any(overflow.values()):
            break
        for key, excess in overflow.items():
            if excess:
                history[key] += 1.0 + excess / capacities[key]["capacity_bits"]
    else:
        overloaded = [
            {
                "key": key,
                "used_bits": final_usage[key],
                "capacity_bits": capacities[key]["capacity_bits"],
            }
            for key in sorted(capacities)
            if final_usage[key] > capacities[key]["capacity_bits"]
        ]
        raise ValidationError(
            f"system routing is infeasible after "
            f"{constraints['max_iterations']} iterations: {overloaded}"
        )

    utilization = _link_utilization_records(capacities, final_usage)
    routed_sinks = sum(len(route["sinks"]) for route in final_routes)
    tree_edges = sum(len(route["tree_edges"]) for route in final_routes)
    return {
        "schema": SYSTEM_ROUTES_SCHEMA,
        "design": assignment.get("design"),
        "platform": platform.name,
        "provider": "negotiated-shortest-path-tree-v1",
        "constraints": dict(constraints),
        "demands": demands,
        "routes": final_routes,
        "link_utilization": utilization,
        "metrics": {
            "demands": len(demands),
            "routed_sinks": routed_sinks,
            "tree_edges": tree_edges,
            "iterations": completed_iteration,
            "max_link_utilization": max(
                (record["utilization"] for record in utilization),
                default=0.0,
            ),
            "total_link_bit_hops": sum(final_usage.values()),
        },
    }


def _validate_route_tree(
    route: Mapping[str, Any],
    arcs: Mapping[ArcKey, Mapping[str, Any]],
) -> Tuple[List[ArcKey], int]:
    raw_edges = route.get("tree_edges")
    if not isinstance(raw_edges, list):
        raise ValidationError(
            f"route {route.get('id')!r}.tree_edges: expected an array"
        )
    edge_keys: List[ArcKey] = []
    graph: Dict[str, List[str]] = defaultdict(list)
    indegree: Dict[str, int] = defaultdict(int)
    latency_by_edge: Dict[Tuple[str, str], int] = {}
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise ValidationError(
                f"route {route.get('id')!r}.tree_edges[{index}]: expected an object"
            )
        key = _arc_key(edge.get("link"), edge.get("from"), edge.get("to"))
        if key not in arcs:
            raise ValidationError(
                f"route {route.get('id')!r}: illegal directed edge {key}"
            )
        if key in edge_keys:
            raise ValidationError(
                f"route {route.get('id')!r}: duplicate edge {key}"
            )
        edge_keys.append(key)
        graph[key[1]].append(key[2])
        indegree[key[2]] += 1
        latency_by_edge[(key[1], key[2])] = arcs[key]["latency_cycles"]

    source = route["source"]
    reachable = {source}
    queue = deque([source])
    latency = {source: 0}
    while queue:
        node = queue.popleft()
        for sink in sorted(graph.get(node, [])):
            if sink in reachable:
                raise ValidationError(
                    f"route {route.get('id')!r}: tree contains a cycle or "
                    f"multiple path to {sink!r}"
                )
            reachable.add(sink)
            latency[sink] = latency[node] + latency_by_edge[(node, sink)]
            queue.append(sink)

    edge_nodes = {node for key in edge_keys for node in (key[1], key[2])}
    if not edge_nodes <= reachable:
        raise ValidationError(
            f"route {route.get('id')!r}: tree has edges disconnected from source"
        )
    missing = sorted(set(route["sinks"]) - reachable)
    if missing:
        raise ValidationError(
            f"route {route.get('id')!r}: sinks are unreachable {missing}"
        )
    for node, count in indegree.items():
        if node != source and count != 1:
            raise ValidationError(
                f"route {route.get('id')!r}: node {node!r} has indegree {count}"
            )
    max_latency = max((latency[sink] for sink in route["sinks"]), default=0)
    return edge_keys, max_latency


def validate_system_routes(
    assignment: Mapping[str, Any],
    platform: Platform,
    routes_artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    if routes_artifact.get("schema") != SYSTEM_ROUTES_SCHEMA:
        raise ValidationError(
            f"routes.schema: expected {SYSTEM_ROUTES_SCHEMA!r}, "
            f"got {routes_artifact.get('schema')!r}"
        )
    constraints = normalize_route_constraints(
        routes_artifact.get("constraints"),
        platform,
    )
    expected_demands = demands_from_assignment(assignment, platform)
    if routes_artifact.get("demands") != expected_demands:
        raise ValidationError("routes.demands does not match partition cut nets")

    adjacency, arcs, capacities = build_directed_graph(platform, constraints)
    del adjacency
    raw_routes = routes_artifact.get("routes")
    if not isinstance(raw_routes, list):
        raise ValidationError("routes.routes: expected an array")
    route_by_id = {
        route.get("id"): route for route in raw_routes if isinstance(route, dict)
    }
    expected_by_id = {demand["id"]: demand for demand in expected_demands}
    if set(route_by_id) != set(expected_by_id) or len(route_by_id) != len(raw_routes):
        raise ValidationError("routes.routes: demand coverage is not exact")

    usage = {key: 0 for key in capacities}
    routed_sinks = 0
    tree_edge_count = 0
    for demand_id in sorted(expected_by_id):
        route = route_by_id[demand_id]
        demand = expected_by_id[demand_id]
        for field in ("id", "net", "source", "sinks", "width_bits"):
            if route.get(field) != demand[field]:
                raise ValidationError(
                    f"route {demand_id!r}.{field}: does not match demand"
                )
        edge_keys, max_latency = _validate_route_tree(route, arcs)
        if route.get("max_latency_cycles") != max_latency:
            raise ValidationError(
                f"route {demand_id!r}.max_latency_cycles: expected "
                f"{max_latency}, got {route.get('max_latency_cycles')!r}"
            )
        for key in edge_keys:
            usage[arcs[key]["capacity_key"]] += demand["width_bits"]
        routed_sinks += len(demand["sinks"])
        tree_edge_count += len(edge_keys)

    expected_utilization = _link_utilization_records(capacities, usage)
    if routes_artifact.get("link_utilization") != expected_utilization:
        raise ValidationError(
            "routes.link_utilization does not match independently recomputed usage"
        )
    overloaded = [
        record
        for record in expected_utilization
        if record["used_bits"] > record["capacity_bits"]
    ]
    if overloaded:
        raise ValidationError(f"routes exceed modeled link capacity: {overloaded}")

    expected_metrics = {
        "demands": len(expected_demands),
        "routed_sinks": routed_sinks,
        "tree_edges": tree_edge_count,
        "max_link_utilization": max(
            (record["utilization"] for record in expected_utilization),
            default=0.0,
        ),
        "total_link_bit_hops": sum(usage.values()),
    }
    metrics = routes_artifact.get("metrics")
    if not isinstance(metrics, dict):
        raise ValidationError("routes.metrics: expected an object")
    for key, expected in expected_metrics.items():
        if metrics.get(key) != expected:
            raise ValidationError(
                f"routes.metrics.{key}: expected {expected}, "
                f"got {metrics.get(key)!r}"
            )

    return {
        "status": "pass",
        **expected_metrics,
        "iterations": metrics.get("iterations"),
        "overloaded_links": 0,
        "link_utilization": expected_utilization,
    }
