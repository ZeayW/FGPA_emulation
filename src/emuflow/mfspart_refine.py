"""MFSPart direct k-way FM uncoarsening and independent replay oracle."""

from __future__ import annotations

import hashlib
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .errors import EmuFlowError, ValidationError
from .mfspart import MFSPART_HIERARCHY_SCHEMA
from .mfspart_initial import MFSPART_INITIAL_SCHEMA, _partition_metrics
from .native_tools import resolve_native_executable


MFSPART_REFINER_INPUT_SCHEMA = "emuflow.mfspart-refiner-input/v1"
MFSPART_REFINEMENT_SCHEMA = "emuflow.mfspart-refinement/v1"
MFSPART_REFINER_PROVIDER = "mfspart-paper-direct-kway-fm-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise_refinement(
    graph: Mapping[str, Any],
    dimensions: Sequence[str],
    parts: Sequence[str],
    distances: Mapping[str, Mapping[str, int]],
    capacities: Mapping[str, Mapping[str, int]],
    assignment: Union[Mapping[int, int], Sequence[int]],
    *,
    hmax: int,
    move_distance: int,
    early_stop: int,
    gamma: float,
    violation_lambda: float,
    mu: float,
) -> Dict[str, Any]:
    if not parts or len(set(parts)) != len(parts):
        raise ValidationError("MFSPart refiner FPGA ids must be unique")
    if not dimensions or len(set(dimensions)) != len(dimensions):
        raise ValidationError("MFSPart refiner dimensions must be unique")
    if hmax < 1 or move_distance < 1 or early_stop < 1:
        raise ValidationError("invalid MFSPart FM distance or early-stop limit")
    if any(
        not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in (gamma, violation_lambda, mu)
    ):
        raise ValidationError("invalid MFSPart FM score parameter")
    nodes = graph.get("nodes")
    nets = graph.get("nets")
    if not isinstance(nodes, list) or not nodes or not isinstance(nets, list):
        raise ValidationError("invalid MFSPart FM graph")
    if isinstance(assignment, Mapping):
        assigned = [assignment.get(index) for index in range(len(nodes))]
    else:
        assigned = list(assignment)
    if len(assigned) != len(nodes) or any(
        not isinstance(part, int)
        or isinstance(part, bool)
        or part < 0
        or part >= len(parts)
        for part in assigned
    ):
        raise ValidationError("invalid MFSPart FM initial assignment")
    distance_matrix = []
    capacity_matrix = []
    for source in parts:
        row = []
        for target in parts:
            distance = distances.get(source, {}).get(target)
            if not isinstance(distance, int) or isinstance(distance, bool) or distance < 0:
                raise ValidationError("incomplete MFSPart FM distance matrix")
            row.append(distance)
        distance_matrix.append(row)
        capacity_row = []
        for dimension in dimensions:
            capacity = capacities.get(source, {}).get(dimension)
            if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
                raise ValidationError("incomplete MFSPart FM capacity matrix")
            capacity_row.append(capacity)
        capacity_matrix.append(capacity_row)
    for left in range(len(parts)):
        if distance_matrix[left][left] != 0:
            raise ValidationError("MFSPart FM self-distance must be zero")
        for right in range(len(parts)):
            if distance_matrix[left][right] != distance_matrix[right][left]:
                raise ValidationError("paper-mode FPGA distances must be symmetric")
    for node in nodes:
        if len(node["weights"]) != len(dimensions):
            raise ValidationError("MFSPart FM node weight dimension mismatch")
    return {
        "schema": MFSPART_REFINER_INPUT_SCHEMA,
        "provider": MFSPART_REFINER_PROVIDER,
        "graph": graph,
        "dimensions": list(dimensions),
        "parts": list(parts),
        "distances": distance_matrix,
        "capacities": capacity_matrix,
        "assignment": assigned,
        "hmax": hmax,
        "move_distance": move_distance,
        "early_stop": early_stop,
        "gamma": float(gamma),
        "lambda": float(violation_lambda),
        "mu": float(mu),
    }


