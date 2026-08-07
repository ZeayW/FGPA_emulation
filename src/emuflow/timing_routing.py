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
    estimate_tdm_ratio,
    normalize_route_constraints,
    route_link_delay_ns,
    validate_system_routes,
    _validate_route_tree,
)


STA_PATHS_SCHEMA = "emuflow.sta-paths/v1"
NATIVE_ROUTER_PROVIDER = "native-load-balanced-v1"
TLR_PROVIDER = "timing-aware-load-balanced-v1"
ROUTE_TDM_PROVIDER = "timing-aware-route-tdm-cooptimized-v1"


def _number(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{context}: expected a number")
    result = float(value)
    if positive and result <= 0.0:
        raise ValidationError(f"{context}: expected a positive number")
    return result


def _normalize_cut_transitions(
    value: Any,
    cut_nets: Sequence[str],
    demand_by_net: Mapping[str, Mapping[str, Any]],
    context: str,
) -> Optional[List[Dict[str, str]]]:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != len(cut_nets):
        raise ValidationError(f"{context}: expected one transition per cut net")
    result = []
    for index, (raw, net) in enumerate(zip(value, cut_nets)):
        item_context = f"{context}[{index}]"
        demand = demand_by_net[net]
        if (
            not isinstance(raw, dict)
            or set(raw) != {"net", "from", "to"}
            or raw.get("net") != net
            or raw.get("from") != demand["source"]
            or raw.get("to") not in demand["sinks"]
        ):
            raise ValidationError(f"{item_context}: invalid routed sink")
        result.append(dict(raw))
    if any(
        result[index - 1]["to"] != result[index]["from"]
        for index in range(1, len(result))
    ):
        raise ValidationError(f"{context}: partition chain is discontinuous")
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
        path = {
                "id": path_id,
                "clock_domain": clock_domain,
                "clock_period_ns": clock_period,
                "slack_ns": slack,
                "fixed_delay_ns": fixed_delay,
                "cut_nets": list(raw_nets),
                "cut_signature": list(signature),
            }
        transitions = _normalize_cut_transitions(
            raw.get("cut_transitions"),
            raw_nets,
            demand_by_net,
            f"{context}.cut_transitions",
        )
        if transitions is not None:
            path["cut_transitions"] = transitions
        paths.append(path)

    positive_scale = max(
        (path["slack_ns"] for path in paths if path["slack_ns"] >= 0.0),
        default=1.0,
    )
    # A path exactly on its timing boundary is valid.  Keep its normalized
    # slack at zero without dividing by an all-zero positive scale.
    if positive_scale == 0.0:
        positive_scale = 1.0
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
            tuple(
                (item["net"], item["from"], item["to"])
                for item in path.get("cut_transitions", [])
            ),
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
                "cut-net-sequence+member-sink-transitions/max-fixed-delay"
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
        demand_by_net = {demand["net"]: demand for demand in demands}
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
            _normalize_cut_transitions(
                item.get("cut_transitions"),
                nets,
                demand_by_net,
                f"{context}.cut_transitions",
            )
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


def _static_predicted_delay(
    source: str,
    sinks: Sequence[str],
    adjacency: Mapping[str, Sequence[Mapping[str, Any]]],
    platform: Platform,
    constraints: Mapping[str, Any],
    distance_cache: Optional[Dict[str, Dict[str, float]]] = None,
) -> float:
    distance = None if distance_cache is None else distance_cache.get(source)
    if distance is None:
        distance = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            current, node = heapq.heappop(queue)
            if current != distance.get(node):
                continue
            for arc in adjacency.get(node, []):
                candidate = current + route_link_delay_ns(
                    platform,
                    arc["link"],
                    node,
                    arc["to"],
                    constraints,
                )
                if candidate < distance.get(arc["to"], float("inf")):
                    distance[arc["to"]] = candidate
                    heapq.heappush(queue, (candidate, arc["to"]))
        if distance_cache is not None:
            distance_cache[source] = distance
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
    timing_paths: Optional[Mapping[str, Any]],
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
                "lanes": link.transport_bits_per_cycle_per_direction,
                "delay_ns": route_link_delay_ns(
                    platform,
                    link.id,
                    arc["from"],
                    arc["to"],
                    constraints,
                ),
                "beta_ns": 1000.0 / link.fabric_clock_mhz,
                "is_sll": link.id in sll_links,
            }
        )

    normalized_timing = timing_paths or {
        "normalization": {
            "positive_slack_scale_ns": 1.0,
            "negative_slack_scale_ns": 1.0,
            "max_clock_period_ns": 1.0,
        },
        "paths": [],
    }
    path_by_net: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for path in normalized_timing["paths"]:
        for net in path["cut_nets"]:
            path_by_net[net].append(path)
    native_demands = []
    demand_index = {demand["net"]: index for index, demand in enumerate(demands)}
    static_distance_cache: Dict[str, Dict[str, float]] = {}
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
                    static_distance_cache,
                ),
            }
        )

    native_paths = []
    for index, path in enumerate(normalized_timing["paths"]):
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
        "normalization": normalized_timing["normalization"],
    }


