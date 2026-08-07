"""Independent adapter and replay oracle for MFSPart min-used legalization."""

from __future__ import annotations

import hashlib
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .errors import EmuFlowError, ValidationError
from .native_tools import resolve_native_executable


MFSPART_LEGALIZATION_SCHEMA = "emuflow.mfspart-min-used-legalization/v1"
MFSPART_LEGALIZER_PROVIDER = "emuflow-native-min-used-legalizer-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise_problem(
    graph: Mapping[str, Any],
    dimensions: Sequence[str],
    parts: Sequence[str],
    capacities: Mapping[str, Mapping[str, int]],
    assignment: Sequence[int],
    min_used: int,
) -> Dict[str, Any]:
    nodes = graph.get("nodes")
    nets = graph.get("nets")
    if not isinstance(nodes, list) or not nodes or not isinstance(nets, list):
        raise ValidationError("invalid MFSPart legalization graph")
    if not dimensions or not parts or len(set(parts)) != len(parts):
        raise ValidationError("invalid MFSPart legalization dimensions or parts")
    if not 0 < min_used <= len(parts):
        raise ValidationError("invalid MFSPart min_used")
    assigned = list(assignment)
    if len(assigned) != len(nodes) or any(
        not isinstance(part, int)
        or isinstance(part, bool)
        or part < 0
        or part >= len(parts)
        for part in assigned
    ):
        raise ValidationError("invalid MFSPart legalization assignment")
    capacity_matrix = []
    for part in parts:
        row = []
        for dimension in dimensions:
            value = capacities.get(part, {}).get(dimension)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValidationError("incomplete MFSPart legalization capacity")
            row.append(value)
        capacity_matrix.append(row)
    for node, record in enumerate(nodes):
        weights = record.get("weights")
        fixed_part = record.get("fixed_part")
        if (
            not isinstance(weights, list)
            or len(weights) != len(dimensions)
            or weights[0] <= 0
            or any(not isinstance(value, int) or value < 0 for value in weights)
            or not isinstance(fixed_part, int)
            or fixed_part < -1
            or fixed_part >= len(parts)
        ):
            raise ValidationError("invalid MFSPart legalization node")
        if fixed_part >= 0 and assigned[node] != fixed_part:
            raise ValidationError("MFSPart legalization input violates fixed node")
    return {
        "graph": {"nodes": nodes, "nets": nets},
        "dimensions": list(dimensions),
        "parts": list(parts),
        "capacities": capacity_matrix,
        "assignment": assigned,
        "min_used": min_used,
    }


