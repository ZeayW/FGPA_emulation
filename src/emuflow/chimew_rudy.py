"""Source-qualified RUDY congestion gate for Chimew lookahead placement.

The native kernel implements the paper's HPWL wire-area density and exact bin
integration.  Python independently validates the physical input and recomputes
every bin.  Version 1 deliberately rejects zero-area net bounding boxes rather
than silently introducing an unpublished epsilon or normalized-coordinate
substitute.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import EmuFlowError, ValidationError
from .native_tools import resolve_native_executable


CHIMEW_RUDY_INPUT_SCHEMA = "emuflow.chimew-rudy-input/v1"
CHIMEW_RUDY_INPUT_PROVIDER = "source-qualified-lookahead-placement-rudy-v1"
CHIMEW_RUDY_REPORT_SCHEMA = "emuflow.chimew-rudy-report/v1"
CHIMEW_RUDY_PROVIDER = "chimew-section2.3-rudy-gate-v1"


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label}: expected a non-empty string")
    return value


def _integer(value: Any, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{label}: expected an integer >= {minimum}")
    return value


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label}: expected a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        qualifier = "positive " if positive else ""
        raise ValidationError(f"{label}: expected a finite {qualifier}number")
    return result


def _digest(value: Any, label: str) -> str:
    result = _string(value, label).lower()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValidationError(f"{label}: expected a SHA-256 digest")
    return result


def _close(lhs: float, rhs: float) -> bool:
    return math.isclose(lhs, rhs, rel_tol=1e-10, abs_tol=1e-12)


def validate_chimew_rudy_input(document: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a physical lookahead placement, netlist, grid, and capacity map."""

    if document.get("schema") != CHIMEW_RUDY_INPUT_SCHEMA:
        raise ValidationError("Chimew RUDY input schema is invalid")
    if document.get("provider") != CHIMEW_RUDY_INPUT_PROVIDER:
        raise ValidationError("Chimew RUDY requires source-qualified lookahead placement")
    if document.get("coordinate_system") != "physical-site-xy":
        raise ValidationError("Chimew RUDY rejects normalized coordinates")
    if document.get("degenerate_bbox_policy") != "reject":
        raise ValidationError("Chimew RUDY v1 requires degenerate_bbox_policy=reject")
    design = _string(document.get("design"), "chimew.rudy.design")
    platform = _string(document.get("platform"), "chimew.rudy.platform")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("Chimew RUDY provenance is missing")
    normalized_provenance = {
        "producer": _string(provenance.get("producer"), "chimew.rudy.producer"),
        "producer_version": _string(
            provenance.get("producer_version"), "chimew.rudy.producer_version"
        ),
    }
    for field in ("placement_sha256", "netlist_sha256", "architecture_sha256"):
        normalized_provenance[field] = _digest(
            provenance.get(field), f"chimew.rudy.{field}"
        )

    grid = document.get("grid")
    if not isinstance(grid, dict):
        raise ValidationError("Chimew RUDY grid is missing")
    origin_x = _number(grid.get("origin_x"), "chimew.rudy.grid.origin_x")
    origin_y = _number(grid.get("origin_y"), "chimew.rudy.grid.origin_y")
    bin_width = _number(
        grid.get("bin_width"), "chimew.rudy.grid.bin_width", positive=True
    )
    bin_height = _number(
        grid.get("bin_height"), "chimew.rudy.grid.bin_height", positive=True
    )
    columns = _integer(grid.get("columns"), "chimew.rudy.grid.columns")
    rows = _integer(grid.get("rows"), "chimew.rudy.grid.rows")
    wire_pitch = _number(
        document.get("wire_pitch_per_layer"),
        "chimew.rudy.wire_pitch_per_layer",
        positive=True,
    )
    max_utilization = _number(
        document.get("max_utilization"),
        "chimew.rudy.max_utilization",
        positive=True,
    )
    raw_capacities = grid.get("capacities")
    bin_count = columns * rows
    if not isinstance(raw_capacities, list) or len(raw_capacities) != bin_count:
        raise ValidationError("Chimew RUDY capacities must cover every bin exactly")
    capacities = []
    for index, raw in enumerate(raw_capacities):
        capacities.append(
            _number(raw, f"chimew.rudy.grid.capacities[{index}]", positive=True)
        )

    raw_nets = document.get("nets")
    if not isinstance(raw_nets, list) or not raw_nets:
        raise ValidationError("Chimew RUDY requires at least one net")
    nets = []
    seen = set()
    grid_upper_x = origin_x + columns * bin_width
    grid_upper_y = origin_y + rows * bin_height
    pin_count = 0
    for index, raw_net in enumerate(raw_nets):
        if not isinstance(raw_net, dict):
            raise ValidationError(f"chimew.rudy.nets[{index}]: expected an object")
        net_id = _string(raw_net.get("id"), f"chimew.rudy.nets[{index}].id")
        if net_id in seen:
            raise ValidationError(f"duplicate Chimew RUDY net {net_id!r}")
        seen.add(net_id)
        raw_pins = raw_net.get("pins")
        if not isinstance(raw_pins, list) or len(raw_pins) < 2:
            raise ValidationError(f"Chimew RUDY net {net_id!r} needs at least two pins")
        pins = []
        for pin_index, raw_pin in enumerate(raw_pins):
            if not isinstance(raw_pin, dict):
                raise ValidationError(f"Chimew RUDY net {net_id!r} pin is invalid")
            x = _number(raw_pin.get("x"), f"{net_id}.pins[{pin_index}].x")
            y = _number(raw_pin.get("y"), f"{net_id}.pins[{pin_index}].y")
            if not (origin_x <= x <= grid_upper_x and origin_y <= y <= grid_upper_y):
                raise ValidationError(f"Chimew RUDY net {net_id!r} lies outside the grid")
            pins.append((x, y))
        lower_x = min(pin[0] for pin in pins)
        upper_x = max(pin[0] for pin in pins)
        lower_y = min(pin[1] for pin in pins)
        upper_y = max(pin[1] for pin in pins)
        if not (upper_x > lower_x and upper_y > lower_y):
            raise ValidationError(
                f"Chimew RUDY net {net_id!r} has a zero-area bounding box"
            )
        nets.append((net_id, pins))
        pin_count += len(pins)

    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or (
        metrics.get("nets") != len(nets) or metrics.get("pins") != pin_count
    ):
        raise ValidationError("Chimew RUDY input metrics do not agree")
    return {
        "design": design,
        "platform": platform,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "bin_width": bin_width,
        "bin_height": bin_height,
        "columns": columns,
        "rows": rows,
        "wire_pitch": wire_pitch,
        "max_utilization": max_utilization,
        "provenance": normalized_provenance,
        "capacities": capacities,
        "nets": nets,
        "pin_count": pin_count,
    }


