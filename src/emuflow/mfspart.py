"""Source-complete MFSPart-style affinity coarsening and hierarchy audit.

The native implementation is an independent paper-level reproduction.  This
module deliberately stays outside the performance path: it adapts versioned
records and independently checks every hierarchy transformation.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .native_tools import resolve_native_executable


MFSPART_INPUT_SCHEMA = "emuflow.mfspart-coarsener-input/v2"
MFSPART_HIERARCHY_SCHEMA = "emuflow.mfspart-hierarchy/v2"
MFSPART_PROVIDER = "mfspart-paper-margin-coarsening-v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _splitmix64(value: int) -> int:
    mask = (1 << 64) - 1
    value = (value + 0x9E3779B97F4A7C15) & mask
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return (value ^ (value >> 31)) & mask


def _tie_key(seed: int, level: int, left: int, right: int) -> int:
    value = seed & ((1 << 64) - 1)
    value ^= _splitmix64(level + 1)
    value ^= _splitmix64(((left + 1) << 32) | (right + 1))
    return _splitmix64(value)


def _normalise_input(
    nodes: Sequence[Mapping[str, Any]],
    nets: Sequence[Mapping[str, Any]],
    dimensions: Sequence[str],
    coarse_bounds: Mapping[str, int],
    *,
    stop_delta: int,
    max_levels: int,
    seed: int,
    fixed_part_distances: Optional[Sequence[Sequence[int]]] = None,
    fixed_radius: int = 1,
    fixed_margin: int = 3,
) -> Dict[str, Any]:
    if not nodes:
        raise ValidationError("MFSPart coarsening requires at least one node")
    if not dimensions or len(set(dimensions)) != len(dimensions):
        raise ValidationError("MFSPart dimensions must be non-empty and unique")
    if stop_delta < 0 or max_levels <= 0 or seed < 0 or seed >= 1 << 64:
        raise ValidationError("invalid MFSPart stopping parameters or seed")
    bounds = []
    for dimension in dimensions:
        bound = coarse_bounds.get(dimension)
        if not isinstance(bound, int) or isinstance(bound, bool) or bound <= 0:
            raise ValidationError(f"invalid coarse bound for {dimension!r}")
        bounds.append(bound)

    node_order: List[str] = []
    normal_nodes = []
    for raw in nodes:
        node_id = raw.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in node_order:
            raise ValidationError("MFSPart node ids must be non-empty and unique")
        raw_weights = raw.get("weights")
        if not isinstance(raw_weights, Mapping):
            raise ValidationError(f"node {node_id!r} has no resource weights")
        weights = []
        for dimension in dimensions:
            weight = raw_weights.get(dimension)
            if (
                not isinstance(weight, int)
                or isinstance(weight, bool)
                or weight < 0
                or (dimension == dimensions[0] and weight == 0)
            ):
                raise ValidationError(
                    f"node {node_id!r} has invalid {dimension!r} weight"
                )
            weights.append(weight)
        fixed_part = raw.get("fixed_part", -1)
        if (
            not isinstance(fixed_part, int)
            or isinstance(fixed_part, bool)
            or fixed_part < -1
        ):
            raise ValidationError(f"node {node_id!r} has invalid fixed_part")
        node_order.append(node_id)
        normal_nodes.append(
            {
                "fixed_part": fixed_part,
                "protected_radius": False,
                "weights": weights,
            }
        )

    node_index = {node_id: index for index, node_id in enumerate(node_order)}
    normal_nets = []
    net_ids = set()
    for raw in nets:
        net_id = raw.get("id")
        if not isinstance(net_id, str) or not net_id or net_id in net_ids:
            raise ValidationError("MFSPart net ids must be non-empty and unique")
        net_ids.add(net_id)
        source = raw.get("source")
        sinks = raw.get("sinks")
        weight = raw.get("weight", 1.0)
        if source not in node_index:
            raise ValidationError(f"net {net_id!r} has unknown source")
        if not isinstance(sinks, Sequence) or isinstance(sinks, (str, bytes)):
            raise ValidationError(f"net {net_id!r} has invalid sinks")
        if not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            raise ValidationError(f"net {net_id!r} has invalid weight")
        sink_indices = []
        for sink in sinks:
            if sink not in node_index or sink == source:
                raise ValidationError(f"net {net_id!r} has invalid sink")
            sink_indices.append(node_index[sink])
        if not sink_indices or len(set(sink_indices)) != len(sink_indices):
            raise ValidationError(f"net {net_id!r} has empty or duplicate sinks")
        normal_nets.append(
            {
                "weight": float(weight),
                "source": node_index[source],
                "sinks": sorted(sink_indices),
            }
        )
    mode = {"kind": "affinity"}
    if fixed_part_distances is not None:
        matrix = [list(row) for row in fixed_part_distances]
        part_count = len(matrix)
        if part_count == 0 or fixed_radius < 0 or fixed_margin < 0:
            raise ValidationError("invalid MFSPart margin coarsening parameters")
        if any(len(row) != part_count for row in matrix):
            raise ValidationError("MFSPart fixed-part distance matrix is not square")
        for left in range(part_count):
            for right in range(part_count):
                distance = matrix[left][right]
                if not isinstance(distance, int) or isinstance(distance, bool) or distance < 0 or distance != matrix[right][left]:
                    raise ValidationError("invalid MFSPart fixed-part distance matrix")
            if matrix[left][left] != 0:
                raise ValidationError("MFSPart fixed-part self-distance must be zero")
        if any(node["fixed_part"] >= part_count for node in normal_nodes):
            raise ValidationError("MFSPart fixed node references unknown part")
        adjacency = [[] for _ in normal_nodes]
        for net in normal_nets:
            for sink in net["sinks"]:
                adjacency[net["source"]].append(sink)
                adjacency[sink].append(net["source"])
        distance = [-1] * len(normal_nodes)
        queue = deque()
        for node, record in enumerate(normal_nodes):
            if record["fixed_part"] >= 0:
                distance[node] = 0
                queue.append(node)
        while queue:
            node = queue.popleft()
            if distance[node] >= fixed_radius:
                continue
            for neighbor in adjacency[node]:
                if distance[neighbor] < 0:
                    distance[neighbor] = distance[node] + 1
                    queue.append(neighbor)
        for node, value in enumerate(distance):
            normal_nodes[node]["protected_radius"] = 0 <= value <= fixed_radius
        mode = {
            "kind": "margin",
            "part_distances": matrix,
            "fixed_radius": fixed_radius,
            "fixed_margin": fixed_margin,
        }
    return {
        "schema": MFSPART_INPUT_SCHEMA,
        "algorithm": MFSPART_PROVIDER,
        "dimensions": list(dimensions),
        "bounds": bounds,
        "stop_delta": stop_delta,
        "max_levels": max_levels,
        "seed": seed,
        "mode": mode,
        "node_order": node_order,
        "nodes": normal_nodes,
        "nets": normal_nets,
    }


def _write_native_input(path: Path, value: Mapping[str, Any]) -> None:
    lines = [
        "EMUFLOW_MFSPART_COARSENER_INPUT_V2",
        "PARAM "
        + " ".join(
            str(item)
            for item in (
                len(value["nodes"]),
                len(value["dimensions"]),
                len(value["nets"]),
                value["stop_delta"],
                value["max_levels"],
                value["seed"],
            )
        ),
    ]
    if value["mode"]["kind"] == "margin":
        matrix = value["mode"]["part_distances"]
        lines.append(
            f"MODE M {len(matrix)} {value['mode']['fixed_radius']} "
            f"{value['mode']['fixed_margin']}"
        )
        for source, row in enumerate(matrix):
            for target, distance in enumerate(row):
                lines.append(f"DIST {source} {target} {distance}")
    else:
        lines.append("MODE A")
    lines.extend(
        f"BOUND {index} {bound}"
        for index, bound in enumerate(value["bounds"])
    )
    for index, node in enumerate(value["nodes"]):
        lines.append(
            "NODE "
            + " ".join(
                str(item)
                for item in (index, node["fixed_part"], *node["weights"])
            )
        )
    for index, net in enumerate(value["nets"]):
        lines.append(
            "NET "
            + " ".join(
                str(item)
                for item in (
                    index,
                    format(net["weight"], ".17g"),
                    net["source"],
                    len(net["sinks"]),
                    *net["sinks"],
                )
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_native_output(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise EmuFlowError("MFSPart coarsener produced no output")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_MFSPART_COARSENER_OUTPUT_V2":
        raise ValidationError("invalid MFSPart coarsener output header")
    parameter = None
    levels: Dict[int, Dict[str, Any]] = {}
    maps: Dict[int, Dict[int, int]] = defaultdict(dict)
    merges: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    fixed_merges: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    mode = None
    metrics: Dict[str, int] = {}
    for line in lines[1:]:
        fields = line.split()
        if not fields:
            continue
        try:
            if fields[0] == "PARAM" and len(fields) == 4:
                if parameter is not None:
                    raise ValidationError("duplicate MFSPart output PARAM")
                parameter = tuple(map(int, fields[1:]))
            elif fields[0] == "MODE" and fields[1:] == ["A"]:
                if mode is not None:
                    raise ValidationError("duplicate MFSPart output MODE")
                mode = {"kind": "affinity"}
            elif fields[0] == "MODE" and len(fields) == 5 and fields[1] == "M":
                if mode is not None:
                    raise ValidationError("duplicate MFSPart output MODE")
                parts, radius, margin = map(int, fields[2:])
                mode = {
                    "kind": "margin",
                    "parts": parts,
                    "fixed_radius": radius,
                    "fixed_margin": margin,
                }
            elif fields[0] == "LEVEL" and len(fields) == 4:
                level, node_count, net_count = map(int, fields[1:])
                if level in levels:
                    raise ValidationError("duplicate MFSPart output LEVEL")
                levels[level] = {
                    "nodes": [None] * node_count,
                    "nets": [None] * net_count,
                }
            elif fields[0] == "NODE" and len(fields) >= 6:
                level, index, fixed_part, protected = map(int, fields[1:5])
                if level not in levels or index not in range(len(levels[level]["nodes"])):
                    raise ValidationError("invalid MFSPart output NODE index")
                if levels[level]["nodes"][index] is not None:
                    raise ValidationError("duplicate MFSPart output NODE")
                levels[level]["nodes"][index] = {
                    "fixed_part": fixed_part,
                    "protected_radius": bool(protected),
                    "weights": list(map(int, fields[5:])),
                }
            elif fields[0] == "NET" and len(fields) >= 7:
                level, index = map(int, fields[1:3])
                weight = float(fields[3])
                source, sink_count = map(int, fields[4:6])
                sinks = list(map(int, fields[6:]))
                if sink_count != len(sinks) or level not in levels or index not in range(len(levels[level]["nets"])):
                    raise ValidationError("invalid MFSPart output NET index")
                if levels[level]["nets"][index] is not None:
                    raise ValidationError("duplicate MFSPart output NET")
                levels[level]["nets"][index] = {
                    "weight": weight,
                    "source": source,
                    "sinks": sinks,
                }
            elif fields[0] == "MAP" and len(fields) == 4:
                level, fine, coarse = map(int, fields[1:])
                if fine in maps[level]:
                    raise ValidationError("duplicate MFSPart output MAP")
                maps[level][fine] = coarse
            elif fields[0] == "MERGE" and len(fields) == 6:
                level, coarse, left, right = map(int, fields[1:5])
                merges[level].append(
                    {
                        "coarse": coarse,
                        "left": left,
                        "right": right,
                        "affinity": float(fields[5]),
                    }
                )
            elif fields[0] == "FIXED_MERGE" and len(fields) >= 5:
                level, coarse, count = map(int, fields[1:4])
                members = list(map(int, fields[4:]))
                if count != len(members):
                    raise ValidationError("invalid MFSPart fixed merge")
                fixed_merges[level].append(
                    {"coarse": coarse, "members": members}
                )
            elif fields[0] == "METRIC" and len(fields) == 3:
                if fields[1] in metrics:
                    raise ValidationError("duplicate MFSPart output METRIC")
                metrics[fields[1]] = int(fields[2])
            else:
                raise ValidationError(f"invalid MFSPart output record {line!r}")
        except ValueError as error:
            raise ValidationError(f"malformed MFSPart output record {line!r}") from error
    if parameter is None or mode is None:
        raise ValidationError("MFSPart output has no PARAM or MODE")
    level_count, dimensions, seed = parameter
    if set(levels) != set(range(level_count)):
        raise ValidationError("MFSPart output level coverage mismatch")
    for level, graph in levels.items():
        if any(node is None for node in graph["nodes"]) or any(net is None for net in graph["nets"]):
            raise ValidationError(f"MFSPart output level {level} is incomplete")
        for node in graph["nodes"]:
            if len(node["weights"]) != dimensions:
                raise ValidationError("MFSPart output weight dimension mismatch")
    return {
        "level_count": level_count,
        "dimensions": dimensions,
        "seed": seed,
        "mode": mode,
        "levels": [levels[index] for index in range(level_count)],
        "maps": [maps[index] for index in range(max(0, level_count - 1))],
        "merges": [merges[index] for index in range(max(0, level_count - 1))],
        "fixed_merges": [
            fixed_merges[index] for index in range(max(0, level_count - 1))
        ],
        "metrics": metrics,
    }


def _pair_affinities(graph: Mapping[str, Any]) -> Dict[Tuple[int, int], float]:
    pair_weights: Dict[Tuple[int, int], float] = defaultdict(float)
    for net in graph["nets"]:
        for sink in net["sinks"]:
            pair = tuple(sorted((net["source"], sink)))
            pair_weights[pair] += net["weight"]
    result = {}
    for pair, weight in pair_weights.items():
        denominator = graph["nodes"][pair[0]]["weights"][0] * graph["nodes"][pair[1]]["weights"][0]
        result[pair] = weight / denominator
    return result


def _expected_map(
    graph: Mapping[str, Any],
    bounds: Sequence[int],
    seed: int,
    level: int,
    mode: Mapping[str, Any],
) -> Tuple[
    Dict[int, int],
    List[Tuple[int, int, float]],
    List[Dict[str, Any]],
    Dict[str, int],
]:
    if mode["kind"] == "margin":
        by_part: Dict[int, List[int]] = defaultdict(list)
        for node, record in enumerate(graph["nodes"]):
            if record["fixed_part"] >= 0:
                by_part[record["fixed_part"]].append(node)
        if any(len(members) > 1 for members in by_part.values()):
            mapping = {}
            fixed_merges = []
            next_coarse = 0
            for node, record in enumerate(graph["nodes"]):
                if node in mapping:
                    continue
                members = by_part.get(record["fixed_part"], [])
                if record["fixed_part"] >= 0 and len(members) > 1:
                    for member in members:
                        mapping[member] = next_coarse
                    fixed_merges.append(
                        {"coarse": next_coarse, "members": list(members)}
                    )
                else:
                    mapping[node] = next_coarse
                next_coarse += 1
            return mapping, [], fixed_merges, {
                "rejected_protected": 0,
                "rejected_margin": 0,
                "rejected_bound": 0,
                "rejected_fixed": 0,
                "margin_repair_rounds": 0,
                "margin_distance_searches": 0,
            }

    affinities = _pair_affinities(graph)
    candidates = []
    rejections = {
        "rejected_protected": 0,
        "rejected_margin": 0,
        "rejected_bound": 0,
        "rejected_fixed": 0,
        "margin_repair_rounds": 0,
        "margin_distance_searches": 0,
    }
    anchor_distances = {}
    if mode["kind"] == "margin":
        adjacency = [[] for _ in graph["nodes"]]
        for net in graph["nets"]:
            for sink in net["sinks"]:
                adjacency[net["source"]].append(sink)
                adjacency[sink].append(net["source"])
        for anchor, record in enumerate(graph["nodes"]):
            if record["fixed_part"] < 0:
                continue
            distances = [-1] * len(graph["nodes"])
            distances[anchor] = 0
            queue = deque([anchor])
            while queue:
                node = queue.popleft()
                for neighbor in adjacency[node]:
                    if distances[neighbor] < 0:
                        distances[neighbor] = distances[node] + 1
                        queue.append(neighbor)
            anchor_distances[anchor] = distances

    def margin_allows(left: int, right: int) -> bool:
        if mode["kind"] != "margin":
            return True
        anchors = sorted(anchor_distances)
        infinity = 1 << 60
        for first_index, first in enumerate(anchors):
            for second in anchors[first_index + 1 :]:
                first_part = graph["nodes"][first]["fixed_part"]
                second_part = graph["nodes"][second]["fixed_part"]
                if first_part == second_part:
                    continue
                required = (
                    mode["part_distances"][first_part][second_part]
                    + mode["fixed_margin"]
                )
                direct = anchor_distances[first][second]
                minimum_allowed = required if direct < 0 else min(direct, required)
                contracted = direct if direct >= 0 else infinity
                for first_side, second_side in ((left, right), (right, left)):
                    first_distance = anchor_distances[first][first_side]
                    second_distance = anchor_distances[second][second_side]
                    if first_distance >= 0 and second_distance >= 0:
                        contracted = min(
                            contracted, first_distance + second_distance
                        )
                if contracted < minimum_allowed:
                    return False
        return True

    for (left, right), affinity in affinities.items():
        left_node, right_node = graph["nodes"][left], graph["nodes"][right]
        if mode["kind"] == "margin" and (
            left_node.get("protected_radius", False)
            or right_node.get("protected_radius", False)
        ):
            rejections["rejected_protected"] += 1
            continue
        fixed_compatible = (
            left_node["fixed_part"] < 0
            or right_node["fixed_part"] < 0
            or left_node["fixed_part"] == right_node["fixed_part"]
        )
        if not fixed_compatible:
            rejections["rejected_fixed"] += 1
            continue
        fits = all(
            left_node["weights"][dimension] + right_node["weights"][dimension]
            <= bounds[dimension]
            for dimension in range(len(bounds))
        )
        if not fits:
            rejections["rejected_bound"] += 1
            continue
        if not margin_allows(left, right):
            rejections["rejected_margin"] += 1
            continue
        candidates.append(
            (-affinity, _tie_key(seed, level, left, right), left, right)
        )
    candidates.sort()
    matched = set()
    selected_candidates = []
    for negative_affinity, tie, left, right in candidates:
        if left in matched or right in matched:
            continue
        matched.update((left, right))
        selected_candidates.append((left, right, -negative_affinity, tie))

    active = [True] * len(selected_candidates)
    anchors = sorted(anchor_distances)
    has_distinct_anchors = any(
        graph["nodes"][first]["fixed_part"]
        != graph["nodes"][second]["fixed_part"]
        for first_index, first in enumerate(anchors)
        for second in anchors[first_index + 1 :]
    )
    while mode["kind"] == "margin" and has_distinct_anchors:
        weighted_adjacency = [[] for _ in graph["nodes"]]
        for net in graph["nets"]:
            for sink in net["sinks"]:
                weighted_adjacency[net["source"]].append((sink, 1, -1))
                weighted_adjacency[sink].append((net["source"], 1, -1))
        for merge, (left, right, _, _) in enumerate(selected_candidates):
            if active[merge]:
                weighted_adjacency[left].append((right, 0, merge))
                weighted_adjacency[right].append((left, 0, merge))
        remove = set()
        infinity = 1 << 60
        for first_index, first in enumerate(anchors):
            if not any(
                graph["nodes"][first]["fixed_part"]
                != graph["nodes"][second]["fixed_part"]
                for second in anchors[first_index + 1 :]
            ):
                continue
            distance = [infinity] * len(graph["nodes"])
            parent = [-1] * len(graph["nodes"])
            parent_merge = [-1] * len(graph["nodes"])
            distance[first] = 0
            queue = deque([first])
            while queue:
                node = queue.popleft()
                for target, cost, merge in weighted_adjacency[node]:
                    candidate_distance = distance[node] + cost
                    if candidate_distance < distance[target] or (
                        candidate_distance == distance[target]
                        and (node, merge) < (parent[target], parent_merge[target])
                    ):
                        distance[target] = candidate_distance
                        parent[target] = node
                        parent_merge[target] = merge
                        if cost == 0:
                            queue.appendleft(target)
                        else:
                            queue.append(target)
            rejections["margin_distance_searches"] += 1
            first_part = graph["nodes"][first]["fixed_part"]
            for second in anchors[first_index + 1 :]:
                second_part = graph["nodes"][second]["fixed_part"]
                if first_part == second_part or distance[second] == infinity:
                    continue
                required = (
                    mode["part_distances"][first_part][second_part]
                    + mode["fixed_margin"]
                )
                baseline = anchor_distances[first][second]
                minimum_allowed = (
                    required if baseline < 0 else min(baseline, required)
                )
                deficit = minimum_allowed - distance[second]
                if deficit <= 0:
                    continue
                path_merges = []
                node = second
                while node != first and parent[node] >= 0:
                    if parent_merge[node] >= 0:
                        path_merges.append(parent_merge[node])
                    node = parent[node]
                path_merges = sorted(
                    set(path_merges),
                    key=lambda merge: (
                        selected_candidates[merge][2],
                        -selected_candidates[merge][3],
                        -merge,
                    ),
                )
                if len(path_merges) < deficit:
                    raise ValidationError(
                        "margin repair cannot identify enough merges"
                    )
                remove.update(path_merges[:deficit])
        if not remove:
            break
        for merge in remove:
            active[merge] = False
        rejections["rejected_margin"] += len(remove)
        rejections["margin_repair_rounds"] += 1

    selected = [
        (left, right, affinity)
        for keep, (left, right, affinity, _) in zip(active, selected_candidates)
        if keep
    ]
    mapping: Dict[int, int] = {}
    for coarse, (left, right, _) in enumerate(selected):
        mapping[left] = coarse
        mapping[right] = coarse
    next_coarse = len(selected)
    for node in range(len(graph["nodes"])):
        if node not in mapping:
            mapping[node] = next_coarse
            next_coarse += 1
    return mapping, selected, [], rejections


def _transform_graph(graph: Mapping[str, Any], mapping: Mapping[int, int]) -> Dict[str, Any]:
    coarse_count = max(mapping.values()) + 1
    dimensions = len(graph["nodes"][0]["weights"])
    nodes = [
        {
            "fixed_part": -1,
            "protected_radius": False,
            "weights": [0] * dimensions,
        }
        for _ in range(coarse_count)
    ]
    for fine, coarse in mapping.items():
        node = graph["nodes"][fine]
        for dimension, weight in enumerate(node["weights"]):
            nodes[coarse]["weights"][dimension] += weight
        if node["fixed_part"] >= 0:
            if nodes[coarse]["fixed_part"] not in (-1, node["fixed_part"]):
                raise ValidationError("coarse node combines incompatible fixed parts")
            nodes[coarse]["fixed_part"] = node["fixed_part"]
        nodes[coarse]["protected_radius"] = (
            nodes[coarse]["protected_radius"]
            or node.get("protected_radius", False)
        )
    aggregated: Dict[Tuple[int, Tuple[int, ...]], float] = defaultdict(float)
    for net in graph["nets"]:
        source = mapping[net["source"]]
        sinks = tuple(sorted({mapping[sink] for sink in net["sinks"] if mapping[sink] != source}))
        if sinks:
            aggregated[(source, sinks)] += net["weight"]
    nets = [
        {"weight": weight, "source": source, "sinks": list(sinks)}
        for (source, sinks), weight in sorted(aggregated.items())
    ]
    return {"nodes": nodes, "nets": nets}


def validate_mfspart_hierarchy(
    artifact: Mapping[str, Any], native_input: Mapping[str, Any]
) -> Dict[str, Any]:
    if artifact.get("schema") != MFSPART_HIERARCHY_SCHEMA:
        raise ValidationError("invalid MFSPart hierarchy schema")
    if artifact.get("seed") != native_input["seed"]:
        raise ValidationError("MFSPart hierarchy seed mismatch")
    levels = artifact.get("levels")
    mappings = artifact.get("fine_to_coarse")
    merges = artifact.get("merges")
    fixed_merges = artifact.get("fixed_merges")
    if not isinstance(levels, list) or not levels:
        raise ValidationError("MFSPart hierarchy has no levels")
    if (
        len(mappings) != len(levels) - 1
        or len(merges) != len(mappings)
        or len(fixed_merges) != len(mappings)
    ):
        raise ValidationError("MFSPart hierarchy depth mismatch")
    if levels[0] != {"nodes": native_input["nodes"], "nets": native_input["nets"]}:
        raise ValidationError("MFSPart hierarchy input graph mismatch")
    rejection_totals = {
        "rejected_protected": 0,
        "rejected_margin": 0,
        "rejected_bound": 0,
        "rejected_fixed": 0,
        "margin_repair_rounds": 0,
        "margin_distance_searches": 0,
    }
    for level, mapping in enumerate(mappings):
        if set(mapping) != set(range(len(levels[level]["nodes"]))):
            raise ValidationError("MFSPart hierarchy map coverage mismatch")
        expected_map, selected, expected_fixed, rejections = _expected_map(
            levels[level],
            native_input["bounds"],
            native_input["seed"],
            level,
            native_input["mode"],
        )
        for name, value in rejections.items():
            rejection_totals[name] += value
        if mapping != expected_map:
            raise ValidationError("MFSPart hierarchy greedy matching mismatch")
        expected_graph = _transform_graph(levels[level], mapping)
        actual_graph = levels[level + 1]
        if expected_graph["nodes"] != actual_graph["nodes"]:
            raise ValidationError("MFSPart hierarchy node aggregation mismatch")
        if len(expected_graph["nets"]) != len(actual_graph["nets"]):
            raise ValidationError("MFSPart hierarchy net aggregation mismatch")
        for expected, actual in zip(expected_graph["nets"], actual_graph["nets"]):
            if expected["source"] != actual["source"] or expected["sinks"] != actual["sinks"] or not math.isclose(expected["weight"], actual["weight"], rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("MFSPart hierarchy net aggregation mismatch")
        actual_merges = merges[level]
        if fixed_merges[level] != expected_fixed:
            raise ValidationError("MFSPart fixed-anchor premerge mismatch")
        if len(actual_merges) != len(selected):
            raise ValidationError("MFSPart hierarchy merge count mismatch")
        for coarse, ((left, right, affinity), actual) in enumerate(zip(selected, actual_merges)):
            if (actual["coarse"], actual["left"], actual["right"]) != (coarse, left, right) or not math.isclose(actual["affinity"], affinity, rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("MFSPart hierarchy merge record mismatch")
    for name, expected in rejection_totals.items():
        if artifact.get("native_metrics", {}).get(name) != expected:
            raise ValidationError(f"MFSPart rejection metric mismatch for {name}")
    return {
        "status": "pass",
        "levels": len(levels) - 1,
        "original_nodes": len(levels[0]["nodes"]),
        "coarsest_nodes": len(levels[-1]["nodes"]),
    }


def build_mfspart_hierarchy(
    nodes: Sequence[Mapping[str, Any]],
    nets: Sequence[Mapping[str, Any]],
    dimensions: Sequence[str],
    coarse_bounds: Mapping[str, int],
    output_dir: Path,
    *,
    stop_delta: int = 0,
    max_levels: int = 32,
    seed: int = 0,
    fixed_part_distances: Optional[Sequence[Sequence[int]]] = None,
    fixed_radius: int = 1,
    fixed_margin: int = 3,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    native_input = _normalise_input(
        nodes,
        nets,
        dimensions,
        coarse_bounds,
        stop_delta=stop_delta,
        max_levels=max_levels,
        seed=seed,
        fixed_part_distances=fixed_part_distances,
        fixed_radius=fixed_radius,
        fixed_margin=fixed_margin,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "mfspart_coarsener.in"
    output_path = output_dir / "mfspart_coarsener.out"
    log_path = output_dir / "mfspart_coarsener.log"
    _write_native_input(input_path, native_input)
    command = resolve_native_executable("emuflow_mfspart_coarsener", executable)
    completed = subprocess.run(
        [command, str(input_path.resolve()), str(output_path.resolve())],
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise EmuFlowError(
            "MFSPart coarsener failed with exit code "
            f"{completed.returncode}: {completed.stdout[-2000:]}"
        )
    parsed = _parse_native_output(output_path)
    expected_mode = native_input["mode"]
    if parsed["mode"]["kind"] != expected_mode["kind"]:
        raise ValidationError("MFSPart native mode mismatch")
    if expected_mode["kind"] == "margin" and (
        parsed["mode"]["parts"] != len(expected_mode["part_distances"])
        or parsed["mode"]["fixed_radius"] != expected_mode["fixed_radius"]
        or parsed["mode"]["fixed_margin"] != expected_mode["fixed_margin"]
    ):
        raise ValidationError("MFSPart native margin parameters mismatch")
    artifact = {
        "schema": MFSPART_HIERARCHY_SCHEMA,
        "provider": MFSPART_PROVIDER,
        "claim_scope": (
            "independent paper-level affinity and fixed-node margin "
            "coarsening reproduction"
        ),
        "dimensions": list(dimensions),
        "bounds": dict(coarse_bounds),
        "seed": parsed["seed"],
        "mode": native_input["mode"],
        "node_order": native_input["node_order"],
        "levels": parsed["levels"],
        "fine_to_coarse": parsed["maps"],
        "merges": parsed["merges"],
        "fixed_merges": parsed["fixed_merges"],
        "native_metrics": parsed["metrics"],
        "artifacts": {
            "input_sha256": _sha256(input_path),
            "output_sha256": _sha256(output_path),
        },
    }
    artifact["validation"] = validate_mfspart_hierarchy(artifact, native_input)
    return artifact