def _write_input(path: Path, problem: Mapping[str, Any]) -> None:
    graph = problem["graph"]
    lines = [
        "EMUFLOW_MFSPART_LEGALIZER_INPUT_V1",
        "PARAM "
        + " ".join(
            str(value)
            for value in (
                len(problem["parts"]),
                len(graph["nodes"]),
                len(problem["dimensions"]),
                len(graph["nets"]),
                problem["min_used"],
            )
        ),
    ]
    for part, row in enumerate(problem["capacities"]):
        for dimension, capacity in enumerate(row):
            lines.append(f"CAP {part} {dimension} {capacity}")
    for node, record in enumerate(graph["nodes"]):
        lines.append(
            "NODE "
            + " ".join(
                str(value)
                for value in (node, record["fixed_part"], *record["weights"])
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
        raise EmuFlowError("MFSPart legalizer produced no output")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_MFSPART_LEGALIZER_OUTPUT_V1":
        raise ValidationError("invalid MFSPart legalizer output header")
    status = None
    moves = []
    final: Dict[int, int] = {}
    metrics: Dict[str, int] = {}
    for line in lines[1:]:
        fields = line.split()
        try:
            if fields == ["STATUS", "PASS"]:
                status = "pass"
            elif fields[0] == "MOVE" and len(fields) == 7:
                step, node, source, target = map(int, fields[1:5])
                moves.append(
                    {
                        "step": step,
                        "node": node,
                        "source": source,
                        "target": target,
                        "pair_cut_delta": float(fields[5]),
                        "connectivity_delta": float(fields[6]),
                    }
                )
            elif fields[0] == "FINAL" and len(fields) == 3:
                node, part = map(int, fields[1:])
                if node in final:
                    raise ValidationError("duplicate MFSPart legalizer FINAL")
                final[node] = part
            elif fields[0] == "METRIC" and len(fields) == 3:
                metrics[fields[1]] = int(fields[2])
            else:
                raise ValidationError("invalid MFSPart legalizer output record")
        except (ValueError, IndexError) as error:
            raise ValidationError("malformed MFSPart legalizer output") from error
    if status != "pass" or set(final) != set(range(node_count)):
        raise ValidationError("incomplete MFSPart legalizer output")
    return {
        "moves": moves,
        "assignment": [final[node] for node in range(node_count)],
        "metrics": metrics,
    }


def _objectives(graph: Mapping[str, Any], assignment: Sequence[int]) -> tuple[float, float]:
    pair_cut = 0.0
    connectivity = 0.0
    for net in graph["nets"]:
        source = assignment[net["source"]]
        touched = {source}
        for sink in net["sinks"]:
            target = assignment[sink]
            touched.add(target)
            if source != target:
                pair_cut += net["weight"]
        connectivity += net["weight"] * (len(touched) - 1)
    return pair_cut, connectivity


def validate_mfspart_legalization(
    artifact: Mapping[str, Any], problem: Mapping[str, Any]
) -> Dict[str, Any]:
    if artifact.get("schema") != MFSPART_LEGALIZATION_SCHEMA:
        raise ValidationError("invalid MFSPart legalization schema")
    assignment = list(problem["assignment"])
    graph = problem["graph"]
    loads = [
        [0] * len(problem["dimensions"]) for _ in problem["parts"]
    ]
    for node, part in enumerate(assignment):
        for dimension, weight in enumerate(graph["nodes"][node]["weights"]):
            loads[part][dimension] += weight
    moves = artifact.get("moves")
    if not isinstance(moves, list):
        raise ValidationError("invalid MFSPart legalization moves")
    replayed = []
    while len(set(assignment)) < problem["min_used"]:
        target = next(part for part in range(len(problem["parts"])) if part not in assignment)
        counts = Counter(assignment)
        before_pair, before_connectivity = _objectives(graph, assignment)
        candidates = []
        for node, source in enumerate(assignment):
            record = graph["nodes"][node]
            if counts[source] <= 1 or record["fixed_part"] >= 0:
                continue
            if any(
                loads[target][dimension] + record["weights"][dimension]
                > problem["capacities"][target][dimension]
                for dimension in range(len(problem["dimensions"]))
            ):
                continue
            candidate = list(assignment)
            candidate[node] = target
            after_pair, after_connectivity = _objectives(graph, candidate)
            candidates.append(
                (
                    after_pair - before_pair,
                    after_connectivity - before_connectivity,
                    record["weights"][0],
                    node,
                    source,
                )
            )
        if not candidates:
            raise ValidationError("independent oracle found no legal min-used move")
        pair_delta, connectivity_delta, _, node, source = min(candidates)
        expected = {
            "step": len(replayed),
            "node": node,
            "source": source,
            "target": target,
            "pair_cut_delta": pair_delta,
            "connectivity_delta": connectivity_delta,
        }
        replayed.append(expected)
        assignment[node] = target
        for dimension, weight in enumerate(graph["nodes"][node]["weights"]):
            loads[source][dimension] -= weight
            loads[target][dimension] += weight
    if len(moves) != len(replayed):
        raise ValidationError("MFSPart legalization move count mismatch")
    for actual, expected in zip(moves, replayed):
        if any(actual[key] != expected[key] for key in ("step", "node", "source", "target")):
            raise ValidationError("MFSPart legalization move mismatch")
        for key in ("pair_cut_delta", "connectivity_delta"):
            if not math.isclose(actual[key], expected[key], rel_tol=1e-12, abs_tol=1e-12):
                raise ValidationError("MFSPart legalization delta mismatch")
    if artifact.get("assignment") != assignment:
        raise ValidationError("MFSPart legalization final assignment mismatch")
    if artifact.get("metrics") != {
        "moves": len(replayed),
        "used_parts": len(set(assignment)),
    }:
        raise ValidationError("MFSPart legalization metric mismatch")
    return {"status": "pass", "moves": len(replayed), "used_parts": len(set(assignment))}


def legalize_mfspart_min_used(
    graph: Mapping[str, Any],
    dimensions: Sequence[str],
    parts: Sequence[str],
    capacities: Mapping[str, Mapping[str, int]],
    assignment: Sequence[int],
    min_used: int,
    output_dir: Path,
    *,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    problem = _normalise_problem(
        graph, dimensions, parts, capacities, assignment, min_used
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "mfspart_legalizer.in"
    output_path = output_dir / "mfspart_legalizer.out"
    log_path = output_dir / "mfspart_legalizer.log"
    _write_input(input_path, problem)
    command = resolve_native_executable("emuflow_mfspart_legalizer", executable)
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
            "MFSPart min-used legalizer failed with exit code "
            f"{completed.returncode}: {completed.stdout[-2000:]}"
        )
    parsed = _parse_output(output_path, len(problem["graph"]["nodes"]))
    artifact = {
        "schema": MFSPART_LEGALIZATION_SCHEMA,
        "provider": MFSPART_LEGALIZER_PROVIDER,
        "claim_scope": "EmuFlow min-used-FPGA extension; not an MFSPart paper claim",
        **parsed,
        "artifacts": {
            "input_sha256": _sha256(input_path),
            "output_sha256": _sha256(output_path),
        },
    }
    artifact["validation"] = validate_mfspart_legalization(artifact, problem)
    return artifact