def _write_native_input(
    path: Path,
    node_count: int,
    model: Mapping[str, Any],
    constraints: Mapping[str, Any],
    provider: str,
) -> None:
    normalization = model["normalization"]
    with path.open("w", encoding="utf-8") as stream:
        stream.write("EMUFLOW_TLR_INPUT_V7\n")
        stream.write(
            "PARAM "
            f"{node_count} {int(provider == ROUTE_TDM_PROVIDER)} "
            f"{constraints['max_iterations']} "
            f"{constraints.get('reroute_rounds', 8)} "
            f"{constraints.get('lambda_load', 2.0):.17g} "
            f"{constraints.get('lambda_timing', 4.0):.17g} "
            f"{constraints.get('lambda_history', 1.0):.17g} "
            f"{constraints.get('lambda_tdm', 0.1):.17g} "
            f"{constraints.get('tdm_ratio_quantum', 8)} "
            f"{constraints['frame_slots']} "
            f"{normalization['positive_slack_scale_ns']:.17g} "
            f"{normalization['negative_slack_scale_ns']:.17g} "
            f"{normalization['max_clock_period_ns']:.17g} "
            f"{int(constraints.get('tree_edge_sum_tdm', False))} "
            f"{constraints.get('tdm_min_ratio', 1)} "
            f"{int(constraints.get('hard_sll_capacity', False))} "
            f"{constraints.get('max_route_hops') or 0}\n"
        )
        for arc in model["arcs"]:
            stream.write(
                "ARC "
                f"{arc['index']} {arc['link_index']} {arc['from']} {arc['to']} "
                f"{arc['capacity_domain']} {arc['direction_group']} "
                f"{arc['opposite_arc']} {arc['capacity']} "
                f"{arc['lanes']} {arc['delay_ns']:.17g} "
                f"{arc['beta_ns']:.17g} {int(arc['is_sll'])}\n"
            )
        for demand in model["native_demands"]:
            sinks = ",".join(str(sink) for sink in demand["sinks"])
            stream.write(
                "DEMAND "
                f"{demand['index']} {demand['source']} {sinks} "
                f"{demand['width']} {demand['normalized_slack']:.17g} "
                f"{demand['predicted_delay_ns']:.17g}\n"
            )
        for timing_path in model["paths"]:
            demands = ",".join(str(item) for item in timing_path["demands"])
            stream.write(
                "PATH "
                f"{timing_path['index']} "
                f"{timing_path['clock_period_ns']:.17g} "
                f"{timing_path['baseline_slack_ns']:.17g} "
                f"{timing_path['fixed_delay_ns']:.17g} {demands}\n"
            )


