"""STA extraction adapters with stable EmuIR cut-net identity."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .partition import PARTITION_ASSIGNMENT_SCHEMA
from .timing_routing import STA_PATHS_SCHEMA, compress_sta_paths


VIVADO_STA_TSV_HEADER = (
    "path_id_hex\tclock_domain_hex\tclock_period_ns\tslack_ns\t"
    "fixed_delay_ns\tcut_nets_hex"
)
VIVADO_CUT_NET_MAP_HEADER = "vivado_net_hex\tcut_net_hex"
STA_PATH_DATABASE_SCHEMA = "emuflow.sta-path-database/v1"
VIVADO_NET_MAP_HEADER = "vivado_net_hex\temuir_net_hex"
VIVADO_PATH_DATABASE_TSV_HEADER = (
    "path_id_hex\tclock_domain_hex\tclock_period_ns\tslack_ns\t"
    "fixed_delay_ns\tpath_nets_hex"
)


def _hex_encode(value: str) -> str:
    return value.encode("utf-8").hex()


def _hex_decode(value: str, context: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValidationError(f"{context}: invalid UTF-8 hex") from error


def _database_normalization(paths: list[Dict[str, Any]]) -> Dict[str, float]:
    positive_scale = max(
        (path["slack_ns"] for path in paths if path["slack_ns"] >= 0.0),
        default=1.0,
    )
    if positive_scale == 0.0:
        positive_scale = 1.0
    negative_scale = abs(
        min(
            (path["slack_ns"] for path in paths if path["slack_ns"] < 0.0),
            default=-1.0,
        )
    )
    max_period = max(path["clock_period_ns"] for path in paths)
    return {
        "positive_slack_scale_ns": positive_scale,
        "negative_slack_scale_ns": negative_scale,
        "max_clock_period_ns": max_period,
    }


def _validate_database_normalization(
    value: Any,
) -> Dict[str, float]:
    if not isinstance(value, dict):
        raise ValidationError("STA path database normalization is invalid")
    expected_keys = {
        "positive_slack_scale_ns",
        "negative_slack_scale_ns",
        "max_clock_period_ns",
    }
    if set(value) != expected_keys:
        raise ValidationError("STA path database normalization is invalid")
    result = {}
    for key in sorted(expected_keys):
        item = value[key]
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or float(item) <= 0.0
        ):
            raise ValidationError(
                f"STA path database normalization.{key} is invalid"
            )
        result[key] = float(item)
    return result


def _normalized_slack(
    period: float,
    slack: float,
    normalization: Mapping[str, float],
) -> float:
    if slack >= 0.0:
        return (
            slack
            * period
            / (
                normalization["positive_slack_scale_ns"]
                * normalization["max_clock_period_ns"]
            )
        )
    return (
        slack
        / (
            normalization["negative_slack_scale_ns"]
            * period
        )
    )


def write_vivado_cut_net_map(
    ir_path: Path,
    assignment_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    assignment = read_json(assignment_path)
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError(
            f"assignment.schema: expected {PARTITION_ASSIGNMENT_SCHEMA!r}"
        )
    if assignment.get("design") != ir.value["design"]["name"]:
        raise ValidationError("assignment.design does not match EmuIR")
    net_index = {
        net["id"]: index for index, net in enumerate(ir.value["nets"])
    }
    cut_nets = sorted(
        {
            cut["net"]
            for cut in assignment.get("cut_nets", [])
            if isinstance(cut, dict) and isinstance(cut.get("net"), str)
        }
    )
    unknown = sorted(set(cut_nets) - set(net_index))
    if unknown:
        raise ValidationError(
            f"assignment cut nets are absent from EmuIR: {unknown[:10]}"
        )
    lines = [VIVADO_CUT_NET_MAP_HEADER]
    for cut_net in cut_nets:
        vivado_name = f"__emuflow_net_{net_index[cut_net]}"
        lines.append(
            f"{_hex_encode(vivado_name)}\t{_hex_encode(cut_net)}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "status": "pass",
        "design": assignment["design"],
        "cut_nets": len(cut_nets),
        "output": str(output_path),
    }


def write_vivado_net_map(
    ir_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        output.write(VIVADO_NET_MAP_HEADER + "\n")
        for index, net in enumerate(ir.value["nets"]):
            output.write(
                f"{_hex_encode(f'__emuflow_net_{index}')}\t"
                f"{_hex_encode(net['id'])}\n"
            )
    return {
        "status": "pass",
        "design": ir.value["design"]["name"],
        "nets": len(ir.value["nets"]),
        "output": str(output_path),
    }


def import_vivado_path_database_tsv(
    input_path: Path,
    ir_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    known_nets = {net["id"] for net in ir.value["nets"]}
    lines = input_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != VIVADO_PATH_DATABASE_TSV_HEADER:
        raise ValidationError("Vivado path database TSV: invalid header")
    paths = []
    path_ids = set()
    for index, line in enumerate(lines[1:], start=2):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 6:
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                "expected six fields"
            )
        path_id = _hex_decode(
            fields[0],
            f"Vivado path database TSV line {index} path",
        )
        clock_domain = _hex_decode(
            fields[1],
            f"Vivado path database TSV line {index} clock",
        )
        if not path_id or path_id in path_ids:
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                "invalid or duplicate path"
            )
        path_ids.add(path_id)
        try:
            clock_period = float(fields[2])
            slack = float(fields[3])
            fixed_delay = float(fields[4])
        except ValueError as error:
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                "invalid numeric field"
            ) from error
        if clock_period <= 0.0 or fixed_delay < 0.0:
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                "invalid period/delay"
            )
        raw_nets = fields[5].split(",")
        if not raw_nets or any(not item for item in raw_nets):
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                "empty path-net list"
            )
        path_nets = [
            _hex_decode(
                item,
                f"Vivado path database TSV line {index} path_nets_hex",
            )
            for item in raw_nets
        ]
        if len(set(path_nets)) != len(path_nets):
            raise ValidationError(
                f"Vivado path database TSV line {index}: duplicate net"
            )
        unknown = sorted(set(path_nets) - known_nets)
        if unknown:
            raise ValidationError(
                f"Vivado path database TSV line {index}: "
                f"unknown EmuIR nets {unknown}"
            )
        paths.append(
            {
                "id": path_id,
                "clock_domain": clock_domain,
                "clock_period_ns": clock_period,
                "slack_ns": slack,
                "fixed_delay_ns": fixed_delay,
                "path_nets": path_nets,
            }
        )
    if not paths:
        raise ValidationError(
            "Vivado path database TSV contains no mapped timing paths"
        )
    normalization = _database_normalization(paths)
    for path in paths:
        path["normalized_slack"] = _normalized_slack(
            path["clock_period_ns"],
            path["slack_ns"],
            normalization,
        )
    artifact = {
        "schema": STA_PATH_DATABASE_SCHEMA,
        "design": ir.value["design"]["name"],
        "source": {
            "provider": "vivado-get-timing-path-database-v1",
            "input": str(input_path),
        },
        "normalization": normalization,
        "paths": paths,
    }
    write_json(output_path, artifact)
    return {
        "status": "pass",
        "design": artifact["design"],
        "paths": len(paths),
        "unique_path_nets": len(
            {net for path in paths for net in path["path_nets"]}
        ),
        "output": str(output_path),
    }


def project_sta_path_database(
    database_path: Path,
    assignment_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    database = read_json(database_path)
    assignment = read_json(assignment_path)
    if database.get("schema") != STA_PATH_DATABASE_SCHEMA:
        raise ValidationError("STA path database schema is invalid")
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError(
            f"assignment.schema: expected {PARTITION_ASSIGNMENT_SCHEMA!r}"
        )
    if database.get("design") != assignment.get("design"):
        raise ValidationError(
            "STA path database design does not match assignment"
        )
    cut_nets = {
        cut["net"]
        for cut in assignment.get("cut_nets", [])
        if isinstance(cut, dict) and isinstance(cut.get("net"), str)
    }
    if not cut_nets:
        raise ValidationError(
            "STA path projection requires partition cut nets"
        )
    normalization = _validate_database_normalization(
        database.get("normalization")
    )
    cut_by_net = {
        cut["net"]: cut
        for cut in assignment.get("cut_nets", [])
        if isinstance(cut, dict)
        and isinstance(cut.get("net"), str)
        and cut["net"] in cut_nets
    }
    if set(cut_by_net) != cut_nets:
        raise ValidationError("partition cut-net records are invalid")
    cut_signature_by_net = {}
    for net in sorted(cut_by_net):
        cut = cut_by_net[net]
        sources = cut.get("source_fpgas", [])
        sinks = cut.get("sink_fpgas", [])
        if (
            not isinstance(sources, list)
            or not all(isinstance(item, str) and item for item in sources)
            or not isinstance(sinks, list)
            or not all(isinstance(item, str) and item for item in sinks)
        ):
            raise ValidationError(
                f"partition cut-net {net!r} endpoint lists are invalid"
            )
        cut_signature_by_net[net] = (
            f"{','.join(sources)}->{','.join(sinks)}"
        )
    paths = []
    database_records = []
    covered_cut_nets = set()
    path_ids = set()
    raw_paths = database.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValidationError("STA path database paths are invalid")
    for index, path in enumerate(raw_paths):
        if not isinstance(path, dict):
            raise ValidationError(
                f"STA path database paths[{index}] is invalid"
            )
        path_id = path.get("id")
        clock_domain = path.get("clock_domain")
        clock_period = path.get("clock_period_ns")
        slack = path.get("slack_ns")
        fixed_delay = path.get("fixed_delay_ns")
        if (
            not isinstance(path_id, str)
            or not path_id
            or path_id in path_ids
            or not isinstance(clock_domain, str)
            or not clock_domain
        ):
            raise ValidationError(
                f"STA path database paths[{index}] identity is invalid"
            )
        path_ids.add(path_id)
        for name, value in (
            ("clock_period_ns", clock_period),
            ("slack_ns", slack),
            ("fixed_delay_ns", fixed_delay),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValidationError(
                    f"STA path database paths[{index}].{name} is invalid"
                )
        if float(clock_period) <= 0.0 or float(fixed_delay) < 0.0:
            raise ValidationError(
                f"STA path database paths[{index}] timing is invalid"
            )
        path_nets = path.get("path_nets")
        if (
            not isinstance(path_nets, list)
            or not path_nets
            or not all(isinstance(net, str) and net for net in path_nets)
            or len(path_nets) != len(set(path_nets))
        ):
            raise ValidationError(
                f"STA path database paths[{index}].path_nets is invalid"
            )
        expected_normalized = _normalized_slack(
            float(clock_period),
            float(slack),
            normalization,
        )
        normalized = path.get("normalized_slack")
        if (
            isinstance(normalized, bool)
            or not isinstance(normalized, (int, float))
            or not math.isfinite(float(normalized))
            or abs(float(normalized) - expected_normalized) > 1.0e-12
        ):
            raise ValidationError(
                f"STA path database paths[{index}].normalized_slack "
                "is invalid"
            )
        database_records.append(
            {
                "clock_period_ns": float(clock_period),
                "slack_ns": float(slack),
            }
        )
        projected = [net for net in path_nets if net in cut_nets]
        if not projected:
            continue
        covered_cut_nets.update(projected)
        paths.append(
            {
                "id": path_id,
                "clock_domain": clock_domain,
                "clock_period_ns": float(clock_period),
                "slack_ns": float(slack),
                "fixed_delay_ns": float(fixed_delay),
                "cut_nets": projected,
                "cut_signature": [
                    cut_signature_by_net[net]
                    for net in projected
                ],
                "normalized_slack": expected_normalized,
                "compressed_path_ids": [path_id],
            }
        )
    if not paths:
        raise ValidationError(
            "STA path database has no path crossing this partition"
        )
    expected_normalization = _database_normalization(database_records)
    if any(
        abs(expected_normalization[key] - normalization[key]) > 1.0e-12
        for key in expected_normalization
    ):
        raise ValidationError(
            "STA path database normalization does not match its paths"
        )
    artifact = compress_sta_paths({
        "schema": STA_PATHS_SCHEMA,
        "design": assignment["design"],
        "source": {
            "provider": "partition-projected-sta-paths-v1",
            "input": str(database_path),
        },
        "normalization": normalization,
        "paths": paths,
    })
    write_json(output_path, artifact)
    return {
        "status": "pass",
        "design": assignment["design"],
        "database_paths": len(raw_paths),
        "projected_paths": len(paths),
        "compressed_paths": len(artifact["paths"]),
        "cut_nets": len(cut_nets),
        "covered_cut_nets": len(covered_cut_nets),
        "uncovered_cut_nets": len(cut_nets - covered_cut_nets),
        "output": str(output_path),
    }


def import_vivado_sta_tsv(
    input_path: Path,
    assignment_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    assignment = read_json(assignment_path)
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError(
            f"assignment.schema: expected {PARTITION_ASSIGNMENT_SCHEMA!r}"
        )
    valid_cut_nets = {
        cut["net"]
        for cut in assignment.get("cut_nets", [])
        if isinstance(cut, dict) and isinstance(cut.get("net"), str)
    }
    lines = input_path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != VIVADO_STA_TSV_HEADER:
        raise ValidationError("Vivado STA TSV: invalid header")
    paths = []
    path_ids = set()
    for index, line in enumerate(lines[1:], start=2):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 6:
            raise ValidationError(
                f"Vivado STA TSV line {index}: expected six fields"
            )
        path_id = _hex_decode(fields[0], f"Vivado STA TSV line {index} path")
        clock_domain = _hex_decode(
            fields[1], f"Vivado STA TSV line {index} clock"
        )
        if not path_id or path_id in path_ids:
            raise ValidationError(
                f"Vivado STA TSV line {index}: invalid or duplicate path"
            )
        path_ids.add(path_id)
        try:
            clock_period = float(fields[2])
            slack = float(fields[3])
            fixed_delay = float(fields[4])
        except ValueError as error:
            raise ValidationError(
                f"Vivado STA TSV line {index}: invalid numeric field"
            ) from error
        if clock_period <= 0.0 or fixed_delay < 0.0:
            raise ValidationError(
                f"Vivado STA TSV line {index}: invalid period/delay"
            )
        raw_cut_nets = fields[5].split(",")
        if not raw_cut_nets or any(not item for item in raw_cut_nets):
            raise ValidationError(
                f"Vivado STA TSV line {index}: empty cut-net list"
            )
        cut_nets = [
            _hex_decode(
                item, f"Vivado STA TSV line {index} cut_nets_hex"
            )
            for item in raw_cut_nets
        ]
        if len(set(cut_nets)) != len(cut_nets):
            raise ValidationError(
                f"Vivado STA TSV line {index}: duplicate cut net"
            )
        unknown = sorted(set(cut_nets) - valid_cut_nets)
        if unknown:
            raise ValidationError(
                f"Vivado STA TSV line {index}: unknown cut nets {unknown}"
            )
        paths.append(
            {
                "id": path_id,
                "clock_domain": clock_domain,
                "clock_period_ns": clock_period,
                "slack_ns": slack,
                "fixed_delay_ns": fixed_delay,
                "cut_nets": cut_nets,
            }
        )
    if not paths:
        raise ValidationError("Vivado STA TSV contains no cut timing paths")
    artifact = {
        "schema": STA_PATHS_SCHEMA,
        "design": assignment["design"],
        "source": {
            "provider": "vivado-get-timing-paths-v1",
            "input": str(input_path),
        },
        "paths": paths,
    }
    write_json(output_path, artifact)
    return {
        "status": "pass",
        "design": assignment["design"],
        "paths": len(paths),
        "unique_cut_nets": len(
            {net for path in paths for net in path["cut_nets"]}
        ),
        "output": str(output_path),
    }
