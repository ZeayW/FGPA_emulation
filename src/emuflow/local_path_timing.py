"""Source-bound post-route timing for original paths local to one FPGA."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .logic_segment_timing import (
    LOGIC_SEGMENT_QUERY_HEADER,
    LOGIC_SEGMENT_TIMING_HEADER,
    _vpr_atom_pin,
)
from .partition import PARTITION_ASSIGNMENT_SCHEMA
from .sta import (
    STA_PATH_DATABASE_SCHEMA,
    sta_object_index,
    sta_path_endpoints,
    validate_sta_path_database,
)


LOCAL_PATH_IDENTITY_SCHEMA = "emuflow.local-path-identity/v1"
LOCAL_PATH_TIMING_SCHEMA = "emuflow.local-path-timing/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def path_id_set_sha256(path_ids: list[str] | set[str]) -> str:
    """Return an unambiguous digest of a sorted set of original path IDs."""
    encoded = json.dumps(
        sorted(path_ids), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_manifest(
    database: Mapping[str, Any],
    path: Path,
    original_ir_path: Path,
    assignment_path: Path,
    routes_path: Path,
) -> Dict[str, Any]:
    ids = [item["id"] for item in database["paths"]]
    return {
        "path_database_sha256": _sha256(path),
        "original_ir_sha256": _sha256(original_ir_path),
        "assignment_sha256": _sha256(assignment_path),
        "routes_sha256": _sha256(routes_path),
        "original_paths": len(ids),
        "original_path_ids_sha256": path_id_set_sha256(ids),
    }


def validate_local_path_identity(database: Mapping[str, Any]) -> Dict[str, Any]:
    if (
        database.get("schema") != LOCAL_PATH_IDENTITY_SCHEMA
        or database.get("status") != "pass"
    ):
        raise ValidationError("local path identity is invalid")
    fpga = database.get("fpga")
    if not isinstance(fpga, str) or not fpga:
        raise ValidationError("local path identity FPGA is invalid")
    source = database.get("source")
    if not isinstance(source, dict) or set(source) != {
        "path_database_sha256",
        "original_ir_sha256",
        "assignment_sha256",
        "routes_sha256",
        "original_paths",
        "original_path_ids_sha256",
    }:
        raise ValidationError("local path identity source seal is invalid")
    if (
        any(
            not isinstance(source[field], str) or len(source[field]) != 64
            for field in (
                "path_database_sha256",
                "original_ir_sha256",
                "assignment_sha256",
                "routes_sha256",
                "original_path_ids_sha256",
            )
        )
        or isinstance(source["original_paths"], bool)
        or not isinstance(source["original_paths"], int)
        or source["original_paths"] <= 0
    ):
        raise ValidationError("local path identity source seal is invalid")
    paths = database.get("paths")
    if not isinstance(paths, list):
        raise ValidationError("local path identity paths are invalid")
    ids = set()
    for index, path in enumerate(paths):
        context = f"local path identity[{index}]"
        if not isinstance(path, dict) or set(path) != {
            "id",
            "kind",
            "fpga",
            "clock_domain",
            "clock_period_ns",
            "start_pin",
            "end_pin",
        }:
            raise ValidationError(f"{context} is invalid")
        path_id = path["id"]
        if (
            not isinstance(path_id, str)
            or not path_id
            or path_id in ids
            or path["kind"] != "local"
            or path["fpga"] != fpga
            or not isinstance(path["clock_domain"], str)
            or not path["clock_domain"]
            or isinstance(path["clock_period_ns"], bool)
            or not isinstance(path["clock_period_ns"], (int, float))
            or not math.isfinite(float(path["clock_period_ns"]))
            or float(path["clock_period_ns"]) <= 0.0
            or not isinstance(path["start_pin"], str)
            or not path["start_pin"]
            or not isinstance(path["end_pin"], str)
            or not path["end_pin"]
        ):
            raise ValidationError(f"{context} is invalid")
        ids.add(path_id)
    coverage = database.get("coverage")
    if not isinstance(coverage, dict) or coverage != {
        "local_paths": len(paths)
    }:
        raise ValidationError("local path identity coverage is invalid")
    return {"status": "pass", "fpga": fpga, "local_paths": len(paths)}


def write_vpr_local_path_query(
    original_ir_path: Path,
    assignment_path: Path,
    path_database_path: Path,
    routes_path: Path,
    merged_ir_path: Path,
    fpga: str,
    query_path: Path,
    identity_path: Path,
) -> Dict[str, Any]:
    """Materialize every original same-partition path as a routed VPR query."""
    validate_sta_path_database(path_database_path, original_ir_path)
    original_ir = EmuIR.load(original_ir_path)
    merged_ir = EmuIR.load(merged_ir_path)
    assignment = read_json(assignment_path)
    database = read_json(path_database_path)
    routes = read_json(routes_path)
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError("local path assignment schema is invalid")
    if assignment.get("design") != database.get("design"):
        raise ValidationError("local path assignment design disagrees")
    raw_route_timing = routes.get("timing", {}).get("paths")
    if not isinstance(raw_route_timing, list):
        raise ValidationError("local path system route timing is invalid")
    cross_path_ids = set()
    for item in raw_route_timing:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValidationError("local path system route timing is invalid")
        members = item.get("compressed_path_ids", [item["path"]])
        if (
            not isinstance(members, list)
            or not all(isinstance(member, str) and member for member in members)
        ):
            raise ValidationError("local path route member coverage is invalid")
        cross_path_ids.update(members)
    if merged_ir.value["design"]["name"] != f"{assignment['design']}__{fpga}":
        raise ValidationError("local path merged IR target is invalid")
    instance_assignment = assignment.get("instance_assignment")
    if not isinstance(instance_assignment, dict):
        raise ValidationError("local path instance assignment is invalid")
    object_index = sta_object_index(original_ir)
    merged_index = {
        item["id"]: index
        for index, item in enumerate(merged_ir.value["instances"])
    }
    merged_instances = {
        item["id"]: item for item in merged_ir.value["instances"]
    }
    records = []
    unresolved = []
    for path in database["paths"]:
        if path["id"] in cross_path_ids:
            continue
        try:
            start, end = sta_path_endpoints(path, object_index)
        except ValidationError as error:
            unresolved.append({"path": path["id"], "reason": str(error)})
            continue
        start_instance = start["instance"]
        end_instance = end["instance"]
        start_fpga = instance_assignment.get(start_instance)
        end_fpga = instance_assignment.get(end_instance)
        if not isinstance(start_fpga, str) or not isinstance(end_fpga, str):
            unresolved.append(
                {"path": path["id"], "reason": "endpoint-partition-unresolved"}
            )
            continue
        if start_fpga != end_fpga:
            raise ValidationError(
                f"original path {path['id']!r} is cross-partition but absent "
                "from the Phase 4 timing population"
            )
        if start_fpga != fpga:
            continue
        records.append(
            {
                "id": path["id"],
                "kind": "local",
                "fpga": fpga,
                "clock_domain": path["clock_domain"],
                "clock_period_ns": float(path["clock_period_ns"]),
                "start_pin": _vpr_atom_pin(
                    merged_ir, merged_index, start, merged_instances
                ),
                "end_pin": _vpr_atom_pin(
                    merged_ir, merged_index, end, merged_instances
                ),
            }
        )
    if unresolved:
        raise ValidationError(
            "complete local path timing has unresolved original endpoints: "
            f"{unresolved[:10]}"
        )
    records.sort(key=lambda item: item["id"])
    identity = {
        "schema": LOCAL_PATH_IDENTITY_SCHEMA,
        "status": "pass",
        "design": assignment["design"],
        "fpga": fpga,
        "provider": "original-timing-pathdb-to-vpr-routed-endpoints-v1",
        "source": _source_manifest(
            database,
            path_database_path,
            original_ir_path,
            assignment_path,
            routes_path,
        ),
        "coverage": {"local_paths": len(records)},
        "paths": records,
    }
    validate_local_path_identity(identity)
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(identity_path, identity)
    rows = [
        LOGIC_SEGMENT_QUERY_HEADER,
        *(
            "\t".join(
                (item["id"], "local", item["start_pin"], item["end_pin"])
            )
            for item in records
        ),
    ]
    query_path.parent.mkdir(parents=True, exist_ok=True)
    query_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return {
        "status": "pass",
        "fpga": fpga,
        "local_paths": len(records),
        "query": str(query_path),
        "identity": str(identity_path),
    }


def import_vpr_local_path_timing(
    input_path: Path,
    identity_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    identity = read_json(identity_path)
    validate_local_path_identity(identity)
    expected = {item["id"]: item for item in identity["paths"]}
    lines = input_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != LOGIC_SEGMENT_TIMING_HEADER:
        raise ValidationError("VPR local path timing header is invalid")
    measured: Dict[str, float] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        if not line:
            continue
        fields = line.split("\t")
        item = expected.get(fields[0]) if len(fields) == 5 else None
        try:
            delay = float(fields[2])
        except (IndexError, ValueError) as error:
            raise ValidationError(
                f"VPR local path timing line {line_number} is invalid"
            ) from error
        if (
            item is None
            or fields[0] in measured
            or fields[1] != "local"
            or fields[3] != item["start_pin"]
            or fields[4] != item["end_pin"]
            or not math.isfinite(delay)
            or delay < 0.0
        ):
            raise ValidationError(
                f"VPR local path timing line {line_number} is invalid"
            )
        measured[fields[0]] = delay
    if set(measured) != set(expected):
        raise ValidationError(
            "VPR local path timing coverage is incomplete: "
            f"{sorted(set(expected) - set(measured))[:10]}"
        )
    database = {
        "schema": LOCAL_PATH_TIMING_SCHEMA,
        "status": "pass",
        "design": identity["design"],
        "fpga": identity["fpga"],
        "provider": "vpr-tatum-original-local-setup-path-delay-v1",
        "qualification": (
            "source-bound-routed-endpoint-delay-with-capture-setup"
        ),
        "source": identity["source"],
        "coverage": identity["coverage"],
        "paths": [
            {**item, "delay_ns": measured[item["id"]]}
            for item in identity["paths"]
        ],
    }
    validation = validate_local_path_timing(database)
    write_json(output_path, database)
    return {**validation, "output": str(output_path)}


def validate_local_path_timing(database: Mapping[str, Any]) -> Dict[str, Any]:
    if database.get("schema") != LOCAL_PATH_TIMING_SCHEMA:
        raise ValidationError("local path timing schema is invalid")
    identity = {
        key: database[key]
        for key in (
            "status", "design", "fpga", "source", "coverage", "paths"
        )
    }
    identity.update(
        {
            "schema": LOCAL_PATH_IDENTITY_SCHEMA,
            "provider": "local-path-timing-validation",
            "paths": [
                {key: value for key, value in item.items() if key != "delay_ns"}
                for item in database["paths"]
            ],
        }
    )
    checked = validate_local_path_identity(identity)
    maximum = 0.0
    for item in database["paths"]:
        delay = item.get("delay_ns")
        if (
            isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not math.isfinite(float(delay))
            or float(delay) < 0.0
        ):
            raise ValidationError("local path timing delay is invalid")
        maximum = max(maximum, float(delay))
    return {**checked, "maximum_delay_ns": maximum}