def _parse_native_output(path: Path, model: Mapping[str, Any]) -> Dict[str, Any]:
    routes = {}
    locks = {}
    timing = {}
    metrics: Dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as stream:
        if stream.readline().strip() != "EMUFLOW_TLR_OUTPUT_V1":
            raise EmuFlowError("TLR router returned an invalid output header")
        for line in stream:
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
                    "baseline_candidate_feasible",
                    "balanced_candidate_feasible",
                    "selected_delay_demand_balanced",
                    "total_link_bit_hops",
                    "estimated_max_tdm_ratio",
                }:
                    value = int(value)
                metrics[fields[1]] = value
            else:
                raise EmuFlowError(f"TLR router returned malformed record: {line}")
    if len(routes) != len(model["demands"]):
        raise EmuFlowError("TLR router did not return exact demand coverage")
    if len(timing) != len(model["paths"]):
        raise EmuFlowError("TLR router did not return exact timing-path coverage")
    expected_metrics = {
        "iterations",
        "accepted_reroutes",
        "rolled_back_reroutes",
        "baseline_candidate_feasible",
        "balanced_candidate_feasible",
        "selected_delay_demand_balanced",
        "worst_slack_ns",
        "worst_normalized_slack",
        "estimated_worst_tdm_slack_ns",
        "estimated_worst_tdm_normalized_slack",
        "estimated_max_tdm_ratio",
        "max_utilization",
        "total_link_bit_hops",
    }
    if set(metrics) != expected_metrics:
        raise EmuFlowError(
            "TLR router returned incomplete metric coverage"
        )
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


def route_system_native(
    assignment: Mapping[str, Any],
    platform: Platform,
    constraints: Mapping[str, Any],
    timing_paths: Optional[Mapping[str, Any]] = None,
    executable: Optional[str] = None,
    provider: str = NATIVE_ROUTER_PROVIDER,
) -> Dict[str, Any]:
    if provider not in {
        NATIVE_ROUTER_PROVIDER,
        TLR_PROVIDER,
        ROUTE_TDM_PROVIDER,
    }:
        raise ValueError(f"unsupported native routing provider {provider!r}")
    if provider == NATIVE_ROUTER_PROVIDER and timing_paths is not None:
        raise ValueError(
            f"{NATIVE_ROUTER_PROVIDER} does not accept timing paths; "
            f"use {ROUTE_TDM_PROVIDER}"
        )
    if provider != NATIVE_ROUTER_PROVIDER and timing_paths is None:
        raise ValueError(f"{provider} requires normalized timing paths")
    nodes, model = _prepare_native_model(
        assignment, platform, constraints, timing_paths
    )
    resolved = resolve_native_executable("emuflow_tlr_router", executable)
    with tempfile.TemporaryDirectory(prefix="emuflow-tlr-") as temporary:
        root = Path(temporary)
        native_input = root / "router.in"
        native_output = root / "router.out"
        _write_native_input(
            native_input, len(nodes), model, constraints, provider
        )
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

    # The native-only records are no longer needed after output parsing.  On
    # multi-million-demand cases, releasing them before materializing the
    # public route schema avoids retaining two complete demand views.
    model["native_demands"].clear()
    _, arcs, capacities = build_directed_graph(platform, constraints)
    routes = []
    usage = {key: 0 for key in capacities}
    maximum_observed_hops = 0
    for demand_index, demand in enumerate(model["demands"]):
        native_route = native["routes"].pop(demand_index)
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
        _, _, route_hops = _validate_route_tree(route, arcs)
        maximum_observed_hops = max(maximum_observed_hops, route_hops)
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
    if timing_paths is not None:
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
                    **(
                        {"cut_transitions": path["cut_transitions"]}
                        if "cut_transitions" in path
                        else {}
                    ),
                    **native["timing"].pop(index),
                }
            )
    model["paths"].clear()
    result = {
        "schema": "emuflow.system-routes/v1",
        "design": assignment.get("design"),
        "platform": platform.name,
        "provider": provider,
        "constraints": dict(constraints),
        "demands": model["demands"],
        "routes": routes,
        "link_utilization": utilization,
        "direction_locks": direction_locks,
        "metrics": {
            "demands": len(model["demands"]),
            "routed_sinks": sum(len(route["sinks"]) for route in routes),
            "tree_edges": sum(len(route["tree_edges"]) for route in routes),
            "iterations": native["metrics"]["iterations"],
            "accepted_reroutes": native["metrics"]["accepted_reroutes"],
            "rolled_back_reroutes": native["metrics"][
                "rolled_back_reroutes"
            ],
            "max_link_utilization": max(
                (record["utilization"] for record in utilization), default=0.0
            ),
            "total_link_bit_hops": sum(usage.values()),
            "estimated_max_tdm_ratio": native["metrics"][
                "estimated_max_tdm_ratio"
            ],
            **(
                {"max_route_hops_observed": maximum_observed_hops}
                if constraints.get("max_route_hops") is not None
                else {}
            ),
        },
        **(
            {
                "joint_optimization": {
                    "method": (
                        "dac25-informed-delay-demand-balanced+"
                        "aspdac26-timing-refinement-v2"
                    ),
                    "objective": (
                        "lexicographic estimated TDM normalized slack, "
                        "route normalized slack, utilization, bit-hops"
                    ),
                    "ratio_model": "quantized-domain-load-over-lanes",
                    "candidate_generation": {
                        "shortest_path_tree": bool(
                            native["metrics"][
                                "baseline_candidate_feasible"
                            ]
                        ),
                        "delay_demand_balanced_tree": bool(
                            native["metrics"][
                                "balanced_candidate_feasible"
                            ]
                        ),
                        "selected": (
                            "delay-demand-balanced"
                            if native["metrics"][
                                "selected_delay_demand_balanced"
                            ]
                            else "shortest-path-tree"
                        ),
                    },
                    "reference": (
                        "DAC 2025 delay-demand-balanced die-level routing "
                        "and ASP-DAC 2026 timing-aware load-balanced "
                        "routing/rip-up-reroute"
                    ),
                }
            }
            if provider == ROUTE_TDM_PROVIDER
            else {}
        ),
    }
    if timing_paths is not None:
        result["timing"] = {
            "schema": STA_PATHS_SCHEMA,
            "normalization": timing_paths["normalization"],
            "compression": timing_paths["compression"],
            "paths": timing_records,
        }
        result["metrics"].update(
            {
                "worst_slack_ns": native["metrics"]["worst_slack_ns"],
                "worst_normalized_slack": native["metrics"][
                    "worst_normalized_slack"
                ],
                "estimated_worst_tdm_slack_ns": native["metrics"][
                    "estimated_worst_tdm_slack_ns"
                ],
                "estimated_worst_tdm_normalized_slack": native["metrics"][
                    "estimated_worst_tdm_normalized_slack"
                ],
            }
        )
    return result


