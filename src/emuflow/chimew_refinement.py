"""Source-qualified, paper-bounded Chimew position refinement.

The paper specifies sorting equal-encoding signals by source y, swapping only
those signals, and never increasing group SLL crossings.  It does not publish
the complete swap schedule or tie breaks.  This module therefore labels its
deterministic anchor ordering and no-worse pairwise-y acceptance as a
first-party inference instead of claiming an exact unpublished reproduction.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .chimew_grouping import (
    CHIMEW_GROUPING_PROVIDER,
    CHIMEW_GROUPING_SCHEMA,
    _domain,
    _integer,
    _popcount,
    _tdm_ratio,
    _string,
    validate_chimew_crossings,
)
from .errors import EmuFlowError, ValidationError
from .native_tools import resolve_native_executable


CHIMEW_POSITION_SCHEMA = "emuflow.chimew-lookahead-positions/v1"
CHIMEW_POSITION_PROVIDER = "source-qualified-physical-site-lookahead-v1"
CHIMEW_REFINED_GROUPING_SCHEMA = "emuflow.chimew-refined-signal-groups/v1"
CHIMEW_REFINEMENT_PROVIDER = "chimew-section3.3.2-bounded-inference-v1"


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label}: expected a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(f"{label}: expected a finite number")
    return result


def _digest(value: Any, label: str) -> str:
    digest = _string(value, label).lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValidationError(f"{label}: expected a SHA-256 digest")
    return digest


def validate_chimew_positions(
    schedule: Mapping[str, Any], document: Mapping[str, Any]
) -> Dict[str, float]:
    """Validate source logic-element y positions in physical site units."""

    if document.get("schema") != CHIMEW_POSITION_SCHEMA:
        raise ValidationError("Chimew lookahead position schema is invalid")
    if document.get("provider") != CHIMEW_POSITION_PROVIDER:
        raise ValidationError("Chimew refinement requires source-qualified placement")
    if document.get("coordinate_system") != "physical-site-y":
        raise ValidationError("Chimew refinement rejects normalized y coordinates")
    if (
        document.get("design") != schedule.get("design")
        or document.get("platform") != schedule.get("platform")
    ):
        raise ValidationError("Chimew positions do not match schedule identity")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("Chimew placement provenance is missing")
    _string(provenance.get("producer"), "chimew.positions.producer")
    _string(provenance.get("producer_version"), "chimew.positions.version")
    _digest(
        provenance.get("placement_sha256"), "chimew.positions.placement_sha256"
    )
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValidationError("Chimew position entries must be an array")
    positions: Dict[str, float] = {}
    for index, record in enumerate(raw_entries):
        if not isinstance(record, dict):
            raise ValidationError(f"chimew.positions[{index}]: expected an object")
        entry_id = _string(
            record.get("schedule_entry"),
            f"chimew.positions[{index}].schedule_entry",
        )
        if entry_id in positions:
            raise ValidationError(f"duplicate Chimew position for {entry_id!r}")
        positions[entry_id] = _number(
            record.get("source_y"), f"chimew.positions[{entry_id}].source_y"
        )
    expected = {entry["id"] for entry in schedule.get("entries", [])}
    if set(positions) != expected:
        raise ValidationError("Chimew positions must cover schedule entries exactly")
    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("signals") != len(positions):
        raise ValidationError("Chimew position metrics do not agree")
    return positions


def _validate_initial_groups(
    schedule: Mapping[str, Any],
    initial: Mapping[str, Any],
    encodings: Mapping[str, int],
) -> Dict[str, int]:
    if initial.get("schema") != CHIMEW_GROUPING_SCHEMA:
        raise ValidationError("Chimew initial grouping schema is invalid")
    if initial.get("provider") != CHIMEW_GROUPING_PROVIDER:
        raise ValidationError("position refinement requires Algorithm 1 groups")
    if (
        initial.get("design") != schedule.get("design")
        or initial.get("platform") != schedule.get("platform")
    ):
        raise ValidationError("Chimew initial groups do not match schedule identity")
    raw_entries = initial.get("entries")
    if not isinstance(raw_entries, list):
        raise ValidationError("Chimew initial group entries must be an array")
    groups: Dict[str, int] = {}
    for index, record in enumerate(raw_entries):
        if not isinstance(record, dict):
            raise ValidationError(f"chimew.groups[{index}]: expected an object")
        entry_id = _string(
            record.get("schedule_entry"), f"chimew.groups[{index}].schedule_entry"
        )
        if entry_id in groups:
            raise ValidationError(f"duplicate Chimew group for {entry_id!r}")
        group = _integer(record.get("group"), f"chimew.groups[{entry_id}].group")
        if record.get("encoding") != encodings.get(entry_id):
            raise ValidationError("Chimew initial group encoding does not agree")
        groups[entry_id] = group
    entries = {entry["id"]: entry for entry in schedule.get("entries", [])}
    if set(groups) != set(entries):
        raise ValidationError("Chimew initial groups must cover schedule exactly")
    grouped: Dict[int, list[str]] = defaultdict(list)
    for entry_id, group in groups.items():
        grouped[group].append(entry_id)
    crossing_bits = 0
    for group, members in grouped.items():
        domains = {_domain(entries[entry_id]) for entry_id in members}
        ratios = {_tdm_ratio(entries[entry_id]) for entry_id in members}
        if len(domains) != 1 or len(ratios) != 1:
            raise ValidationError(f"Chimew group {group} crosses a grouping domain")
        ratio = next(iter(ratios))
        if len(members) > ratio:
            raise ValidationError(f"Chimew group {group} exceeds its TDM ratio")
        encoding = 0
        for entry_id in members:
            encoding |= encodings[entry_id]
        crossing_bits += _popcount(encoding)
    metrics = initial.get("metrics")
    if not isinstance(metrics, dict) or (
        metrics.get("signals") != len(entries)
        or metrics.get("groups") != len(grouped)
        or metrics.get("group_physical_sll_crossings") != crossing_bits
    ):
        raise ValidationError("Chimew initial group metrics do not agree")
    return groups


def _timing_guards(
    schedule: Mapping[str, Any], initial: Mapping[str, Any]
) -> Dict[str, Optional[tuple[Any, ...]]]:
    schedule_by_id = {entry["id"]: entry for entry in schedule.get("entries", [])}
    guards: Dict[str, Optional[tuple[Any, ...]]] = {}
    for record in initial.get("entries", []):
        raw = record.get("timing_guard_lane")
        if raw is not None and (
            not isinstance(raw, list)
            or len(raw) != 4
            or not all(isinstance(value, str) and value for value in raw[:3])
            or isinstance(raw[3], bool)
            or not isinstance(raw[3], int)
            or raw[3] < 0
        ):
            raise ValidationError("Chimew timing guard lane is invalid")
        entry_id = record["schedule_entry"]
        guard = tuple(raw) if raw is not None else None
        if guard is not None:
            entry = schedule_by_id[entry_id]
            expected = (
                entry["link"], entry["from"], entry["to"], entry.get("lane")
            )
            if guard != expected:
                raise ValidationError("Chimew timing guard lane disagrees with schedule")
        guards[entry_id] = guard
    guarded_lanes = {guard for guard in guards.values() if guard is not None}
    for entry_id, entry in schedule_by_id.items():
        lane = (entry["link"], entry["from"], entry["to"], entry.get("lane"))
        if (lane in guarded_lanes) != (guards.get(entry_id) is not None):
            raise ValidationError("Chimew timing guard does not cover a complete lane")
    return guards


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _pairwise_objective(
    members: Mapping[int, set[str]], positions: Mapping[str, float], groups: set[int]
) -> float:
    """Return the sum of absolute y differences within each group.

    For sorted values ``y[i]``, every value contributes ``i * y[i]`` as the
    larger endpoint and the prefix sum as the smaller endpoints.  This is
    mathematically identical to enumerating every pair, but avoids the
    quadratic replay cost for large TDM groups.
    """

    objective = 0.0
    for group in groups:
        values = sorted(
            positions[entry_id] for entry_id in members.get(group, set())
        )
        prefix = 0.0
        for index, value in enumerate(values):
            objective += value * index - prefix
            prefix += value
    return objective


def _oracle_refine(
    entries: Sequence[Mapping[str, Any]],
    encodings: Mapping[str, int],
    positions: Mapping[str, float],
    initial: Mapping[str, int],
    guards: Optional[Mapping[str, Optional[tuple[Any, ...]]]] = None,
) -> Tuple[Dict[str, int], int, int, float, float]:
    assignment = dict(initial)
    all_groups = set(assignment.values())
    members: Dict[int, set[str]] = defaultdict(set)
    for entry_id, group in assignment.items():
        members[group].add(entry_id)
    before_total = _pairwise_objective(members, positions, all_groups)
    guards = guards or {}
    buckets: Dict[
        Tuple[Tuple[str, str, str], int, int, Optional[tuple[Any, ...]]],
        list[str],
    ] = defaultdict(list)
    for entry in entries:
        buckets[
            (
                _domain(entry),
                _tdm_ratio(entry),
                encodings[entry["id"]],
                guards.get(entry["id"]),
            )
        ].append(entry["id"])
    accepted = moved = 0
    guard_sentinel = ("", "", "", -1)
    for (_, _, encoding, _guard), bucket in sorted(
        buckets.items(),
        key=lambda item: (
            item[0][0],
            item[0][3] is not None,
            item[0][3] or guard_sentinel,
            item[0][1],
            item[0][2],
        ),
    ):
        affected = {assignment[entry_id] for entry_id in bucket}
        if len(bucket) < 2 or len(affected) < 2:
            continue
        slots = {group: sum(assignment[item] == group for item in bucket) for group in affected}
        anchors = []
        for group in affected:
            values = [
                positions[item]
                for item in members[group]
                if encodings[item] != encoding
            ]
            if not values:
                values = [
                    positions[item]
                    for item in members[group]
                    if encodings[item] == encoding
                ]
            anchors.append((_median(values), group))
        ordered_groups = sorted(anchors)
        ordered_signals = sorted(bucket, key=lambda item: (positions[item], item))
        old_groups = [assignment[item] for item in ordered_signals]
        before = _pairwise_objective(members, positions, affected)
        position = 0
        for _, group in ordered_groups:
            for _ in range(slots[group]):
                assignment[ordered_signals[position]] = group
                position += 1
        for entry_id, group in zip(ordered_signals, old_groups):
            members[group].remove(entry_id)
        for entry_id in ordered_signals:
            members[assignment[entry_id]].add(entry_id)
        after = _pairwise_objective(members, positions, affected)
        if after > before + 1e-12:
            for entry_id, group in zip(ordered_signals, old_groups):
                members[assignment[entry_id]].remove(entry_id)
                assignment[entry_id] = group
                members[group].add(entry_id)
            continue
        changed = sum(
            assignment[entry_id] != group
            for entry_id, group in zip(ordered_signals, old_groups)
        )
        if changed:
            accepted += 1
            moved += changed
    after_total = _pairwise_objective(members, positions, all_groups)
    return assignment, accepted, moved, before_total, after_total


def _run_native(
    entries: Sequence[Mapping[str, Any]],
    encodings: Mapping[str, int],
    positions: Mapping[str, float],
    initial: Mapping[str, int],
    guards: Mapping[str, Optional[tuple[Any, ...]]],
    executable: Optional[str],
) -> Tuple[Dict[str, int], int, int, float, float]:
    guard_sentinel = ("", "", "", -1)
    domains = sorted(
        {(_domain(entry), guards.get(entry["id"])) for entry in entries},
        key=lambda item: (
            item[0], item[1] is not None, item[1] or guard_sentinel
        ),
    )
    domain_index = {domain: index for index, domain in enumerate(domains)}
    with tempfile.TemporaryDirectory(prefix="emuflow-chimew-refinement-") as temporary:
        root = Path(temporary)
        input_path = root / "input.txt"
        output_path = root / "output.txt"
        lines = ["EMUFLOW_CHIMEW_REFINER_INPUT_V1"]
        for index, entry in enumerate(entries):
            entry_id = entry["id"]
            lines.append(
                "SIGNAL "
                f"{index} {domain_index[(_domain(entry), guards.get(entry_id))]} "
                f"{_tdm_ratio(entry)} "
                f"{encodings[entry_id]} {initial[entry_id]} "
                f"{positions[entry_id]:.17g}"
            )
        input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = resolve_native_executable(
            "emuflow_chimew_position_refiner", executable
        )
        completed = subprocess.run(
            [command, str(input_path), str(output_path)],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise EmuFlowError(
                "Chimew position refiner failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        lines = output_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_CHIMEW_REFINER_OUTPUT_V1":
        raise EmuFlowError("Chimew position refiner output header is invalid")
    assignment: Dict[str, int] = {}
    metrics: Optional[Tuple[int, int, float, float]] = None
    for line in lines[1:]:
        fields = line.split()
        if fields[:1] == ["METRIC"] and len(fields) == 5:
            metrics = (int(fields[1]), int(fields[2]), float(fields[3]), float(fields[4]))
        elif fields[:1] == ["ASSIGN"] and len(fields) == 3:
            index = int(fields[1])
            if not 0 <= index < len(entries):
                raise EmuFlowError("Chimew refiner returned an invalid signal")
            assignment[entries[index]["id"]] = int(fields[2])
        else:
            raise EmuFlowError("Chimew position refiner output is malformed")
    if len(assignment) != len(entries) or metrics is None:
        raise EmuFlowError("Chimew position refiner output is incomplete")
    return assignment, *metrics


def refine_chimew_groups(
    schedule: Mapping[str, Any],
    crossing_document: Mapping[str, Any],
    initial_grouping: Mapping[str, Any],
    position_document: Mapping[str, Any],
    *,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Refine equal-encoding signals and verify the native decision trace."""

    encodings = validate_chimew_crossings(schedule, crossing_document)
    positions = validate_chimew_positions(schedule, position_document)
    initial = _validate_initial_groups(schedule, initial_grouping, encodings)
    guards = _timing_guards(schedule, initial_grouping)
    entries = sorted(schedule["entries"], key=lambda entry: entry["id"])
    native = _run_native(entries, encodings, positions, initial, guards, executable)
    oracle = _oracle_refine(entries, encodings, positions, initial, guards)
    if native[:3] != oracle[:3] or any(
        not math.isclose(native[index], oracle[index], rel_tol=1e-12, abs_tol=1e-9)
        for index in (3, 4)
    ):
        raise EmuFlowError("native Chimew refinement disagrees with Python replay")
    assignment, accepted, moved, before, after = native
    if after > before + 1e-9:
        raise EmuFlowError("Chimew refinement increased the position objective")
    initial_crossings: Dict[int, int] = defaultdict(int)
    refined_crossings: Dict[int, int] = defaultdict(int)
    for entry in entries:
        entry_id = entry["id"]
        initial_crossings[initial[entry_id]] |= encodings[entry_id]
        refined_crossings[assignment[entry_id]] |= encodings[entry_id]
    if initial_crossings != refined_crossings:
        raise EmuFlowError("Chimew refinement changed a group SLL encoding")
    return {
        "schema": CHIMEW_REFINED_GROUPING_SCHEMA,
        "status": "standalone_paper_bounded_inference",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "provider": CHIMEW_REFINEMENT_PROVIDER,
        "paper_scope": "FPGA-2026-Section-3.3.2-position-refinement",
        "claim_boundary": (
            "paper specifies equal-encoding source-y sorting/swaps and SLL "
            "non-increase; group-anchor ordering and pairwise-y acceptance "
            "are deterministic first-party inference"
        ),
        "integration_status": "not-a-phase6-pin-plan",
        "metrics": {
            "signals": len(entries),
            "groups": len(set(assignment.values())),
            "accepted_encoding_buckets": accepted,
            "moved_signals": moved,
            "pairwise_source_y_before": before,
            "pairwise_source_y_after": after,
            "group_physical_sll_crossings": sum(
                _popcount(value) for value in refined_crossings.values()
            ),
            "oracle_disagreements": 0,
        },
        "placement_provenance": dict(position_document["provenance"]),
        "entries": [
            {
                "schedule_entry": entry["id"],
                "group": assignment[entry["id"]],
                "encoding": encodings[entry["id"]],
                "source_y": positions[entry["id"]],
            }
            for entry in entries
        ],
    }