def _write_native_input(path: Path, problem: Mapping[str, Any]) -> None:
    graph = problem["graph"]
    lines = [
        "EMUFLOW_MFSPART_REFINER_INPUT_V1",
        "PARAM "
        + " ".join(
            str(value)
            for value in (
                len(problem["parts"]),
                len(graph["nodes"]),
                len(problem["dimensions"]),
                len(graph["nets"]),
                problem["hmax"],
                problem["move_distance"],
                problem["early_stop"],
                format(problem["gamma"], ".17g"),
                format(problem["lambda"], ".17g"),
                format(problem["mu"], ".17g"),
            )
        ),
    ]
    for source, row in enumerate(problem["distances"]):
        for target, distance in enumerate(row):
            lines.append(f"DIST {source} {target} {distance}")
    for part, row in enumerate(problem["capacities"]):
        for dimension, capacity in enumerate(row):
            lines.append(f"CAP {part} {dimension} {capacity}")
    for index, node in enumerate(graph["nodes"]):
        lines.append(
            "NODE "
            + " ".join(
                str(value)
                for value in (index, node["fixed_part"], *node["weights"])
            )
        )
    for index, net in enumerate(graph["nets"]):
        lines.append(
            "NET "
            + " ".join(
                str(value)
                for value in (
                    index,
                    format(net["weight"], ".17g"),
                    net["source"],
                    len(net["sinks"]),
                    *net["sinks"],
                )
            )
        )
    lines.extend(
        f"ASSIGN {node} {part}"
        for node, part in enumerate(problem["assignment"])
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_output(path: Path, node_count: int) -> Dict[str, Any]:
    if not path.is_file():
        raise EmuFlowError("MFSPart refiner produced no output")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_MFSPART_REFINER_OUTPUT_V1":
        raise ValidationError("invalid MFSPart refiner output header")
    status = None
    moves = []
    final: Dict[int, int] = {}
    metrics: Dict[str, float] = {}
    for line in lines[1:]:
        fields = line.split()
        try:
            if fields[0] == "STATUS" and len(fields) == 2:
                if status is not None:
                    raise ValidationError("duplicate MFSPart refiner status")
                status = fields[1]
            elif fields[0] == "MOVE" and len(fields) == 8:
                index, node, source, target = map(int, fields[1:5])
                if index != len(moves):
                    raise ValidationError("MFSPart move sequence mismatch")
                moves.append(
                    {
                        "node": node,
                        "source": source,
                        "target": target,
                        "gain": float(fields[5]),
                        "cumulative_gain": float(fields[6]),
                        "kept": bool(int(fields[7])),
                    }
                )
            elif fields[0] == "FINAL" and len(fields) == 3:
                node, part = map(int, fields[1:])
                if node in final:
                    raise ValidationError("duplicate MFSPart final assignment")
                final[node] = part
            elif fields[0] == "METRIC" and len(fields) == 3:
                if fields[1] in metrics:
                    raise ValidationError("duplicate MFSPart refiner metric")
                metrics[fields[1]] = float(fields[2])
            else:
                raise ValidationError(f"invalid MFSPart refiner output record {line!r}")
        except (ValueError, IndexError) as error:
            raise ValidationError(f"malformed MFSPart refiner output record {line!r}") from error
    if status != "PASS" or set(final) != set(range(node_count)):
        raise ValidationError("incomplete MFSPart refiner output")
    return {
        "moves": moves,
        "assignment": [final[index] for index in range(node_count)],
        "metrics": metrics,
    }


def _adjacency_and_incidence(problem: Mapping[str, Any]):
    adjacency = [[] for _ in problem["graph"]["nodes"]]
    incidence = [[] for _ in problem["graph"]["nodes"]]
    for net_index, net in enumerate(problem["graph"]["nets"]):
        incidence[net["source"]].append(net_index)
        for sink in net["sinks"]:
            adjacency[net["source"]].append((sink, net["weight"]))
            adjacency[sink].append((net["source"], net["weight"]))
            incidence[sink].append(net_index)
    return adjacency, incidence


def _loads(problem, assignment):
    loads = [[0] * len(problem["dimensions"]) for _ in problem["parts"]]
    for node, part in enumerate(assignment):
        for dimension, weight in enumerate(problem["graph"]["nodes"][node]["weights"]):
            loads[part][dimension] += weight
    return loads


def _fits(problem, loads, node: int, target: int) -> bool:
    return all(
        loads[target][dimension] + weight
        <= problem["capacities"][target][dimension]
        for dimension, weight in enumerate(problem["graph"]["nodes"][node]["weights"])
    )


def _compatibility(problem, adjacency, incidence, assignment, node: int, candidate: int) -> float:
    hop_score = 0.0
    violation = 0.0
    for neighbor, weight in adjacency[node]:
        distance = problem["distances"][assignment[neighbor]][candidate]
        if distance <= problem["hmax"]:
            hop_score += (problem["hmax"] - distance) * weight
        else:
            violation += weight * (1.0 + problem["mu"] * (distance - problem["hmax"]))
    connectivity = 0.0
    for net_index in incidence[node]:
        net = problem["graph"]["nets"][net_index]
        spanned = {candidate if net["source"] == node else assignment[net["source"]]}
        spanned.update(candidate if sink == node else assignment[sink] for sink in net["sinks"])
        connectivity += net["weight"] * len(spanned)
    return hop_score - problem["gamma"] * connectivity - problem["lambda"] * violation


def _replay(problem: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[int], Dict[str, float]]:
    adjacency, incidence = _adjacency_and_incidence(problem)
    assignment = list(problem["assignment"])
    loads = _loads(problem, assignment)
    locked = [False] * len(assignment)
    moves = []
    cumulative = 0.0
    best_cumulative = 0.0
    best_prefix = 0
    ineffective = 0
    while ineffective < problem["early_stop"]:
        choices = []
        for node, source in enumerate(assignment):
            if locked[node] or problem["graph"]["nodes"][node]["fixed_part"] >= 0:
                continue
            source_score = _compatibility(problem, adjacency, incidence, assignment, node, source)
            for target in range(len(problem["parts"])):
                if target == source or problem["distances"][source][target] > problem["move_distance"] or not _fits(problem, loads, node, target):
                    continue
                gain = _compatibility(problem, adjacency, incidence, assignment, node, target) - source_score
                choices.append((gain, -node, -target, node, source, target))
        if not choices:
            break
        gain, _, _, node, source, target = max(choices)
        for dimension, weight in enumerate(problem["graph"]["nodes"][node]["weights"]):
            loads[source][dimension] -= weight
            loads[target][dimension] += weight
        assignment[node] = target
        locked[node] = True
        cumulative += gain
        moves.append({"node": node, "source": source, "target": target, "gain": gain, "cumulative_gain": cumulative, "kept": False})
        if cumulative > best_cumulative:
            best_cumulative = cumulative
            best_prefix = len(moves)
            ineffective = 0
        else:
            ineffective += 1
    final = list(problem["assignment"])
    for index, move in enumerate(moves):
        move["kept"] = index < best_prefix
        if move["kept"]:
            final[move["node"]] = move["target"]
    initial_metrics = _partition_metrics(problem, problem["assignment"])
    final_metrics = _partition_metrics(problem, final)
    metrics = {
        "attempted_moves": float(len(moves)),
        "best_prefix": float(best_prefix),
        "best_cumulative_gain": best_cumulative,
    }
    metrics.update({f"initial_{name}": value for name, value in initial_metrics.items()})
    metrics.update({f"final_{name}": value for name, value in final_metrics.items()})
    return moves, final, metrics


def validate_mfspart_refinement(artifact: Mapping[str, Any], problem: Mapping[str, Any]) -> Dict[str, Any]:
    if artifact.get("schema") != MFSPART_REFINEMENT_SCHEMA:
        raise ValidationError("invalid MFSPart refinement schema")
    expected_moves, expected_assignment, expected_metrics = _replay(problem)
    actual_moves = artifact.get("moves")
    if not isinstance(actual_moves, list) or len(actual_moves) != len(expected_moves):
        raise ValidationError("MFSPart FM move count mismatch")
    for expected, actual in zip(expected_moves, actual_moves):
        if (actual.get("node"), actual.get("source"), actual.get("target"), actual.get("kept")) != (expected["node"], expected["source"], expected["target"], expected["kept"]) or not math.isclose(actual.get("gain"), expected["gain"], rel_tol=1e-12, abs_tol=1e-12) or not math.isclose(actual.get("cumulative_gain"), expected["cumulative_gain"], rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError("MFSPart FM move replay mismatch")
    if artifact.get("assignment") != expected_assignment:
        raise ValidationError("MFSPart FM best-prefix rollback mismatch")
    for name, expected in expected_metrics.items():
        actual = artifact.get("metrics", {}).get(name)
        if actual is None or not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValidationError(f"MFSPart FM metric mismatch for {name}")
    return {
        "status": "pass",
        "attempted_moves": len(actual_moves),
        "kept_moves": sum(move["kept"] for move in actual_moves),
        "best_cumulative_gain": artifact["metrics"]["best_cumulative_gain"],
    }


def refine_mfspart_level(
    graph: Mapping[str, Any],
    dimensions: Sequence[str],
    parts: Sequence[str],
    distances: Mapping[str, Mapping[str, int]],
    capacities: Mapping[str, Mapping[str, int]],
    assignment: Union[Mapping[int, int], Sequence[int]],
    output_dir: Path,
    *,
    hmax: int,
    move_distance: int = 2,
    early_stop: int,
    gamma: float = 15.0,
    violation_lambda: float = 10_000.0,
    mu: float = 0.1,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    problem = _normalise_refinement(graph, dimensions, parts, distances, capacities, assignment, hmax=hmax, move_distance=move_distance, early_stop=early_stop, gamma=gamma, violation_lambda=violation_lambda, mu=mu)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "mfspart_refiner.in"
    output_path = output_dir / "mfspart_refiner.out"
    log_path = output_dir / "mfspart_refiner.log"
    _write_native_input(input_path, problem)
    command = resolve_native_executable("emuflow_mfspart_refiner", executable)
    completed = subprocess.run([command, str(input_path.resolve()), str(output_path.resolve())], cwd=output_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise EmuFlowError(f"MFSPart refiner failed with exit code {completed.returncode}: {completed.stdout[-2000:]}")
    parsed = _parse_output(output_path, len(graph["nodes"]))
    artifact = {
        "schema": MFSPART_REFINEMENT_SCHEMA,
        "provider": MFSPART_REFINER_PROVIDER,
        "claim_scope": "independent paper-level direct k-way FM Eqs. 9--10",
        "moves": parsed["moves"],
        "assignment": parsed["assignment"],
        "metrics": parsed["metrics"],
        "artifacts": {"input_sha256": _sha256(input_path), "output_sha256": _sha256(output_path)},
    }
    artifact["validation"] = validate_mfspart_refinement(artifact, problem)
    return artifact


def refine_mfspart_hierarchy(
    hierarchy: Mapping[str, Any],
    initial_partition: Mapping[str, Any],
    parts: Sequence[str],
    distances: Mapping[str, Mapping[str, int]],
    capacities: Mapping[str, Mapping[str, int]],
    output_dir: Path,
    *,
    hmax: int,
    move_distance: int = 2,
    early_stop_fraction: float = 0.2,
    gamma: float = 15.0,
    violation_lambda: float = 10_000.0,
    mu: float = 0.1,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    if hierarchy.get("schema") != MFSPART_HIERARCHY_SCHEMA or initial_partition.get("schema") != MFSPART_INITIAL_SCHEMA:
        raise ValidationError("invalid MFSPart hierarchy or initial partition")
    if not 0 < early_stop_fraction <= 1:
        raise ValidationError("invalid MFSPart early-stop fraction")
    levels = hierarchy["levels"]
    mappings = hierarchy["fine_to_coarse"]
    current = [initial_partition["assignment"][index] for index in range(len(levels[-1]["nodes"]))]
    reports = []
    for level in range(len(levels) - 1, -1, -1):
        if level < len(levels) - 1:
            current = [current[mappings[level][fine]] for fine in range(len(levels[level]["nodes"]))]
        early_stop = max(1, math.ceil(early_stop_fraction * len(current)))
        report = refine_mfspart_level(
            levels[level],
            hierarchy["dimensions"],
            parts,
            distances,
            capacities,
            current,
            output_dir / f"level_{level:03d}",
            hmax=hmax,
            move_distance=move_distance,
            early_stop=early_stop,
            gamma=gamma,
            violation_lambda=violation_lambda,
            mu=mu,
            executable=executable,
        )
        current = report["assignment"]
        reports.append({"level": level, "refinement": report})
    return {
        "schema": "emuflow.mfspart-uncoarsening/v1",
        "provider": MFSPART_REFINER_PROVIDER,
        "levels": reports,
        "assignment": current,
        "validation": {
            "status": "pass",
            "refined_levels": len(reports),
            "original_nodes": len(current),
        },
    }