def reconstruct_system_route_timing(
    assignment: Mapping[str, Any],
    platform: Platform,
    routes: Mapping[str, Any],
    timing_paths: Mapping[str, Any],
) -> Dict[str, Any]:
    """Evaluate any legal route provider against one normalized STA input."""
    validation = validate_system_routes(assignment, platform, routes)
    constraints = normalize_route_constraints(
        routes.get("constraints"), platform
    )
    _, arcs, capacities = build_directed_graph(platform, constraints)
    link_by_id = {link.id: link for link in platform.links}

    route_delay_by_net = {}
    capacity_usage = {key: 0 for key in capacities}
    for route in routes["routes"]:
        graph: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for edge in route["tree_edges"]:
            key = _arc_key(edge["link"], edge["from"], edge["to"])
            graph[edge["from"]].append((edge["to"], edge["link"]))
            capacity_usage[arcs[key]["capacity_key"]] += int(
                route["width_bits"]
            )
        delay_by_node = {route["source"]: 0.0}
        queue = deque([route["source"]])
        while queue:
            node = queue.popleft()
            for sink, link_id in graph[node]:
                delay_by_node[sink] = (
                    delay_by_node[node]
                    + route_link_delay_ns(
                        platform, link_id, node, sink, constraints
                    )
                )
                queue.append(sink)
        route_delay_by_net[route["net"]] = max(
            delay_by_node[sink] for sink in route["sinks"]
        )

    ratios = {}
    for key, capacity in capacities.items():
        lanes = link_by_id[
            capacity["link"]
        ].transport_bits_per_cycle_per_direction
        signals = capacity_usage[key]
        ratios[key] = estimate_tdm_ratio(
            signals,
            lanes,
            constraints,
            is_sll=capacity["link"] in constraints["sll_links"],
        )

    route_tdm_delay_by_net = {}
    for route in routes["routes"]:
        graph: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for edge in route["tree_edges"]:
            key = _arc_key(edge["link"], edge["from"], edge["to"])
            graph[edge["from"]].append(
                (
                    edge["to"],
                    edge["link"],
                    arcs[key]["capacity_key"],
                )
            )
        delay_by_node = {route["source"]: 0.0}
        queue = deque([route["source"]])
        while queue:
            node = queue.popleft()
            for sink, link_id, capacity_key in graph[node]:
                link = link_by_id[link_id]
                delay_by_node[sink] = (
                    delay_by_node[node]
                    + route_link_delay_ns(
                        platform, link_id, node, sink, constraints
                    )
                    + (
                        0.0
                        if link_id in constraints["sll_links"]
                        else (1000.0 / link.fabric_clock_mhz)
                        * (ratios[capacity_key] - 1)
                    )
                )
                queue.append(sink)
        if constraints.get("tree_edge_sum_tdm", False):
            route_tdm_delay_by_net[route["net"]] = sum(
                route_link_delay_ns(
                    platform,
                    edge["link"],
                    edge["from"],
                    edge["to"],
                    constraints,
                )
                + (
                    0.0
                    if edge["link"] in constraints["sll_links"]
                    else (
                        1000.0 / link_by_id[edge["link"]].fabric_clock_mhz
                    )
                    * (
                        ratios[
                            arcs[
                                _arc_key(
                                    edge["link"], edge["from"], edge["to"]
                                )
                            ]["capacity_key"]
                        ]
                        - 1
                    )
                )
                for edge in route["tree_edges"]
            )
        else:
            route_tdm_delay_by_net[route["net"]] = max(
                delay_by_node[sink] for sink in route["sinks"]
            )

    normalization = timing_paths["normalization"]
    path_records = []
    for path in timing_paths["paths"]:
        delay = path["fixed_delay_ns"] + sum(
            route_delay_by_net[net] for net in path["cut_nets"]
        )
        slack = path["clock_period_ns"] - delay
        normalized = (
            slack
            * path["clock_period_ns"]
            / (
                normalization["positive_slack_scale_ns"]
                * normalization["max_clock_period_ns"]
            )
            if slack >= 0.0
            else slack
            / (
                normalization["negative_slack_scale_ns"]
                * path["clock_period_ns"]
            )
        )
        tdm_delay = path["fixed_delay_ns"] + sum(
            route_tdm_delay_by_net[net] for net in path["cut_nets"]
        )
        tdm_slack = path["clock_period_ns"] - tdm_delay
        tdm_normalized = (
            tdm_slack
            * path["clock_period_ns"]
            / (
                normalization["positive_slack_scale_ns"]
                * normalization["max_clock_period_ns"]
            )
            if tdm_slack >= 0.0
            else tdm_slack
            / (
                normalization["negative_slack_scale_ns"]
                * path["clock_period_ns"]
            )
        )
        path_records.append(
            {
                "path": path["id"],
                "slack_ns": slack,
                "normalized_slack": normalized,
                "estimated_tdm_slack_ns": tdm_slack,
                "estimated_tdm_normalized_slack": tdm_normalized,
            }
        )
    if not path_records:
        raise ValidationError(
            "route timing reconstruction has no normalized STA paths"
        )
    worst_slack = min(
        path_records,
        key=lambda record: (
            record["slack_ns"],
            record["path"],
        ),
    )
    worst_normalized = min(
        path_records,
        key=lambda record: (
            record["normalized_slack"],
            record["path"],
        ),
    )
    worst_tdm_slack = min(
        path_records,
        key=lambda record: (
            record["estimated_tdm_slack_ns"],
            record["path"],
        ),
    )
    worst_tdm_normalized = min(
        path_records,
        key=lambda record: (
            record["estimated_tdm_normalized_slack"],
            record["path"],
        ),
    )
    return {
        **validation,
        "provider": routes["provider"],
        "timing_paths_original": timing_paths["compression"][
            "original_paths"
        ],
        "timing_paths_compressed": timing_paths["compression"][
            "compressed_paths"
        ],
        "worst_slack_path": worst_slack["path"],
        "worst_slack_ns": worst_slack["slack_ns"],
        "worst_normalized_path": worst_normalized["path"],
        "worst_normalized_slack": worst_normalized[
            "normalized_slack"
        ],
        "estimated_worst_tdm_slack_path": worst_tdm_slack["path"],
        "estimated_worst_tdm_slack_ns": worst_tdm_slack[
            "estimated_tdm_slack_ns"
        ],
        "estimated_worst_tdm_normalized_path": (
            worst_tdm_normalized["path"]
        ),
        "estimated_worst_tdm_normalized_slack": worst_tdm_normalized[
            "estimated_tdm_normalized_slack"
        ],
        "estimated_max_tdm_ratio": max(ratios.values(), default=1),
    }


