"""Standalone, source-qualified reproduction of Chimew Algorithm 1.

This module deliberately stops at the paper's encoding-based initial grouping.
It does not silently substitute normalized placement regions for physical SLLs,
and its output is not a Phase 6 pin plan until the placement refinement and
two-stage bank/channel assignment gates are implemented.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .native_tools import resolve_native_executable


CHIMEW_CROSSING_SCHEMA = "emuflow.chimew-crossing-encodings/v1"
CHIMEW_GROUPING_SCHEMA = "emuflow.chimew-signal-groups/v1"
CHIMEW_GROUPING_PROVIDER = "chimew-algorithm1-encoding-grouping-v1"
CHIMEW_CROSSING_PROVIDER = "source-qualified-physical-sll-routing-v1"
CHIMEW_ACADEMIC_CROSSING_PROVIDER = (
    "academic-virtual-region-routing-lookahead-v1"
)
CHIMEW_SCHEDULE_RATIO_PROVIDER = (
    "emuflow-lane-occupancy-ratio-materializer-v1"
)


def _popcount(value: int) -> int:
    return bin(value).count("1")


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{label}: expected an integer >= {minimum}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label}: expected a non-empty string")
    return value


def _crossing_mask(values: Any, count: int, label: str) -> int:
    if not isinstance(values, list) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        raise ValidationError(f"{label}: expected an integer list")
    if len(values) != len(set(values)):
        raise ValidationError(f"{label}: duplicate SLL index")
    if any(value < 0 or value >= count for value in values):
        raise ValidationError(f"{label}: SLL index is out of range")
    mask = 0
    for value in values:
        mask |= 1 << value
    return mask


def validate_chimew_crossings(
    schedule: Mapping[str, Any], document: Mapping[str, Any]
) -> Dict[str, int]:
    """Validate exact physical-SLL encodings against a schedule."""

    if document.get("schema") != CHIMEW_CROSSING_SCHEMA:
        raise ValidationError("Chimew crossing encoding schema is invalid")
    provider = document.get("provider")
    if provider not in {
        CHIMEW_CROSSING_PROVIDER,
        CHIMEW_ACADEMIC_CROSSING_PROVIDER,
    }:
        raise ValidationError(
            "Chimew crossings require source-qualified physical SLL routing"
        )
    if provider == CHIMEW_ACADEMIC_CROSSING_PROVIDER and (
        document.get("qualification")
        != "academic-virtual-region-lookahead"
        or document.get("coordinate_system") != "normalized-placement-y"
    ):
        raise ValidationError(
            "academic Chimew crossings require an explicit virtual-region "
            "qualification"
        )
    if (
        document.get("design") != schedule.get("design")
        or document.get("platform") != schedule.get("platform")
    ):
        raise ValidationError("Chimew crossings do not match schedule identity")
    slls_per_fpga = _integer(
        document.get("slls_per_fpga"), "chimew.slls_per_fpga", minimum=1
    )
    if slls_per_fpga > 31:
        raise ValidationError("Chimew v1 supports at most 31 SLLs per FPGA")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("Chimew crossing provenance is missing")
    _string(provenance.get("producer"), "chimew.provenance.producer")
    _string(provenance.get("producer_version"), "chimew.provenance.version")
    digest = _string(
        provenance.get("routing_sha256"), "chimew.provenance.routing_sha256"
    ).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValidationError("Chimew routing provenance SHA-256 is invalid")

    expected = {entry["id"] for entry in schedule.get("entries", [])}
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise ValidationError("Chimew crossing entries must be an array")
    encodings: Dict[str, int] = {}
    total_crossings = 0
    for index, record in enumerate(raw_entries):
        if not isinstance(record, dict):
            raise ValidationError(f"chimew.entries[{index}]: expected an object")
        entry_id = _string(
            record.get("schedule_entry"), f"chimew.entries[{index}].schedule_entry"
        )
        if entry_id in encodings:
            raise ValidationError(f"duplicate Chimew crossing for {entry_id!r}")
        source = _crossing_mask(
            record.get("source_slls"), slls_per_fpga, f"{entry_id}.source_slls"
        )
        sink = _crossing_mask(
            record.get("sink_slls"), slls_per_fpga, f"{entry_id}.sink_slls"
        )
        encoding = source | (sink << slls_per_fpga)
        if record.get("encoding") != encoding:
            raise ValidationError(
                f"Chimew crossing {entry_id!r} encoding is not independently derived"
            )
        encodings[entry_id] = encoding
        total_crossings += _popcount(encoding)
    if set(encodings) != expected:
        raise ValidationError("Chimew crossings must cover schedule entries exactly")
    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or (
        metrics.get("signals") != len(encodings)
        or metrics.get("physical_sll_crossings") != total_crossings
    ):
        raise ValidationError("Chimew crossing metrics do not agree")
    return encodings


def _domain(entry: Mapping[str, Any]) -> Tuple[str, str, str]:
    return entry["link"], entry["from"], entry["to"]


def _tdm_ratio(entry: Mapping[str, Any]) -> int:
    """Return the serialization ratio; direct-lane schedules imply ratio one."""

    return _integer(
        entry.get("tdm_ratio", 1), f"{entry['id']}.tdm_ratio", minimum=1
    )


def materialize_chimew_schedule_ratios(
    schedule: Mapping[str, Any],
) -> Dict[str, Any]:
    """Make implicit lane occupancy explicit for the Chimew adapter.

    This is an EmuFlow integration transform, not a Chimew paper kernel.  A
    baseline schedule already fixes a direction-qualified logical lane and a
    unique slot for every signal.  The number of signals occupying that lane
    is therefore the exact serialization capacity of its existing group.
    """

    raw_entries = schedule.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValidationError("Chimew ratio materialization requires schedule entries")
    explicit = ["tdm_ratio" in entry for entry in raw_entries]
    if any(explicit):
        if not all(explicit):
            raise ValidationError(
                "Chimew schedule mixes explicit and implicit TDM ratios"
            )
        raise ValidationError("Chimew schedule ratios are already explicit")

    entry_ids = set()
    groups: Dict[Tuple[str, str, str, int], list[str]] = defaultdict(list)
    occupancy = set()
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ValidationError(f"schedule.entries[{index}] is invalid")
        entry_id = _string(entry.get("id"), f"schedule.entries[{index}].id")
        link = _string(entry.get("link"), f"{entry_id}.link")
        source = _string(entry.get("from"), f"{entry_id}.from")
        sink = _string(entry.get("to"), f"{entry_id}.to")
        lane = _integer(entry.get("lane"), f"{entry_id}.lane")
        slot = _integer(entry.get("slot"), f"{entry_id}.slot")
        if entry_id in entry_ids:
            raise ValidationError("Chimew ratio materialization found duplicate IDs")
        collision = (link, source, sink, lane, slot)
        if collision in occupancy:
            raise ValidationError(
                "Chimew ratio materialization found a lane/slot collision"
            )
        entry_ids.add(entry_id)
        occupancy.add(collision)
        groups[(link, source, sink, lane)].append(entry_id)

    result = copy.deepcopy(dict(schedule))
    ratio_by_id = {
        entry_id: len(member_ids)
        for member_ids in groups.values()
        for entry_id in member_ids
    }
    for entry in result["entries"]:
        entry["tdm_ratio"] = ratio_by_id[entry["id"]]
    source_sha256 = hashlib.sha256(
        json.dumps(
            schedule, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    result["chimew_ratio_materialization"] = {
        "provider": CHIMEW_SCHEDULE_RATIO_PROVIDER,
        "scope": "EmuFlow adapter, not a Chimew paper claim",
        "source_schedule_sha256": source_sha256,
        "direction_lane_groups": len(groups),
        "max_lane_occupancy": max(map(len, groups.values())),
    }
    return result


def _nearest_key(
    encoding: int, target: int, multiplicity: Mapping[int, int], index: int
) -> tuple[int, int, int, int, int, int]:
    category = 0 if encoding == target else 1 if encoding | target == target else 2
    return (
        category,
        _popcount(encoding ^ target) if category == 2 else 0,
        -_popcount(encoding),
        multiplicity[encoding],
        -encoding,
        index,
    )


def _oracle_groups(
    entries: Sequence[Mapping[str, Any]], encodings: Mapping[str, int]
) -> tuple[Dict[str, int], int, int]:
    buckets: Dict[tuple[Tuple[str, str, str], int], list[int]] = defaultdict(list)
    for index, entry in enumerate(entries):
        buckets[(_domain(entry), _tdm_ratio(entry))].append(index)
    assignment: Dict[str, int] = {}
    group_count = 0
    crossing_bits = 0
    for (_, ratio), indices in sorted(buckets.items()):
        multiplicity = Counter(encodings[entries[index]["id"]] for index in indices)
        remaining = sorted(
            indices,
            key=lambda index: (
                -_popcount(encodings[entries[index]["id"]]),
                -encodings[entries[index]["id"]],
                index,
            ),
        )
        while remaining:
            target = encodings[entries[remaining[0]]["id"]]
            members = []
            while remaining and len(members) < ratio:
                selected = min(
                    remaining,
                    key=lambda index: _nearest_key(
                        encodings[entries[index]["id"]], target, multiplicity, index
                    ),
                )
                encoding = encodings[entries[selected]["id"]]
                members.append(selected)
                target |= encoding
                multiplicity[encoding] -= 1
                remaining.remove(selected)
            for index in members:
                assignment[entries[index]["id"]] = group_count
            crossing_bits += _popcount(target)
            group_count += 1
    return assignment, group_count, crossing_bits


def _run_native(
    entries: Sequence[Mapping[str, Any]],
    encodings: Mapping[str, int],
    executable: Optional[str],
) -> tuple[Dict[str, int], int, int]:
    domains = sorted({_domain(entry) for entry in entries})
    domain_index = {domain: index for index, domain in enumerate(domains)}
    with tempfile.TemporaryDirectory(prefix="emuflow-chimew-grouping-") as temporary:
        root = Path(temporary)
        input_path = root / "input.txt"
        output_path = root / "output.txt"
        lines = ["EMUFLOW_CHIMEW_GROUPER_INPUT_V1"]
        for index, entry in enumerate(entries):
            lines.append(
                f"SIGNAL {index} {domain_index[_domain(entry)]} "
                f"{_tdm_ratio(entry)} {encodings[entry['id']]}"
            )
        input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = resolve_native_executable(
            "emuflow_chimew_signal_grouper", executable
        )
        completed = subprocess.run(
            [command, str(input_path), str(output_path)],
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise EmuFlowError(
                "Chimew signal grouper failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        lines = output_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "EMUFLOW_CHIMEW_GROUPER_OUTPUT_V1":
        raise EmuFlowError("Chimew signal grouper output header is invalid")
    assignment: Dict[str, int] = {}
    group_count = crossing_bits = None
    for line in lines[1:]:
        fields = line.split()
        if fields[:1] == ["METRIC"] and len(fields) == 3:
            group_count, crossing_bits = int(fields[1]), int(fields[2])
        elif fields[:1] == ["ASSIGN"] and len(fields) == 3:
            index = int(fields[1])
            if not 0 <= index < len(entries):
                raise EmuFlowError("Chimew grouper returned an invalid signal")
            assignment[entries[index]["id"]] = int(fields[2])
        else:
            raise EmuFlowError("Chimew signal grouper output is malformed")
    if len(assignment) != len(entries) or group_count is None or crossing_bits is None:
        raise EmuFlowError("Chimew signal grouper output is incomplete")
    return assignment, group_count, crossing_bits


def build_chimew_initial_groups(
    schedule: Mapping[str, Any],
    crossing_document: Mapping[str, Any],
    *,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Run paper Algorithm 1 and independently replay every group decision."""

    encodings = validate_chimew_crossings(schedule, crossing_document)
    entries = sorted(schedule["entries"], key=lambda entry: entry["id"])
    native = _run_native(entries, encodings, executable)
    oracle = _oracle_groups(entries, encodings)
    if native != oracle:
        raise EmuFlowError("native Chimew grouping disagrees with Python replay")
    assignment, group_count, crossing_bits = native
    return {
        "schema": CHIMEW_GROUPING_SCHEMA,
        "status": "standalone_paper_kernel",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "provider": CHIMEW_GROUPING_PROVIDER,
        "paper_scope": "FPGA-2026-Algorithm-1-initial-grouping",
        "integration_status": "not-a-phase6-pin-plan",
        "tie_break": "stable-encoding-count-then-schedule-entry-order",
        "metrics": {
            "signals": len(entries),
            "groups": group_count,
            "group_physical_sll_crossings": crossing_bits,
            "oracle_disagreements": 0,
        },
        "entries": [
            {
                "schedule_entry": entry["id"],
                "group": assignment[entry["id"]],
                "encoding": encodings[entry["id"]],
            }
            for entry in entries
        ],
    }
