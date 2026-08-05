"""Adapters and an independent checker for the 2025 EDA Elite contest.

The contest model is deliberately preserved separately from BoardDB.  The
BoardDB and partition-assignment artifacts are execution adapters for EmuFlow;
the normalized contest instance remains the source of truth for scoring.
"""

import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ValidationError
from .io import read_json, write_json
from .partition import PARTITION_ASSIGNMENT_SCHEMA
from .routing import SYSTEM_ROUTES_SCHEMA


EDA2025_INSTANCE_SCHEMA = "emuflow.contest-eda2025-instance/v1"
EDA2025_EVALUATION_SCHEMA = "emuflow.contest-eda2025-evaluation/v1"
EDA2025_SOURCE_URL = (
    "https://edaoss.icisc.cn/file/cacheFile/2025/8/11/"
    "1e213a00cbd94e2b91e997740753cb60.pdf"
)


def _data_lines(path: Path) -> List[Tuple[int, str]]:
    result = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, start=1):
            line = raw.split("#", 1)[0].strip()
            if line:
                result.append((line_number, line))
    return result


def _positive_int(raw: str, context: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise ValidationError(f"{context}: expected an integer") from error
    if value <= 0:
        raise ValidationError(f"{context}: expected a positive integer")
    return value


def parse_design_info(path: Path) -> Tuple[List[str], Dict[str, int]]:
    fpga_ids: List[str] = []
    limits: Dict[str, int] = {}
    for line_number, line in _data_lines(path):
        fields = line.split()
        if len(fields) != 2:
            raise ValidationError(
                f"{path}:{line_number}: expected '<FPGA_ID> <Max_IO>'"
            )
        fpga_id, raw_limit = fields
        if fpga_id in limits:
            raise ValidationError(
                f"{path}:{line_number}: duplicate FPGA {fpga_id!r}"
            )
        fpga_ids.append(fpga_id)
        limits[fpga_id] = _positive_int(
            raw_limit, f"{path}:{line_number} Max_IO"
        )
    if not fpga_ids:
        raise ValidationError(f"{path}: expected at least one FPGA")
    return fpga_ids, limits


def parse_topology(path: Path, fpga_ids: Sequence[str]) -> List[List[int]]:
    expected = list(fpga_ids)
    rows: Dict[str, List[int]] = {}
    for line_number, line in _data_lines(path):
        fpga_id, separator, values = line.partition(":")
        fpga_id = fpga_id.strip()
        if not separator or not fpga_id:
            raise ValidationError(
                f"{path}:{line_number}: expected '<FPGA_ID>: c1,c2,...'"
            )
        if fpga_id not in expected:
            raise ValidationError(
                f"{path}:{line_number}: unknown FPGA {fpga_id!r}"
            )
        if fpga_id in rows:
            raise ValidationError(
                f"{path}:{line_number}: duplicate FPGA {fpga_id!r}"
            )
        raw_channels = [item.strip() for item in values.split(",")]
        if len(raw_channels) != len(expected):
            raise ValidationError(
                f"{path}:{line_number}: expected {len(expected)} channels"
            )
        channels = []
        for index, raw_channel in enumerate(raw_channels):
            try:
                channel = int(raw_channel)
            except ValueError as error:
                raise ValidationError(
                    f"{path}:{line_number}: channel {index} is not an integer"
                ) from error
            if channel < 0:
                raise ValidationError(
                    f"{path}:{line_number}: channel {index} is negative"
                )
            channels.append(channel)
        rows[fpga_id] = channels
    missing = [fpga_id for fpga_id in expected if fpga_id not in rows]
    if missing:
        raise ValidationError(f"{path}: missing topology rows {missing}")
    matrix = [rows[fpga_id] for fpga_id in expected]
    for left in range(len(expected)):
        if matrix[left][left] != 0:
            raise ValidationError(
                f"{path}: topology diagonal for {expected[left]!r} must be zero"
            )
        for right in range(left + 1, len(expected)):
            if matrix[left][right] != matrix[right][left]:
                raise ValidationError(
                    f"{path}: topology must be symmetric at "
                    f"{expected[left]!r}, {expected[right]!r}"
                )
    return matrix


def parse_design_nets(path: Path) -> List[Dict[str, Any]]:
    nets = []
    for net_index, (line_number, line) in enumerate(_data_lines(path), start=1):
        fields = line.split()
        if len(fields) < 3:
            raise ValidationError(
                f"{path}:{line_number}: expected source, weight, and sinks"
            )
        source = fields[0]
        weight = _positive_int(fields[1], f"{path}:{line_number} weight")
        if weight != 1:
            raise ValidationError(
                f"{path}:{line_number}: the published 2025 benchmark model "
                "requires unit net weights"
            )
        sinks = fields[2:]
        if source in sinks or len(set(sinks)) != len(sinks):
            raise ValidationError(
                f"{path}:{line_number}: net endpoints must be distinct"
            )
        nets.append(
            {
                "id": f"net_{net_index:06d}",
                "source_node": source,
                "sink_nodes": sinks,
                "weight": weight,
                "source_line": line_number,
            }
        )
    if not nets:
        raise ValidationError(f"{path}: expected at least one net")
    return nets


def parse_partition_assignment(
    path: Path, fpga_ids: Sequence[str], required_nodes: Sequence[str]
) -> Dict[str, str]:
    known = set(fpga_ids)
    assignment: Dict[str, str] = {}
    seen_fpgas = set()
    for line_number, line in _data_lines(path):
        fpga_id, separator, nodes = line.partition(":")
        fpga_id = fpga_id.strip()
        if not separator or fpga_id not in known:
            raise ValidationError(
                f"{path}:{line_number}: expected a known '<FPGA_ID>: ...'"
            )
        if fpga_id in seen_fpgas:
            raise ValidationError(
                f"{path}:{line_number}: duplicate FPGA {fpga_id!r}"
            )
        seen_fpgas.add(fpga_id)
        for node in nodes.split():
            if node in assignment:
                raise ValidationError(
                    f"{path}:{line_number}: node {node!r} is assigned twice"
                )
            assignment[node] = fpga_id
    missing_fpgas = sorted(known - seen_fpgas)
    if missing_fpgas:
        raise ValidationError(
            f"{path}: missing partition rows for FPGAs {missing_fpgas}"
        )
    missing = sorted(set(required_nodes) - set(assignment))
    if missing:
        preview = missing[:8]
        raise ValidationError(
            f"{path}: {len(missing)} referenced nodes are unassigned: {preview}"
        )
    return assignment


def _validate_parameters(
    alpha_ns: float,
    beta_ns: float,
    ratio_quantum: int,
    max_ratio: int,
    topology_change_fraction: float,
) -> None:
    if (
        isinstance(alpha_ns, bool)
        or not isinstance(alpha_ns, (int, float))
        or alpha_ns <= 0
    ):
        raise ValidationError("alpha_ns: expected a positive number")
    if (
        isinstance(beta_ns, bool)
        or not isinstance(beta_ns, (int, float))
        or beta_ns < 0
    ):
        raise ValidationError("beta_ns: expected a non-negative number")
    for name, value in (("ratio_quantum", ratio_quantum), ("max_ratio", max_ratio)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError(f"{name}: expected a positive integer")
    if max_ratio % ratio_quantum:
        raise ValidationError("max_ratio must be a multiple of ratio_quantum")
    if (
        isinstance(topology_change_fraction, bool)
        or not isinstance(topology_change_fraction, (int, float))
        or not 0.0 <= topology_change_fraction <= 1.0
    ):
        raise ValidationError(
            "topology_change_fraction: expected a number between zero and one"
        )


def _check_io_limits(
    matrix: Sequence[Sequence[int]],
    fpga_ids: Sequence[str],
    limits: Mapping[str, int],
    context: str,
) -> None:
    for index, fpga_id in enumerate(fpga_ids):
        used = sum(matrix[index])
        if used > limits[fpga_id]:
            raise ValidationError(
                f"{context}: FPGA {fpga_id!r} uses {used} channels, "
                f"above Max_IO {limits[fpga_id]}"
            )


def import_eda2025_instance(
    info_path: Path,
    net_path: Path,
    topology_path: Path,
    assignment_path: Path,
    output_dir: Path,
    name: str,
    alpha_ns: float = 0.7,
    beta_ns: float = 30.0,
    ratio_quantum: int = 8,
    max_ratio: int = 512,
    topology_change_fraction: float = 0.3,
) -> Dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("name: expected a non-empty string")
    _validate_parameters(
        alpha_ns, beta_ns, ratio_quantum, max_ratio, topology_change_fraction
    )
    fpga_ids, limits = parse_design_info(info_path)
    topology = parse_topology(topology_path, fpga_ids)
    _check_io_limits(topology, fpga_ids, limits, "initial topology")
    nets = parse_design_nets(net_path)
    required_nodes = sorted(
        {
            node
            for net in nets
            for node in [net["source_node"], *net["sink_nodes"]]
        }
    )
    node_assignment = parse_partition_assignment(
        assignment_path, fpga_ids, required_nodes
    )

    instance = {
        "schema": EDA2025_INSTANCE_SCHEMA,
        "name": name,
        "source": {
            "contest": "2025 EDA Elite Challenge",
            "problem": "Reconfigurable multi-FPGA system routing",
            "specification_url": EDA2025_SOURCE_URL,
        },
        "parameters": {
            "alpha_ns": float(alpha_ns),
            "beta_ns": float(beta_ns),
            "ratio_quantum": ratio_quantum,
            "max_ratio": max_ratio,
            "topology_change_fraction": float(topology_change_fraction),
        },
        "fpga_ids": fpga_ids,
        "max_external_channels": limits,
        "initial_topology": topology,
        "nets": nets,
        "node_assignment": dict(sorted(node_assignment.items())),
    }

    boarddb = {
        "schema": "emuflow.boarddb/v1",
        "platform": {
            "name": name,
            "kind": "virtual",
            "description": (
                "2025 EDA Elite abstract multi-FPGA topology; exact contest "
                "timing is evaluated from the normalized contest instance"
            ),
        },
        "fpgas": [
            {
                "id": fpga_id,
                "part": "eda-elite-2025-abstract",
                "utilization_limit": 1.0,
                "capacity": {"lut": 1},
            }
            for fpga_id in fpga_ids
        ],
        "links": [
            {
                "id": f"contest_link_{left:03d}_{right:03d}",
                "endpoints": [fpga_ids[left], fpga_ids[right]],
                "direction": "full_duplex",
                "mode": "abstract",
                "data_lanes_per_direction": topology[left][right],
                # One TDM slot is alpha ns; the exact contest base term is
                # represented by the separately emitted link-delay override.
                "fabric_clock_mhz": 1000.0 / float(alpha_ns),
                "latency_cycles": 0,
            }
            for left in range(len(fpga_ids))
            for right in range(left + 1, len(fpga_ids))
            if topology[left][right] > 0
        ],
    }

    cut_nets = []
    for net in nets:
        source_fpga = node_assignment[net["source_node"]]
        sink_fpgas = sorted(
            {
                node_assignment[node]
                for node in net["sink_nodes"]
                if node_assignment[node] != source_fpga
            }
        )
        if sink_fpgas:
            cut_nets.append(
                {
                    "net": net["id"],
                    "cut_class": "register_output",
                    "source_fpgas": [source_fpga],
                    "sink_fpgas": sink_fpgas,
                    "sink_endpoints": sum(
                        node_assignment[node] != source_fpga
                        for node in net["sink_nodes"]
                    ),
                }
            )
    assignment = {
        "schema": PARTITION_ASSIGNMENT_SCHEMA,
        "design": name,
        "platform": name,
        "cut_nets": cut_nets,
    }
    contest_period = max(
        1.0,
        (len(fpga_ids) - 1) * (beta_ns + alpha_ns * max_ratio),
    )
    timing_paths = {
        "schema": "emuflow.sta-paths/v1",
        "design": name,
        "paths": [
            {
                "id": f"contest_path_{cut['net']}_{sink}",
                "clock_domain": "contest_max_path_delay",
                "clock_period_ns": contest_period,
                "slack_ns": 0.0,
                "fixed_delay_ns": 0.0,
                "cut_nets": [cut["net"]],
                "cut_transitions": [
                    {
                        "net": cut["net"],
                        "from": cut["source_fpgas"][0],
                        "to": sink,
                    }
                ],
            }
            for cut in cut_nets
            for sink in cut["sink_fpgas"]
        ],
    }
    # The native kernel reconstructs a TDM hop as
    # ``delay_ns + slot_ns * (ratio - 1)``.  Supplying beta + alpha as
    # the base therefore makes its optimization objective exactly equal to
    # the contest's ``beta + alpha * ratio`` expression.
    link_delay = {
        link["id"]: float(beta_ns + alpha_ns) for link in boarddb["links"]
    }
    constraints = {
        "schema": "emuflow.system-route-constraints/v1",
        "frame_slots": max_ratio,
        "max_iterations": 20,
        "unavailable_links": [],
        "link_delay_ns": link_delay,
        "sll_links": [],
        "shared_capacity_links": [link["id"] for link in boarddb["links"]],
        "reroute_rounds": 8,
        "lambda_load": 2.0,
        "lambda_timing": 4.0,
        "lambda_history": 1.0,
        "lambda_tdm": 0.1,
        "tdm_ratio_quantum": ratio_quantum,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "contest_instance.json", instance)
    write_json(output_dir / "boarddb.json", boarddb)
    write_json(output_dir / "partition_assignment.json", assignment)
    write_json(output_dir / "route_constraints.json", constraints)
    write_json(output_dir / "contest_timing_paths.json", timing_paths)
    return {
        "status": "pass",
        "schema": EDA2025_INSTANCE_SCHEMA,
        "name": name,
        "fpgas": len(fpga_ids),
        "logical_nodes": len(node_assignment),
        "nets": len(nets),
        "cut_nets": len(cut_nets),
        "physical_channel_pairs": len(boarddb["links"]),
        "artifacts": {
            "instance": str(output_dir / "contest_instance.json"),
            "boarddb": str(output_dir / "boarddb.json"),
            "assignment": str(output_dir / "partition_assignment.json"),
            "route_constraints": str(output_dir / "route_constraints.json"),
            "timing_paths": str(output_dir / "contest_timing_paths.json"),
        },
    }


def _validate_instance(value: Mapping[str, Any]) -> None:
    if value.get("schema") != EDA2025_INSTANCE_SCHEMA:
        raise ValidationError(
            f"instance.schema: expected {EDA2025_INSTANCE_SCHEMA!r}"
        )
    fpga_ids = value.get("fpga_ids")
    if (
        not isinstance(fpga_ids, list)
        or not fpga_ids
        or not all(isinstance(item, str) and item for item in fpga_ids)
        or len(set(fpga_ids)) != len(fpga_ids)
    ):
        raise ValidationError("instance.fpga_ids: invalid")
    limits = value.get("max_external_channels")
    if not isinstance(limits, dict) or set(limits) != set(fpga_ids):
        raise ValidationError("instance.max_external_channels: invalid")
    if any(
        isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0
        for limit in limits.values()
    ):
        raise ValidationError(
            "instance.max_external_channels: limits must be positive integers"
        )
    topology = value.get("initial_topology")
    if (
        not isinstance(topology, list)
        or len(topology) != len(fpga_ids)
        or any(not isinstance(row, list) or len(row) != len(fpga_ids) for row in topology)
    ):
        raise ValidationError("instance.initial_topology: invalid dimensions")
    for left, row in enumerate(topology):
        for right, channel in enumerate(row):
            if (
                isinstance(channel, bool)
                or not isinstance(channel, int)
                or channel < 0
            ):
                raise ValidationError(
                    "instance.initial_topology: channels must be "
                    "non-negative integers"
                )
            if left == right and channel != 0:
                raise ValidationError(
                    "instance.initial_topology: diagonal must be zero"
                )
            if channel != topology[right][left]:
                raise ValidationError(
                    "instance.initial_topology: matrix must be symmetric"
                )
    _check_io_limits(topology, fpga_ids, limits, "initial topology")
    parameters = value.get("parameters")
    expected_parameters = {
        "alpha_ns",
        "beta_ns",
        "ratio_quantum",
        "max_ratio",
        "topology_change_fraction",
    }
    if not isinstance(parameters, dict) or set(parameters) != expected_parameters:
        raise ValidationError("instance.parameters: invalid field coverage")
    _validate_parameters(**parameters)
    assignment = value.get("node_assignment")
    if not isinstance(assignment, dict) or any(
        not isinstance(node, str)
        or not node
        or fpga_id not in fpga_ids
        for node, fpga_id in assignment.items()
    ):
        raise ValidationError("instance.node_assignment: invalid")
    nets = value.get("nets")
    if not isinstance(nets, list) or not nets:
        raise ValidationError("instance.nets: expected a non-empty array")
    net_ids = set()
    for index, net in enumerate(nets):
        context = f"instance.nets[{index}]"
        if not isinstance(net, dict):
            raise ValidationError(f"{context}: expected an object")
        net_id = net.get("id")
        source = net.get("source_node")
        sinks = net.get("sink_nodes")
        if (
            not isinstance(net_id, str)
            or not net_id
            or net_id in net_ids
        ):
            raise ValidationError(f"{context}.id: invalid or duplicate")
        net_ids.add(net_id)
        if source not in assignment:
            raise ValidationError(f"{context}.source_node: unassigned")
        if (
            not isinstance(sinks, list)
            or not sinks
            or not all(isinstance(sink, str) and sink in assignment for sink in sinks)
            or source in sinks
            or len(set(sinks)) != len(sinks)
        ):
            raise ValidationError(f"{context}.sink_nodes: invalid")
        if net.get("weight") != 1:
            raise ValidationError(f"{context}.weight: expected one")


def _topology_change(
    initial: Sequence[Sequence[int]], updated: Sequence[Sequence[int]]
) -> Tuple[int, int]:
    initial_channels = 0
    changed_channels = 0
    for left in range(len(initial)):
        for right in range(left + 1, len(initial)):
            initial_channels += initial[left][right]
            changed_channels += abs(initial[left][right] - updated[left][right])
    return initial_channels, changed_channels


def _route_sink_paths(
    route: Mapping[str, Any],
    pair_delays: Mapping[Tuple[str, str], float],
) -> Dict[str, Tuple[List[str], float]]:
    source = route["source"]
    graph: Dict[str, List[str]] = defaultdict(list)
    indegree: Dict[str, int] = defaultdict(int)
    edge_set = set()
    for edge in route.get("tree_edges", []):
        if not isinstance(edge, dict):
            raise ValidationError(f"route {route.get('id')!r}: malformed edge")
        left, right = edge.get("from"), edge.get("to")
        if not isinstance(left, str) or not isinstance(right, str) or left == right:
            raise ValidationError(f"route {route.get('id')!r}: malformed edge")
        directed = (left, right)
        pair = tuple(sorted(directed))
        if directed in edge_set or pair not in pair_delays:
            raise ValidationError(
                f"route {route.get('id')!r}: duplicate or unavailable edge {directed}"
            )
        edge_set.add(directed)
        graph[left].append(right)
        indegree[right] += 1
    delay = {source: 0.0}
    paths = {source: [source]}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for sink in graph[node]:
            if sink in delay:
                raise ValidationError(
                    f"route {route.get('id')!r}: tree has a cycle or reconvergence"
                )
            delay[sink] = delay[node] + pair_delays[tuple(sorted((node, sink)))]
            paths[sink] = [*paths[node], sink]
            queue.append(sink)
    edge_nodes = {node for edge in edge_set for node in edge}
    if not edge_nodes <= set(delay):
        raise ValidationError(f"route {route.get('id')!r}: disconnected tree")
    missing = sorted(set(route["sinks"]) - set(delay))
    if missing:
        raise ValidationError(
            f"route {route.get('id')!r}: unreachable sinks {missing}"
        )
    if any(count != 1 for node, count in indegree.items() if node != source):
        raise ValidationError(f"route {route.get('id')!r}: not an arborescence")
    return {sink: (paths[sink], delay[sink]) for sink in route["sinks"]}


def _route_path_delays(
    route: Mapping[str, Any],
    pair_delays: Mapping[Tuple[str, str], float],
) -> Dict[str, float]:
    return {
        sink: delay
        for sink, (_, delay) in _route_sink_paths(route, pair_delays).items()
    }


def _write_official_solution(
    output_dir: Path,
    instance: Mapping[str, Any],
    route_by_net: Mapping[str, Mapping[str, Any]],
    pair_delays: Mapping[Tuple[str, str], float],
    topology: Sequence[Sequence[int]],
) -> None:
    """Write the contest's text outputs from independently checked routes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fpga_ids = instance["fpga_ids"]
    fpga_number = {
        fpga_id: index for index, fpga_id in enumerate(fpga_ids, start=1)
    }
    assignment = instance["node_assignment"]
    records = []
    for net in instance["nets"]:
        net_id = net["id"]
        route = route_by_net.get(net_id)
        if route is None:
            continue
        sink_paths = _route_sink_paths(route, pair_delays)
        endpoint_paths = []
        source_fpga = assignment[net["source_node"]]
        for sink_node in net["sink_nodes"]:
            sink_fpga = assignment[sink_node]
            if sink_fpga == source_fpga:
                continue
            path, delay = sink_paths[sink_fpga]
            endpoint_paths.append((path, delay))
        records.append(
            (
                -max(delay for _, delay in endpoint_paths),
                net["source_line"],
                endpoint_paths,
            )
        )
    records.sort(key=lambda record: (record[0], record[1]))
    route_lines = []
    for _, source_line, endpoint_paths in records:
        route_lines.append(f"[net {source_line}]")
        for path, delay in endpoint_paths:
            encoded_path = ",".join(str(fpga_number[node]) for node in path)
            route_lines.append(f"[{encoded_path}] [{delay:.10g}]")
        route_lines.append("")
    (output_dir / "design.route.out").write_text(
        "\n".join(route_lines), encoding="utf-8"
    )

    topology_lines = [
        f"{fpga_id}: {','.join(str(channel) for channel in topology[index])}"
        for index, fpga_id in enumerate(fpga_ids)
    ]
    (output_dir / "design.newtopo").write_text(
        "\n".join(topology_lines) + "\n", encoding="utf-8"
    )


def evaluate_eda2025_routes(
    instance_path: Path,
    routes_path: Path,
    output_path: Optional[Path] = None,
    new_topology_path: Optional[Path] = None,
    runtime_seconds: float = 0.0,
    official_output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    instance = read_json(instance_path)
    _validate_instance(instance)
    routes = read_json(routes_path)
    if routes.get("schema") != SYSTEM_ROUTES_SCHEMA:
        raise ValidationError(
            f"routes.schema: expected {SYSTEM_ROUTES_SCHEMA!r}"
        )
    if (
        isinstance(runtime_seconds, bool)
        or not isinstance(runtime_seconds, (int, float))
        or runtime_seconds < 0
    ):
        raise ValidationError("runtime_seconds: expected a non-negative number")
    fpga_ids = instance["fpga_ids"]
    topology = (
        parse_topology(new_topology_path, fpga_ids)
        if new_topology_path is not None
        else instance["initial_topology"]
    )
    _check_io_limits(
        topology,
        fpga_ids,
        instance["max_external_channels"],
        "evaluated topology",
    )
    initial_channels, changed_channels = _topology_change(
        instance["initial_topology"], topology
    )
    allowed_changes = math.floor(
        initial_channels
        * instance["parameters"]["topology_change_fraction"]
        + 1.0e-12
    )
    if changed_channels > allowed_changes:
        raise ValidationError(
            f"topology changes {changed_channels} channels; allowed {allowed_changes}"
        )

    net_by_id = {net["id"]: net for net in instance["nets"]}
    node_assignment = instance["node_assignment"]
    expected = {}
    for net_id, net in net_by_id.items():
        source = node_assignment[net["source_node"]]
        sinks = sorted(
            {
                node_assignment[node]
                for node in net["sink_nodes"]
                if node_assignment[node] != source
            }
        )
        if sinks:
            expected[net_id] = (source, sinks)
    raw_routes = routes.get("routes")
    if not isinstance(raw_routes, list):
        raise ValidationError("routes.routes: expected an array")
    route_by_net = {
        route.get("net"): route for route in raw_routes if isinstance(route, dict)
    }
    if len(route_by_net) != len(raw_routes) or set(route_by_net) != set(expected):
        raise ValidationError("routes.routes: contest cut-net coverage is not exact")

    pair_nets: Dict[Tuple[str, str], set] = defaultdict(set)
    for net_id, route in route_by_net.items():
        source, sinks = expected[net_id]
        if route.get("source") != source or route.get("sinks") != sinks:
            raise ValidationError(
                f"route for {net_id!r}: source/sinks do not match contest assignment"
            )
        raw_edges = route.get("tree_edges")
        if not isinstance(raw_edges, list):
            raise ValidationError(f"route for {net_id!r}: expected tree_edges")
        for edge in raw_edges:
            if not isinstance(edge, dict):
                raise ValidationError(f"route for {net_id!r}: malformed edge")
            left, right = edge.get("from"), edge.get("to")
            if (
                not isinstance(left, str)
                or not isinstance(right, str)
                or left == right
            ):
                raise ValidationError(f"route for {net_id!r}: malformed edge")
            pair_nets[tuple(sorted((left, right)))].add(net_id)

    index = {fpga_id: item for item, fpga_id in enumerate(fpga_ids)}
    pair_records = []
    pair_delays = {}
    quantum = instance["parameters"]["ratio_quantum"]
    for pair in sorted(pair_nets):
        if len(pair) != 2 or pair[0] not in index or pair[1] not in index:
            raise ValidationError(f"routes use unknown FPGA pair {pair}")
        channels = topology[index[pair[0]]][index[pair[1]]]
        if channels <= 0:
            raise ValidationError(f"routes use disconnected FPGA pair {pair}")
        load = len(pair_nets[pair])
        ratio = math.ceil(load / channels / quantum) * quantum
        if ratio > instance["parameters"]["max_ratio"]:
            raise ValidationError(
                f"FPGA pair {pair} requires TDM ratio {ratio}, above "
                f"Rmax {instance['parameters']['max_ratio']}"
            )
        delay = (
            instance["parameters"]["beta_ns"]
            + instance["parameters"]["alpha_ns"] * ratio
        )
        pair_delays[pair] = delay
        pair_records.append(
            {
                "fpgas": list(pair),
                "channels": channels,
                "routed_nets": load,
                "tdm_ratio": ratio,
                "hop_delay_ns": delay,
            }
        )

    net_records = []
    worst_delay = 0.0
    for net_id in sorted(route_by_net):
        sink_delays = _route_path_delays(route_by_net[net_id], pair_delays)
        maximum = max(sink_delays.values())
        worst_delay = max(worst_delay, maximum)
        net_records.append(
            {
                "net": net_id,
                "sink_delay_ns": dict(sorted(sink_delays.items())),
                "max_delay_ns": maximum,
            }
        )
    score = worst_delay * (1.0 + 0.2 * runtime_seconds / 3600.0)
    result = {
        "schema": EDA2025_EVALUATION_SCHEMA,
        "status": "pass",
        "instance": instance["name"],
        "model": {
            "tdm_ratio": "ceil_to_quantum(unique_routed_nets/channels)",
            "hop_delay_ns": "beta_ns + alpha_ns * tdm_ratio",
            "topology_change_counting": "undirected upper triangle",
        },
        "topology": {
            "initial_channels": initial_channels,
            "changed_channels": changed_channels,
            "allowed_changed_channels": allowed_changes,
        },
        "pair_utilization": pair_records,
        "nets": net_records,
        "metrics": {
            "routed_cut_nets": len(net_records),
            "used_fpga_pairs": len(pair_records),
            "max_tdm_ratio": max(
                (record["tdm_ratio"] for record in pair_records), default=0
            ),
            "worst_path_delay_ns": worst_delay,
            "runtime_seconds": float(runtime_seconds),
            "contest_score": score,
        },
    }
    if official_output_dir is not None:
        _write_official_solution(
            official_output_dir,
            instance,
            route_by_net,
            pair_delays,
            topology,
        )
    if output_path is not None:
        write_json(output_path, result)
    return result