def _overlap(lower_a: float, upper_a: float, lower_b: float, upper_b: float) -> float:
    return max(0.0, min(upper_a, upper_b) - max(lower_a, lower_b))


def _oracle(problem: Mapping[str, Any]) -> Tuple[list[float], Dict[str, float]]:
    columns = problem["columns"]
    rows = problem["rows"]
    loads = [0.0] * (columns * rows)
    total_wire_area = 0.0
    for _, pins in problem["nets"]:
        lower_x = min(pin[0] for pin in pins)
        upper_x = max(pin[0] for pin in pins)
        lower_y = min(pin[1] for pin in pins)
        upper_y = max(pin[1] for pin in pins)
        width = upper_x - lower_x
        height = upper_y - lower_y
        wire_area = (width + height) * problem["wire_pitch"]
        density = wire_area / (width * height)
        total_wire_area += wire_area
        first_column = max(
            0,
            math.floor((lower_x - problem["origin_x"]) / problem["bin_width"]),
        )
        last_column = min(
            columns - 1,
            math.ceil((upper_x - problem["origin_x"]) / problem["bin_width"])
            - 1,
        )
        first_row = max(
            0,
            math.floor((lower_y - problem["origin_y"]) / problem["bin_height"]),
        )
        last_row = min(
            rows - 1,
            math.ceil((upper_y - problem["origin_y"]) / problem["bin_height"])
            - 1,
        )
        for row in range(first_row, last_row + 1):
            bin_lower_y = problem["origin_y"] + row * problem["bin_height"]
            y_overlap = _overlap(
                lower_y, upper_y, bin_lower_y, bin_lower_y + problem["bin_height"]
            )
            if y_overlap == 0.0:
                continue
            for column in range(first_column, last_column + 1):
                bin_lower_x = problem["origin_x"] + column * problem["bin_width"]
                x_overlap = _overlap(
                    lower_x,
                    upper_x,
                    bin_lower_x,
                    bin_lower_x + problem["bin_width"],
                )
                loads[row * columns + column] += density * x_overlap * y_overlap
    utilizations = [
        load / capacity for load, capacity in zip(loads, problem["capacities"])
    ]
    metrics = {
        "nets": len(problem["nets"]),
        "pins": problem["pin_count"],
        "total_wire_area": total_wire_area,
        "total_bin_load": sum(loads),
        "peak_load": max(loads),
        "peak_utilization": max(utilizations),
        "overloaded_bins": sum(
            value > problem["max_utilization"] + 1e-12 for value in utilizations
        ),
    }
    return loads, metrics