def validate_native_system_routes(
    assignment: Mapping[str, Any],
    platform: Platform,
    routes: Mapping[str, Any],
    timing_paths: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    validation = validate_system_routes(assignment, platform, routes)
    provider = routes.get("provider")
    if provider not in {
        NATIVE_ROUTER_PROVIDER,
        TLR_PROVIDER,
        ROUTE_TDM_PROVIDER,
    }:
        raise ValidationError(
            "routes.provider: expected a supported native routing provider"
        )
    if provider == NATIVE_ROUTER_PROVIDER and timing_paths is not None:
        raise ValidationError(
            "native load-balanced routes must not use STA timing input"
        )
    if provider != NATIVE_ROUTER_PROVIDER and timing_paths is None:
        raise ValidationError("timing-aware routes require normalized STA input")
    if routes.get("provider") == ROUTE_TDM_PROVIDER:
        joint = routes.get("joint_optimization")
        if not isinstance(joint, dict) or joint.get("method") != (
            "dac25-informed-delay-demand-balanced+"
            "aspdac26-timing-refinement-v2"
        ):
            raise ValidationError(
                "routes.joint_optimization: invalid co-optimization metadata"
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
    route_by_net = {}
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
                delay_by_node[sink] = delay_by_node[node] + route_link_delay_ns(
                    platform, link_id, node, sink, constraints
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
        route_by_net[route["net"]] = route
        route_signature_by_net[route["net"]] = sorted(signature)

    if provider == NATIVE_ROUTER_PROVIDER:
        if "timing" in routes:
            raise ValidationError(
                "native load-balanced routes must not contain timing records"
            )
        return {
            **validation,
            "provider": provider,
            "direction_locks": len(lock_by_link),
            "predicted_delay_check": "pass",
        }

    # Independently reconstruct the route/TDM co-optimization proxy used by
    # the native router.  This is deliberately separate from native output:
    # a corrupt or incorrectly implemented optimizer cannot self-certify.
    _, arcs, capacities = build_directed_graph(platform, constraints)
    capacity_usage = {key: 0 for key in capacities}
    for route in routes["routes"]:
        width = int(route["width_bits"])
        for edge in route["tree_edges"]:
            key = _arc_key(edge["link"], edge["from"], edge["to"])
            capacity_usage[arcs[key]["capacity_key"]] += width
    link_by_id = {link.id: link for link in platform.links}
    ratios = {}
    for key, capacity in capacities.items():
        lanes = link_by_id[
            capacity["link"]
        ].transport_bits_per_cycle_per_direction
        signals = capacity_usage[key]
        ratios[key] = estimate_tdm_ratio(
            signals,
            lanes,
            constraints,
            is_sll=capacity["link"] in constraints["sll_links"],
        )
    route_tdm_delay_by_net = {}
    for net, route in route_by_net.items():
        graph: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        for edge in route["tree_edges"]:
            key = _arc_key(edge["link"], edge["from"], edge["to"])
            graph[edge["from"]].append(
                (edge["to"], edge["link"], arcs[key]["capacity_key"])
            )
        delay_by_node = {route["source"]: 0.0}
        queue = deque([route["source"]])
        while queue:
            node = queue.popleft()
            for sink, link_id, capacity_key in graph[node]:
                link = link_by_id[link_id]
                delay_by_node[sink] = (
                    delay_by_node[node]
                    + route_link_delay_ns(
                        platform, link_id, node, sink, constraints
                    )
                    + (1000.0 / link.fabric_clock_mhz)
                    * (ratios[capacity_key] - 1)
                )
                queue.append(sink)
        if constraints.get("tree_edge_sum_tdm", False):
            route_tdm_delay_by_net[net] = sum(
                route_link_delay_ns(
                    platform,
                    edge["link"],
                    edge["from"],
                    edge["to"],
                    constraints,
                )
                + (
                    0.0
                    if edge["link"] in constraints["sll_links"]
                    else (
                        1000.0 / link_by_id[edge["link"]].fabric_clock_mhz
                    )
                    * (
                        ratios[
                            arcs[
                                _arc_key(
                                    edge["link"], edge["from"], edge["to"]
                                )
                            ]["capacity_key"]
                        ]
                        - 1
                    )
                )
                for edge in route["tree_edges"]
            )
        else:
            route_tdm_delay_by_net[net] = max(
                delay_by_node[sink] for sink in route["sinks"]
            )

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
    estimated_worst_tdm_slack = float("inf")
    estimated_worst_tdm_normalized = float("inf")
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
        if actual.get("cut_transitions") != expected.get(
            "cut_transitions"
        ):
            raise ValidationError(
                f"routes.timing path {expected['id']!r}.cut_transitions: "
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
        tdm_delay = expected["fixed_delay_ns"] + sum(
            route_tdm_delay_by_net[net]
            for net in expected["cut_nets"]
        )
        tdm_slack = expected["clock_period_ns"] - tdm_delay
        if tdm_slack >= 0.0:
            tdm_normalized = (
                tdm_slack
                * expected["clock_period_ns"]
                / (positive_scale * max_period)
            )
        else:
            tdm_normalized = (
                tdm_slack
                / (negative_scale * expected["clock_period_ns"])
            )
        estimated_worst_tdm_slack = min(
            estimated_worst_tdm_slack, tdm_slack
        )
        estimated_worst_tdm_normalized = min(
            estimated_worst_tdm_normalized, tdm_normalized
        )
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
    for key, expected in (
        ("estimated_worst_tdm_slack_ns", estimated_worst_tdm_slack),
        (
            "estimated_worst_tdm_normalized_slack",
            estimated_worst_tdm_normalized,
        ),
        ("estimated_max_tdm_ratio", max(ratios.values(), default=1)),
    ):
        if abs(float(metrics.get(key, float("nan"))) - expected) > 1.0e-9:
            raise ValidationError(
                f"routes.metrics.{key} does not match independent "
                "route/TDM proxy recomputation"
            )
    common_timing = reconstruct_system_route_timing(
        assignment, platform, routes, timing_paths
    )
    for key, expected in (
        ("worst_slack_ns", worst_slack),
        ("worst_normalized_slack", worst_normalized),
        ("estimated_worst_tdm_slack_ns", estimated_worst_tdm_slack),
        (
            "estimated_worst_tdm_normalized_slack",
            estimated_worst_tdm_normalized,
        ),
        ("estimated_max_tdm_ratio", max(ratios.values(), default=1)),
    ):
        if abs(float(common_timing[key]) - expected) > 1.0e-9:
            raise ValidationError(
                f"common route timing reconstruction disagrees on {key}"
            )
    return {
        **common_timing,
        "direction_locks": len(locks),
        "accepted_reroutes": metrics.get("accepted_reroutes"),
        "rolled_back_reroutes": metrics.get("rolled_back_reroutes"),
    }
