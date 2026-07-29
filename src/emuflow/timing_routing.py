"""Artifact adapter and independent checker for the in-tree C++ TLR router."""

from __future__ import annotations

import heapq
import subprocess
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .io import read_json
from .native_tools import resolve_native_executable
from .platform import Platform
from .routing import (
    ArcKey,
    _arc_key,
    build_directed_graph,
    demands_from_assignment,
    normalize_route_constraints,
    validate_system_routes,
)


STA_PATHS_SCHEMA = "emuflow.sta-paths/v1"
TLR_PROVIDER = "timing-aware-load-balanced-v1"


def _number(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{context}: expected a number")
    result = float(value)
    if positive and result <= 0.0:
        raise ValidationError(f"{context}: expected a positive number")
    return result


def normalize_sta_paths(
    value: Mapping[str, Any],
    demands: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    if value.get("schema") != STA_PATHS_SCHEMA:
        raise ValidationError(
            f"timing paths.schema: expected {STA_PATHS_SCHEMA!r}, "
            f"got {value.get('schema')!r}"
        )
    demand_by_net = {demand["net"]: demand for demand in demands}
    raw_paths = value.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValidationError("timing paths.paths: expected a non-empty array")

    paths: List[Dict[str, Any]] = []
    path_ids = set()
    demand_nets = set(demand_by_net)
    for index, raw in enumerate(raw_paths):
        context = f"timing paths.paths[{index}]"
        if not isinstance(raw, dict):
            raise ValidationError(f"{context}: expected an object")
        path_id = raw.get("id")
        clock_domain = raw.get("clock_domain")
        if not isinstance(path_id, str) or not path_id:
            raise ValidationError(f"{context}.id: expected a non-empty string")
        if path_id in path_ids:
            raise ValidationError(f"{context}.id: duplicate {path_id!r}")
        path_ids.add(path_id)
        if not isinstance(clock_domain, str) or not clock_domain:
            raise ValidationError(
                f"{context}.clock_domain: expected a non-empty string"
            )
        clock_period = _number(
            raw.get("clock_period_ns"),
            f"{context}.clock_period_ns",
            positive=True,
        )
        slack = _number(raw.get("slack_ns"), f"{context}.slack_ns")
        fixed_delay = _number(
            raw.get("fixed_delay_ns"), f"{context}.fixed_delay_ns"
        )
        if fixed_delay < 0.0:
            raise ValidationError(
                f"{context}.fixed_delay_ns: expected a non-negative number"
            )
        raw_nets = raw.get("cut_nets")
        if (
            not isinstance(raw_nets, list)
            or not raw_nets
            or not all(isinstance(net, str) and net for net in raw_nets)
        ):
            raise ValidationError(
                f"{context}.cut_nets: expected a non-empty string array"
            )
        unknown = sorted(set(raw_nets) - demand_nets)
        if unknown:
            raise ValidationError(
                f"{context}.cut_nets: unknown partition cut nets {unknown}"
            )
        if len(set(raw_nets)) != len(raw_nets):
            raise ValidationError(
                f"{context}.cut_nets: duplicate cut nets are not supported"
            )
        signature = raw.get("cut_signature")
        if signature is None:
            signature = []
            for net in raw_nets:
                demand = demand_by_net[net]
                signature.append(
                    f"{demand['source']}->{','.join(demand['sinks'])}"
                )
        if (
            not isinstance(signature, list)
            or not signature
            or not all(isinstance(item, str) and item for item in signature)
        ):
            raise ValidationError(
                f"{context}.cut_signature: expected a non-empty string array"
            )
        paths.append(
            {
                "id": path_id,
                "clock_domain": clock_domain,
                "clock_period_ns": clock_period,
                "slack_ns": slack,
                "fixed_delay_ns": fixed_delay,
                "cut_nets": list(raw_nets),
                "cut_signature": list(signature),
            }
        )

    positive_scale = max(
        (path["slack_ns"] for path in paths if path["slack_ns"] >= 0.0),
        default=1.0,
    )
    negative_scale = abs(
        min(
            (path["slack_ns"] for path in paths if path["slack_ns"] < 0.0),
            default=-1.0,
        )
    )
    max_period = max(path["clock_period_ns"] for path in paths)
    for path in paths:
        slack = path["slack_ns"]
        if slack >= 0.0:
            normalized = (
                slack
                * path["clock_period_ns"]
                / (positive_scale * max_period)
            )
        else:
            normalized = slack / (negative_scale * path["clock_period_ns"])
        path["normalized_slack"] = normalized

    return {
        "schema": STA_PATHS_SCHEMA,
        "design": value.get("design"),
        "normalization": {
            "positive_slack_scale_ns": positive_scale,
            "negative_slack_scale_ns": negative_scale,
            "max_clock_period_ns": max_period,
        },
        "paths": paths,
    }


def compress_sta_paths(paths_artifact: Mapping[str, Any]) -> Dict[str, Any]:
    """Losslessly compress paths with timing-equivalent ordered cuts.

    Equal cut signatures alone are insufficient across clock domains because
    normalized slack has a different scale.  Requiring the same domain,
    period, and cut-net sequence makes the maximum-fixed-delay representative
    dominate the other members for every possible route delay.
    """

    representatives: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    members: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for path in paths_artifact["paths"]:
        signature = (
            path["clock_domain"],
            path["clock_period_ns"],
            tuple(path["cut_signature"]),
            tuple(path["cut_nets"]),
        )
        members[signature].append(path["id"])
        current = representatives.get(signature)
        key = (
            path["fixed_delay_ns"],
            -path["normalized_slack"],
            path["id"],
        )
        if current is None:
            representatives[signature] = path
        else:
            current_key = (
                current["fixed_delay_ns"],
                -current["normalized_slack"],
                current["id"],
            )
            if key > current_key:
                representatives[signature] = path

    compressed = []
    for signature in sorted(representatives):
        representative = dict(representatives[signature])
        representative["compressed_path_ids"] = sorted(members[signature])
        compressed.append(representative)
    return {
        **dict(paths_artifact),
        "compression": {
            "original_paths": len(paths_artifact["paths"]),
            "compressed_paths": len(compressed),
            "lossless_by": (
                "clock-domain+period+ordered-cut-signature+"
                "cut-net-sequence/max-fixed-delay"
            ),
        },
        "paths": compressed,
    }


def load_sta_paths(
    path: Path,
    demands: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValidationError("timing paths: expected an object")
    if "compression" in value or "normalization" in value:
        if value.get("schema") != STA_PATHS_SCHEMA:
            raise ValidationError(
                f"timing paths.schema: expected {STA_PATHS_SCHEMA!r}"
            )
        compression = value.get("compression")
        normalization = value.get("normalization")
        paths = value.get("paths")
        if (
            not isinstance(compression, dict)
            or not isinstance(normalization, dict)
            or not isinstance(paths, list)
            or not paths
        ):
            raise ValidationError(
                "normalized timing paths: invalid normalization/compression"
            )
        if compression.get("compressed_paths") != len(paths):
            raise ValidationError(
                "normalized timing paths: compressed path count mismatch"
            )
        original_count = compression.get("original_paths")
        if (
            isinstance(original_count, bool)
            or not isinstance(original_count, int)
            or original_count < len(paths)
        ):
            raise ValidationError(
                "normalized timing paths: invalid original path count"
            )
        demand_nets = {demand["net"] for demand in demands}
        positive_scale = _number(
            normalization.get("positive_slack_scale_ns"),
            "timing paths.normalization.positive_slack_scale_ns",
            positive=True,
        )
        negative_scale = _number(
            normalization.get("negative_slack_scale_ns"),
            "timing paths.normalization.negative_slack_scale_ns",
            positive=True,
        )
        max_period = _number(
            normalization.get("max_clock_period_ns"),
            "timing paths.normalization.max_clock_period_ns",
            positive=True,
        )
        ids = set()
        for index, item in enumerate(paths):
            context = f"normalized timing paths.paths[{index}]"
            if not isinstance(item, dict):
                raise ValidationError(f"{context}: expected an object")
            path_id = item.get("id")
            if not isinstance(path_id, str) or not path_id or path_id in ids:
                raise ValidationError(f"{context}.id: invalid or duplicate")
            ids.add(path_id)
            period = _number(
                item.get("clock_period_ns"),
                f"{context}.clock_period_ns",
                positive=True,
            )
            slack = _number(item.get("slack_ns"), f"{context}.slack_ns")
            fixed_delay = _number(
                item.get("fixed_delay_ns"), f"{context}.fixed_delay_ns"
            )
            if fixed_delay < 0.0:
                raise ValidationError(
                    f"{context}.fixed_delay_ns: expected non-negative"
                )
            if (
                not isinstance(item.get("clock_domain"), str)
                or not item["clock_domain"]
            ):
                raise ValidationError(f"{context}.clock_domain: invalid")
            nets = item.get("cut_nets")
            if (
                not isinstance(nets, list)
                or not nets
                or not all(isinstance(net, str) and net for net in nets)
                or len(set(nets)) != len(nets)
                or not set(nets) <= demand_nets
            ):
                raise ValidationError(f"{context}.cut_nets: invalid")
            signature = item.get("cut_signature")
            if (
                not isinstance(signature, list)
                or not signature
                or not all(
                    isinstance(part, str) and part for part in signature
                )
            ):
                raise ValidationError(f"{context}.cut_signature: invalid")
            members = item.get("compressed_path_ids")
            if (
                not isinstance(members, list)
                or not members
                or not all(isinstance(member, str) for member in members)
            ):
                raise ValidationError(
                    f"{context}.compressed_path_ids: invalid"
                )
            expected_normalized = (
                slack * period / (positive_scale * max_period)
                if slack >= 0.0
                else slack / (negative_scale * period)
            )
            if (
                abs(
                    _number(
                        item.get("normalized_slack"),
                        f"{context}.normalized_slack",
                    )
                    - expected_normalized
                )
                > 1.0e-12
            ):
                raise ValidationError(
                    f"{context}.normalized_slack: inconsistent"
                )
        return dict(value)
    return compress_sta_paths(normalize_sta_paths(value, demands))


def _link_delay_ns(
    platform: Platform,
    link_id: str,
    constraints: Mapping[str, Any],
) -> float:
    overrides = constraints.get("link_delay_ns", {})
    if link_id in overrides:
        return float(overrides[link_id])
    link = next(link for link in platform.links if link.id == link_id)
    return link.latency_cycles * 1000.0 / link.fabric_clock_mhz


def _static_predicted_delay(
    source: str,
    sinks: Sequence[str],
    adjacency: Mapping[str, Sequence[Mapping[str, Any]]],
    platform: Platform,
    constraints: Mapping[str, Any],
) -> float:
    distance = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        current, node = heapq.heappop(queue)
        if current != distance.get(node):
            continue
        for arc in adjacency.get(node, []):
            candidate = current + _link_delay_ns(
                platform, arc["link"], constraints
            )
            if candidate < distance.get(arc["to"], float("inf")):
                distance[arc["to"]] = candidate
                heapq.heappush(queue, (candidate, arc["to"]))
    missing = sorted(set(sinks) - set(distance))
    if missing:
        raise ValidationError(
            f"timing-aware routing: source {source!r} cannot reach {missing}"
        )
    return max(distance[sink] for sink in sinks)


def _prepare_native_model(
    assignment: Mapping[str, Any],
    platform: Platform,
    constraints: Mapping[str, Any],
    timing_paths: Mapping[str, Any],
) -> Tuple[List[str], Dict[str, Any]]:
    demands = demands_from_assignment(assignment, platform)
    adjacency, arcs_by_key, capacities = build_directed_graph(
        platform, constraints
    )
    nodes = sorted(fpga.id for fpga in platform.fpgas)
    node_index = {node: index for index, node in enumerate(nodes)}
    capacity_keys = sorted(capacities)
    capacity_index = {
        key: index for index, key in enumerate(capacity_keys)
    }
    link_index = {
        link.id: index
        for index, link in enumerate(sorted(platform.links, key=lambda x: x.id))
    }

    ordered_arc_keys = sorted(arcs_by_key)
    arc_index = {key: index for index, key in enumerate(ordered_arc_keys)}
    direction_group_by_link = {}
    for link in sorted(platform.links, key=lambda item: item.id):
        if link.direction == "half_duplex":
            direction_group_by_link[link.id] = len(direction_group_by_link)
    sll_links = set(constraints.get("sll_links", []))
    native_arcs = []
    for index, key in enumerate(ordered_arc_keys):
        arc = arcs_by_key[key]
        link = next(link for link in platform.links if link.id == arc["link"])
        opposite_key = _arc_key(link.id, arc["to"], arc["from"])
        native_arcs.append(
            {
                "index": index,
                "link_index": link_index[link.id],
                "from": node_index[arc["from"]],
                "to": node_index[arc["to"]],
                "capacity_domain": capacity_index[arc["capacity_key"]],
                "direction_group": direction_group_by_link.get(link.id, -1),
                "opposite_arc": arc_index.get(opposite_key, -1),
                "capacity": capacities[arc["capacity_key"]]["capacity_bits"],
                "delay_ns": _link_delay_ns(platform, link.id, constraints),
                "is_sll": link.id in sll_links,
            }
        )

    path_by_net: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for path in timing_paths["paths"]:
        for net in path["cut_nets"]:
            path_by_net[net].append(path)
    native_demands = []
    demand_index = {demand["net"]: index for index, demand in enumerate(demands)}
    for index, demand in enumerate(demands):
        related = path_by_net.get(demand["net"], [])
        normalized_slack = min(
            (path["normalized_slack"] for path in related), default=1.0
        )
        native_demands.append(
            {
                "index": index,
                "source": node_index[demand["source"]],
                "sinks": [node_index[sink] for sink in demand["sinks"]],
                "width": demand["width_bits"],
                "normalized_slack": normalized_slack,
                "predicted_delay_ns": _static_predicted_delay(
                    demand["source"],
                    demand["sinks"],
                    adjacency,
                    platform,
                    constraints,
                ),
            }
        )

    native_paths = []
    for index, path in enumerate(timing_paths["paths"]):
        native_paths.append(
            {
                "index": index,
                "clock_period_ns": path["clock_period_ns"],
                "baseline_slack_ns": path["slack_ns"],
                "fixed_delay_ns": path["fixed_delay_ns"],
                "demands": [demand_index[net] for net in path["cut_nets"]],
            }
        )
    return nodes, {
        "demands": demands,
        "arc_keys": ordered_arc_keys,
        "arcs": native_arcs,
        "paths": native_paths,
        "native_demands": native_demands,
        "capacity_keys": capacity_keys,
        "normalization": timing_paths["normalization"],
    }


def _write_native_input(
    path: Path,
    node_count: int,
    model: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> None:
    normalization = model["normalization"]
    lines = ["EMUFLOW_TLR_INPUT_V1"]
    lines.append(
        "PARAM "
        f"{node_count} {constraints['max_iterations']} "
        f"{constraints.get('reroute_rounds', 8)} "
        f"{constraints.get('lambda_load', 2.0):.17g} "
        f"{constraints.get('lambda_timing', 4.0):.17g} "
        f"{constraints.get('lambda_history', 1.0):.17g} "
        f"{normalization['positive_slack_scale_ns']:.17g} "
        f"{normalization['negative_slack_scale_ns']:.17g} "
        f"{normalization['max_clock_period_ns']:.17g}"
    )
    for arc in model["arcs"]:
        lines.append(
            "ARC "
            f"{arc['index']} {arc['link_index']} {arc['from']} {arc['to']} "
            f"{arc['capacity_domain']} {arc['direction_group']} "
            f"{arc['opposite_arc']} {arc['capacity']} "
            f"{arc['delay_ns']:.17g} {int(arc['is_sll'])}"
        )
    for demand in model["native_demands"]:
        sinks = ",".join(str(sink) for sink in demand["sinks"])
        lines.append(
            "DEMAND "
            f"{demand['index']} {demand['source']} {sinks} "
            f"{demand['width']} {demand['normalized_slack']:.17g} "
            f"{demand['predicted_delay_ns']:.17g}"
        )
    for timing_path in model["paths"]:
        demands = ",".join(str(item) for item in timing_path["demands"])
        lines.append(
            "PATH "
            f"{timing_path['index']} "
            f"{timing_path['clock_period_ns']:.17g} "
            f"{timing_path['baseline_slack_ns']:.17g} "
            f"{timing_path['fixed_delay_ns']:.17g} {demands}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_native_output(path: Path, model: Mapping[str, Any]) -> Dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_TLR_OUTPUT_V1":
        raise EmuFlowError("TLR router returned an invalid output header")
    routes = {}
    locks = {}
    timing = {}
    metrics: Dict[str, Any] = {}
    for line in lines[1:]:
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "LOCK" and len(fields) == 3:
            locks[int(fields[1])] = int(fields[2])
        elif fields[0] == "ROUTE" and len(fields) == 4:
            routes[int(fields[1])] = {
                "max_delay_ns": float(fields[2]),
                "arcs": (
                    []
                    if fields[3] == "-"
                    else [int(item) for item in fields[3].split(",")]
                ),
            }
        elif fields[0] == "PATH" and len(fields) == 6:
            timing[int(fields[1])] = {
                "delay_ns": float(fields[2]),
                "slack_ns": float(fields[3]),
                "normalized_slack": float(fields[4]),
                "route_signature": fields[5],
            }
        elif fields[0] == "METRIC" and len(fields) == 3:
            value: Any = float(fields[2])
            if fields[1] in {
                "iterations",
                "accepted_reroutes",
                "rolled_back_reroutes",
                "total_link_bit_hops",
            }:
                value = int(value)
            metrics[fields[1]] = value
        else:
            raise EmuFlowError(f"TLR router returned malformed record: {line}")
    if len(routes) != len(model["demands"]):
        raise EmuFlowError("TLR router did not return exact demand coverage")
    if len(timing) != len(model["paths"]):
        raise EmuFlowError("TLR router did not return exact timing-path coverage")
    return {
        "routes": routes,
        "direction_locks": locks,
        "timing": timing,
        "metrics": metrics,
    }


def _tree_latency_cycles(
    source: str,
    sinks: Sequence[str],
    edge_keys: Sequence[ArcKey],
    arcs: Mapping[ArcKey, Mapping[str, Any]],
) -> int:
    graph: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for key in edge_keys:
        graph[key[1]].append((key[2], int(arcs[key]["latency_cycles"])))
    latency = {source: 0}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for sink, edge_latency in graph[node]:
            latency[sink] = latency[node] + edge_latency
            queue.append(sink)
    return max(latency[sink] for sink in sinks)


def route_system_timing_aware(
    assignment: Mapping[str, Any],
    platform: Platform,
    constraints: Mapping[str, Any],
    timing_paths: Mapping[str, Any],
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    nodes, model = _prepare_native_model(
        assignment, platform, constraints, timing_paths
    )
    resolved = resolve_native_executable("emuflow_tlr_router", executable)
    with tempfile.TemporaryDirectory(prefix="emuflow-tlr-") as temporary:
        root = Path(temporary)
        native_input = root / "router.in"
        native_output = root / "router.out"
        _write_native_input(native_input, len(nodes), model, constraints)
        completed = subprocess.run(
            [resolved, str(native_input), str(native_output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise EmuFlowError(
                f"in-tree TLR router failed with exit code "
                f"{completed.returncode}: {detail}"
            )
        native = _parse_native_output(native_output, model)

    _, arcs, capacities = build_directed_graph(platform, constraints)
    routes = []
    usage = {key: 0 for key in capacities}
    for demand_index, demand in enumerate(model["demands"]):
        native_route = native["routes"][demand_index]
        edge_keys = [
            model["arc_keys"][index] for index in native_route["arcs"]
        ]
        for key in edge_keys:
            usage[arcs[key]["capacity_key"]] += demand["width_bits"]
        route = {
            **demand,
            "tree_edges": [
                {"link": key[0], "from": key[1], "to": key[2]}
                for key in edge_keys
            ],
            "max_latency_cycles": _tree_latency_cycles(
                demand["source"], demand["sinks"], edge_keys, arcs
            ),
            "predicted_max_delay_ns": native_route["max_delay_ns"],
        }
        routes.append(route)

    utilization = []
    for key in sorted(capacities):
        capacity = capacities[key]
        used = usage[key]
        utilization.append(
            {
                **capacity,
                "used_bits": used,
                "utilization": used / capacity["capacity_bits"],
            }
        )
    direction_locks = []
    for group, arc_index in sorted(native["direction_locks"].items()):
        key = model["arc_keys"][arc_index]
        direction_locks.append(
            {
                "group": group,
                "link": key[0],
                "from": key[1],
                "to": key[2],
            }
        )
    timing_records = []
    for index, path in enumerate(timing_paths["paths"]):
        timing_records.append(
            {
                "path": path["id"],
                "clock_domain": path["clock_domain"],
                "clock_period_ns": path["clock_period_ns"],
                "fixed_delay_ns": path["fixed_delay_ns"],
                "cut_nets": path["cut_nets"],
                "cut_signature": path["cut_signature"],
                "compressed_path_ids": path["compressed_path_ids"],
                **native["timing"][index],
            }
        )
    return {
        "schema": "emuflow.system-routes/v1",
        "design": assignment.get("design"),
        "platform": platform.name,
        "provider": TLR_PROVIDER,
        "constraints": dict(constraints),
        "demands": model["demands"],
        "routes": routes,
        "link_utilization": utilization,
        "direction_locks": direction_locks,
        "timing": {
            "schema": STA_PATHS_SCHEMA,
            "normalization": timing_paths["normalization"],
            "compression": timing_paths["compression"],
            "paths": timing_records,
        },
        "metrics": {
            "demands": len(model["demands"]),
            "routed_sinks": sum(len(route["sinks"]) for route in routes),
            "tree_edges": sum(len(route["tree_edges"]) for route in routes),
            "iterations": native["metrics"]["iterations"],
            "accepted_reroutes": native["metrics"]["accepted_reroutes"],
            "rolled_back_reroutes": native["metrics"][
                "rolled_back_reroutes"
            ],
            "worst_slack_ns": native["metrics"]["worst_slack_ns"],
            "worst_normalized_slack": native["metrics"][
                "worst_normalized_slack"
            ],
            "max_link_utilization": max(
                (record["utilization"] for record in utilization), default=0.0
            ),
            "total_link_bit_hops": sum(usage.values()),
        },
    }


def validate_timing_aware_system_routes(
    assignment: Mapping[str, Any],
    platform: Platform,
    routes: Mapping[str, Any],
    timing_paths: Mapping[str, Any],
) -> Dict[str, Any]:
    validation = validate_system_routes(assignment, platform, routes)
    if routes.get("provider") != TLR_PROVIDER:
        raise ValidationError(
            f"routes.provider: expected {TLR_PROVIDER!r}"
        )
    locks = routes.get("direction_locks")
    if not isinstance(locks, list):
        raise ValidationError("routes.direction_locks: expected an array")
    lock_by_link = {}
    for lock in locks:
        if not isinstance(lock, dict):
            raise ValidationError("routes.direction_locks: invalid record")
        link = next(
            (item for item in platform.links if item.id == lock.get("link")),
            None,
        )
        if link is None or link.direction != "half_duplex":
            raise ValidationError(
                "routes.direction_locks: lock must name a half-duplex link"
            )
        direction = (lock.get("from"), lock.get("to"))
        if set(direction) != set(link.endpoints):
            raise ValidationError(
                "routes.direction_locks: direction does not match endpoints"
            )
        if link.id in lock_by_link:
            raise ValidationError(
                f"routes.direction_locks: duplicate link {link.id!r}"
            )
        lock_by_link[link.id] = direction
    expected_locks = {
        link.id for link in platform.links if link.direction == "half_duplex"
    }
    if set(lock_by_link) != expected_locks:
        raise ValidationError(
            "routes.direction_locks: half-duplex lock coverage is not exact"
        )
    for route in routes["routes"]:
        for edge in route["tree_edges"]:
            locked = lock_by_link.get(edge["link"])
            if locked is not None and locked != (edge["from"], edge["to"]):
                raise ValidationError(
                    f"route {route['id']!r}: violates direction lock"
                )

    constraints = normalize_route_constraints(
        routes.get("constraints"), platform
    )
    route_delay_by_net = {}
    ordered_arc_keys = sorted(
        build_directed_graph(platform, constraints)[1]
    )
    arc_index = {key: index for index, key in enumerate(ordered_arc_keys)}
    route_signature_by_net = {}
    for route in routes["routes"]:
        graph: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        signature = []
        for edge in route["tree_edges"]:
            graph[edge["from"]].append((edge["to"], edge["link"]))
            signature.append(
                arc_index[_arc_key(edge["link"], edge["from"], edge["to"])]
            )
        delay_by_node = {route["source"]: 0.0}
        queue = deque([route["source"]])
        while queue:
            node = queue.popleft()
            for sink, link_id in graph[node]:
                delay_by_node[sink] = delay_by_node[node] + _link_delay_ns(
                    platform, link_id, constraints
                )
                queue.append(sink)
        predicted_delay = max(
            delay_by_node[sink] for sink in route["sinks"]
        )
        if (
            abs(
                float(route.get("predicted_max_delay_ns", float("nan")))
                - predicted_delay
            )
            > 1.0e-9
        ):
            raise ValidationError(
                f"route {route['id']!r}.predicted_max_delay_ns does not "
                "match independent edge-delay recomputation"
            )
        route_delay_by_net[route["net"]] = predicted_delay
        route_signature_by_net[route["net"]] = sorted(signature)

    timing = routes.get("timing")
    if not isinstance(timing, dict) or timing.get("schema") != STA_PATHS_SCHEMA:
        raise ValidationError("routes.timing: invalid timing artifact")
    records = timing.get("paths")
    if not isinstance(records, list) or len(records) != len(
        timing_paths["paths"]
    ):
        raise ValidationError("routes.timing.paths: coverage is not exact")
    if timing.get("normalization") != timing_paths["normalization"]:
        raise ValidationError(
            "routes.timing.normalization does not match normalized STA input"
        )
    positive_scale = timing_paths["normalization"][
        "positive_slack_scale_ns"
    ]
    negative_scale = timing_paths["normalization"][
        "negative_slack_scale_ns"
    ]
    max_period = timing_paths["normalization"]["max_clock_period_ns"]
    worst_slack = float("inf")
    worst_normalized = float("inf")
    for expected, actual in zip(timing_paths["paths"], records):
        if actual.get("path") != expected["id"]:
            raise ValidationError("routes.timing.paths: order/identity mismatch")
        for key in (
            "clock_domain",
            "clock_period_ns",
            "fixed_delay_ns",
            "cut_nets",
            "cut_signature",
            "compressed_path_ids",
        ):
            if actual.get(key) != expected[key]:
                raise ValidationError(
                    f"routes.timing path {expected['id']!r}.{key}: "
                    "does not match normalized STA input"
                )
        delay = expected["fixed_delay_ns"] + sum(
            route_delay_by_net[net]
            for net in expected["cut_nets"]
        )
        slack = expected["clock_period_ns"] - delay
        if slack >= 0.0:
            normalized = (
                slack
                * expected["clock_period_ns"]
                / (positive_scale * max_period)
            )
        else:
            normalized = (
                slack / (negative_scale * expected["clock_period_ns"])
            )
        for key, value in (
            ("delay_ns", delay),
            ("slack_ns", slack),
            ("normalized_slack", normalized),
        ):
            if abs(float(actual.get(key, float("nan"))) - value) > 1.0e-9:
                raise ValidationError(
                    f"routes.timing path {expected['id']!r}.{key}: "
                    "does not match independent recomputation"
                )
        expected_signature = ",".join(
            str(arc)
            for net in expected["cut_nets"]
            for arc in route_signature_by_net[net]
        )
        if not expected_signature:
            expected_signature = "-"
        if actual.get("route_signature") != expected_signature:
            raise ValidationError(
                f"routes.timing path {expected['id']!r}.route_signature: "
                "does not match independently reconstructed routes"
            )
        worst_slack = min(worst_slack, slack)
        worst_normalized = min(worst_normalized, normalized)
    metrics = routes.get("metrics", {})
    if abs(metrics.get("worst_slack_ns", float("nan")) - worst_slack) > 1.0e-9:
        raise ValidationError(
            "routes.metrics.worst_slack_ns does not match timing paths"
        )
    if (
        abs(
            metrics.get("worst_normalized_slack", float("nan"))
            - worst_normalized
        )
        > 1.0e-9
    ):
        raise ValidationError(
            "routes.metrics.worst_normalized_slack does not match timing paths"
        )
    return {
        **validation,
        "provider": TLR_PROVIDER,
        "timing_paths_original": timing_paths["compression"][
            "original_paths"
        ],
        "timing_paths_compressed": timing_paths["compression"][
            "compressed_paths"
        ],
        "worst_slack_ns": worst_slack,
        "worst_normalized_slack": worst_normalized,
        "direction_locks": len(locks),
        "accepted_reroutes": metrics.get("accepted_reroutes"),
        "rolled_back_reroutes": metrics.get("rolled_back_reroutes"),
    }