def _run_native(
    problem: Mapping[str, Any], executable: Optional[str]
) -> Tuple[list[float], list[float], Dict[str, float]]:
    with tempfile.TemporaryDirectory(prefix="emuflow-chimew-rudy-") as temporary:
        root = Path(temporary)
        input_path = root / "input.txt"
        output_path = root / "output.txt"
        lines = [
            "EMUFLOW_CHIMEW_RUDY_INPUT_V1",
            "GRID "
            f"{problem['origin_x']:.17g} {problem['origin_y']:.17g} "
            f"{problem['bin_width']:.17g} {problem['bin_height']:.17g} "
            f"{problem['columns']} {problem['rows']}",
            "PARAM "
            f"{problem['wire_pitch']:.17g} {problem['max_utilization']:.17g}",
        ]
        for index, capacity in enumerate(problem["capacities"]):
            lines.append(f"CAP {index} {capacity:.17g}")
        for index, (_, pins) in enumerate(problem["nets"]):
            coordinates = " ".join(f"{x:.17g} {y:.17g}" for x, y in pins)
            lines.append(f"NET {index} {len(pins)} {coordinates}")
        input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = resolve_native_executable("emuflow_chimew_rudy", executable)
        completed = subprocess.run(
            [command, str(input_path), str(output_path)],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise EmuFlowError(
                "Chimew RUDY kernel failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        output_lines = output_path.read_text(encoding="utf-8").splitlines()
    if not output_lines or output_lines[0] != "EMUFLOW_CHIMEW_RUDY_OUTPUT_V1":
        raise EmuFlowError("Chimew RUDY output header is invalid")
    loads = [None] * len(problem["capacities"])
    utilizations = [None] * len(problem["capacities"])
    metrics = None
    for line in output_lines[1:]:
        fields = line.split()
        if fields[:1] == ["METRIC"] and len(fields) == 8:
            metrics = {
                "nets": int(fields[1]),
                "pins": int(fields[2]),
                "total_wire_area": float(fields[3]),
                "total_bin_load": float(fields[4]),
                "peak_load": float(fields[5]),
                "peak_utilization": float(fields[6]),
                "overloaded_bins": int(fields[7]),
            }
        elif fields[:1] == ["BIN"] and len(fields) == 4:
            index = int(fields[1])
            if not 0 <= index < len(loads) or loads[index] is not None:
                raise EmuFlowError("Chimew RUDY returned an invalid bin")
            loads[index] = float(fields[2])
            utilizations[index] = float(fields[3])
        else:
            raise EmuFlowError("Chimew RUDY output is malformed")
    if metrics is None or any(value is None for value in loads + utilizations):
        raise EmuFlowError("Chimew RUDY output is incomplete")
    return loads, utilizations, metrics


def evaluate_chimew_rudy(
    document: Mapping[str, Any], *, executable: Optional[str] = None
) -> Dict[str, Any]:
    """Evaluate and independently replay the source-qualified RUDY gate."""

    problem = validate_chimew_rudy_input(document)
    oracle_loads, oracle_metrics = _oracle(problem)
    native_loads, native_utilizations, native_metrics = _run_native(
        problem, executable
    )
    for field in ("nets", "pins", "overloaded_bins"):
        if native_metrics[field] != oracle_metrics[field]:
            raise EmuFlowError(f"native Chimew RUDY {field} disagrees with replay")
    for field in (
        "total_wire_area",
        "total_bin_load",
        "peak_load",
        "peak_utilization",
    ):
        if not _close(native_metrics[field], oracle_metrics[field]):
            raise EmuFlowError(f"native Chimew RUDY {field} disagrees with replay")
    bins = []
    for index, (native_load, oracle_load, utilization, capacity) in enumerate(
        zip(native_loads, oracle_loads, native_utilizations, problem["capacities"])
    ):
        expected_utilization = oracle_load / capacity
        if not _close(native_load, oracle_load) or not _close(
            utilization, expected_utilization
        ):
            raise EmuFlowError(f"native Chimew RUDY bin {index} disagrees with replay")
        bins.append(
            {
                "column": index % problem["columns"],
                "row": index // problem["columns"],
                "capacity": capacity,
                "load": native_load,
                "utilization": utilization,
            }
        )
    canonical_input = json.dumps(
        {
            "origin": [problem["origin_x"], problem["origin_y"]],
            "bin": [problem["bin_width"], problem["bin_height"]],
            "shape": [problem["columns"], problem["rows"]],
            "wire_pitch": problem["wire_pitch"],
            "max_utilization": problem["max_utilization"],
            "provenance": problem["provenance"],
            "capacities": problem["capacities"],
            "nets": [
                {"id": net_id, "pins": pins} for net_id, pins in problem["nets"]
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": CHIMEW_RUDY_REPORT_SCHEMA,
        "status": "standalone_paper_kernel",
        "gate_status": "pass" if native_metrics["overloaded_bins"] == 0 else "rejected",
        "integration_status": "not-a-phase6-pin-plan",
        "design": problem["design"],
        "platform": problem["platform"],
        "provider": CHIMEW_RUDY_PROVIDER,
        "paper_scope": "FPGA-2026-Section-2.3-RUDY",
        "threshold_scope": "explicit-qualification-policy-not-paper-constant",
        "degenerate_bbox_policy": "reject-unpublished-substitution",
        "provenance": problem["provenance"],
        "input_sha256": hashlib.sha256(canonical_input).hexdigest(),
        "max_utilization": problem["max_utilization"],
        "metrics": {**native_metrics, "oracle_disagreements": 0},
        "bins": bins,
    }
