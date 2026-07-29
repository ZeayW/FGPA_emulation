"""STA extraction adapters with stable EmuIR cut-net identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .partition import PARTITION_ASSIGNMENT_SCHEMA
from .timing_routing import STA_PATHS_SCHEMA


VIVADO_STA_TSV_HEADER = (
    "path_id_hex\tclock_domain_hex\tclock_period_ns\tslack_ns\t"
    "fixed_delay_ns\tcut_nets_hex"
)
VIVADO_CUT_NET_MAP_HEADER = "vivado_net_hex\tcut_net_hex"


def _hex_encode(value: str) -> str:
    return value.encode("utf-8").hex()


def _hex_decode(value: str, context: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValidationError(f"{context}: invalid UTF-8 hex") from error


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
