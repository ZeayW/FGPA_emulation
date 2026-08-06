"""Derive serial-transceiver sites from source-backed package pins."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .platform import Platform


VIVADO_PIN_SITE_MAP_SCHEMA = "emuflow.vivado-pin-site-map/v1"
_PIN_NAME = re.compile(r"[A-Za-z0-9_]+")
_EXPECTED_FUNCTION = {
    "tx_p": re.compile(r"MGT[YH]TXP\d+_\d+"),
    "tx_n": re.compile(r"MGT[YH]TXN\d+_\d+"),
    "rx_p": re.compile(r"MGT[YH]RXP\d+_\d+"),
    "rx_n": re.compile(r"MGT[YH]RXN\d+_\d+"),
}
_GT_SITE = re.compile(r"GT[YH]E4_CHANNEL_X\d+Y\d+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tcl_quote(value: str) -> str:
    return "{" + value.replace("\\", "/").replace("}", "\\}") + "}"


def collect_serial_pin_inventory(
    platform: Platform,
) -> Tuple[Dict[str, set[str]], list[Dict[str, Any]]]:
    parts = {fpga.id: fpga.part for fpga in platform.fpgas}
    pins_by_part: Dict[str, set[str]] = defaultdict(set)
    lane_records = []
    for link in sorted(platform.links, key=lambda item: item.id):
        if link.mode != "serial" or not link.endpoint_bindings:
            continue
        for endpoint in sorted(link.endpoint_bindings, key=lambda item: item.fpga):
            part = parts[endpoint.fpga]
            for lane in endpoint.lanes:
                pins = {
                    "tx_p": lane.tx_package_pin_p,
                    "tx_n": lane.tx_package_pin_n,
                    "rx_p": lane.rx_package_pin_p,
                    "rx_n": lane.rx_package_pin_n,
                }
                if any(_PIN_NAME.fullmatch(pin) is None for pin in pins.values()):
                    raise ValidationError("serial package-pin name is not Tcl-safe")
                pins_by_part[part].update(pins.values())
                lane_records.append(
                    {
                        "fpga": endpoint.fpga,
                        "part": part,
                        "link": link.id,
                        "connector": endpoint.connector,
                        "mgt_group": endpoint.mgt,
                        "physical_lane": lane.lane,
                        "package_pins": pins,
                    }
                )
    if not lane_records:
        raise ValidationError("BoardDB has no bound serial-transceiver lanes")
    return dict(pins_by_part), lane_records


def build_vivado_pin_site_tcl(
    *, part: str, pins: Sequence[str], probe_rtl: Path, report_path: Path
) -> str:
    if not part or not pins or len(set(pins)) != len(pins):
        raise ValidationError("Vivado package-pin query inventory is invalid")
    if any(_PIN_NAME.fullmatch(pin) is None for pin in pins):
        raise ValidationError("Vivado package-pin query contains an invalid pin")
    pin_list = " ".join(_tcl_quote(pin) for pin in sorted(pins))
    return "\n".join(
        [
            "create_project -in_memory -part "
            + _tcl_quote(part)
            + " emuflow_pin_site_query",
            "read_verilog " + _tcl_quote(str(probe_rtl)),
            "synth_design -rtl -mode out_of_context -top "
            "emuflow_pin_site_probe -part "
            + _tcl_quote(part),
            "set report_handle [open " + _tcl_quote(str(report_path)) + " w]",
            "foreach pin [list " + pin_list + "] {",
            "  set package_pin [get_package_pins -quiet $pin]",
            "  if {[llength $package_pin] != 1} {",
            "    puts stderr \"EMUFLOW_PIN_SITE pin=$pin package_pin_count="
            "[llength $package_pin]\"",
            "    exit 3",
            "  }",
            "  set sites [get_sites -quiet -of_objects $package_pin]",
            "  if {[llength $sites] != 1} {",
            "    puts stderr \"EMUFLOW_PIN_SITE pin=$pin site_count="
            "[llength $sites]\"",
            "    exit 4",
            "  }",
            "  puts $report_handle \"$pin\\t[get_property PIN_FUNC $package_pin]"
            "\\t[lindex $sites 0]\"",
            "}",
            "close $report_handle",
            "puts \"EMUFLOW_PIN_SITE status=pass pins="
            + str(len(pins))
            + " part="
            + part
            + "\"",
            "exit 0",
            "",
        ]
    )


def parse_vivado_pin_site_report(
    path: Path, expected_pins: Sequence[str]
) -> Dict[str, Dict[str, str]]:
    if not path.is_file():
        raise ValidationError("Vivado package-pin site report is missing")
    rows: Dict[str, Dict[str, str]] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = raw.split("\t")
        if len(fields) != 3 or any(not field for field in fields):
            raise ValidationError(
                f"{path}:{line_number}: malformed Vivado package-pin row"
            )
        pin, function, site = fields
        if pin in rows:
            raise ValidationError(f"{path}:{line_number}: duplicate package pin")
        rows[pin] = {"pin_function": function, "site": site}
    if set(rows) != set(expected_pins):
        raise ValidationError("Vivado package-pin site report coverage mismatch")
    return rows


def validate_lane_site_mapping(
    lane_records: Sequence[Mapping[str, Any]],
    rows_by_part: Mapping[str, Mapping[str, Mapping[str, str]]],
) -> list[Dict[str, Any]]:
    result = []
    for lane in lane_records:
        part_rows = rows_by_part.get(lane["part"])
        if part_rows is None:
            raise ValidationError("Vivado site result is missing a device part")
        sites = set()
        pin_functions = {}
        for role, pin in lane["package_pins"].items():
            row = part_rows.get(pin)
            if row is None:
                raise ValidationError("Vivado site result is missing a package pin")
            function = row["pin_function"]
            site = row["site"]
            if (
                not isinstance(function, str)
                or _EXPECTED_FUNCTION[role].fullmatch(function) is None
            ):
                raise ValidationError(
                    f"{lane['fpga']} {lane['link']} lane "
                    f"{lane['physical_lane']}: {role} pin function is inconsistent"
                )
            if not isinstance(site, str) or _GT_SITE.fullmatch(site) is None:
                raise ValidationError("package pin does not map to a GTHE4/GTYE4 site")
            sites.add(site)
            pin_functions[role] = function
        if len(sites) != 1:
            raise ValidationError(
                f"{lane['fpga']} {lane['link']} lane "
                f"{lane['physical_lane']}: differential pins span GT sites"
            )
        result.append(
            {
                **dict(lane),
                "site": sites.pop(),
                "pin_functions": pin_functions,
            }
        )
    return result


def validate_vivado_pin_site_map(
    value: Mapping[str, Any], platform: Platform
) -> Dict[str, Any]:
    if (
        value.get("schema") != VIVADO_PIN_SITE_MAP_SCHEMA
        or value.get("status") != "pass"
        or value.get("qualification")
        != "vendor_device_db_derived_from_source_backed_package_pins"
        or value.get("platform") != platform.name
    ):
        raise ValidationError("Vivado package-pin site map identity is invalid")
    records = value.get("transceiver_sites")
    if not isinstance(records, list) or any(
        not isinstance(record, dict) for record in records
    ):
        raise ValidationError("Vivado package-pin site inventory is invalid")
    _, expected_lanes = collect_serial_pin_inventory(platform)
    key_fields = ("fpga", "link", "connector", "mgt_group", "physical_lane")
    expected_by_key = {
        tuple(record[field] for field in key_fields): record
        for record in expected_lanes
    }
    actual_by_key = {}
    rows_by_part: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)
    for record in records:
        key = tuple(record.get(field) for field in key_fields)
        expected = expected_by_key.get(key)
        if key in actual_by_key or expected is None:
            raise ValidationError("Vivado package-pin site lane identity is invalid")
        if (
            record.get("part") != expected["part"]
            or record.get("package_pins") != expected["package_pins"]
            or not isinstance(record.get("pin_functions"), dict)
            or not isinstance(record.get("site"), str)
        ):
            raise ValidationError("Vivado package-pin site lane payload mismatch")
        for role, pin in expected["package_pins"].items():
            row = {
                "pin_function": record["pin_functions"].get(role),
                "site": record["site"],
            }
            prior = rows_by_part[expected["part"]].get(pin)
            if prior is not None and prior != row:
                raise ValidationError("Vivado package-pin result is inconsistent")
            rows_by_part[expected["part"]][pin] = row
        actual_by_key[key] = record
    if set(actual_by_key) != set(expected_by_key):
        raise ValidationError("Vivado package-pin site lane coverage mismatch")
    normalized_sites = validate_lane_site_mapping(expected_lanes, rows_by_part)
    normalized = dict(value)
    normalized["transceiver_sites"] = normalized_sites
    return normalized


def validate_vivado_pin_site_map_file(
    *, platform_path: Path, site_map_path: Path
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    value = read_json(site_map_path)
    if value.get("platform_sha256") != _sha256(platform_path):
        raise ValidationError("Vivado package-pin site map BoardDB hash mismatch")
    return validate_vivado_pin_site_map(value, platform)


def derive_vivado_pin_sites(
    *,
    platform_path: Path,
    vivado_executable: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    if platform.kind != "hardware":
        raise ValidationError("Vivado package-pin derivation requires hardware BoardDB")
    if not vivado_executable.is_file():
        raise ValidationError("Vivado executable is missing")
    pins_by_part, lane_records = collect_serial_pin_inventory(platform)
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_rtl = output_dir / "emuflow_pin_site_probe.sv"
    probe_rtl.write_text(
        "module emuflow_pin_site_probe(input wire probe);\nendmodule\n",
        encoding="utf-8",
    )
    version = subprocess.run(
        [str(vivado_executable), "-version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if version.returncode != 0:
        raise EmuFlowError("failed to query Vivado version")
    rows_by_part = {}
    part_artifacts = []
    for index, (part, pin_set) in enumerate(sorted(pins_by_part.items())):
        stem = f"part_{index}"
        report_path = output_dir / f"{stem}.pin_sites.tsv"
        script_path = output_dir / f"{stem}.pin_sites.tcl"
        log_path = output_dir / f"{stem}.vivado.log"
        script_path.write_text(
            build_vivado_pin_site_tcl(
                part=part,
                pins=sorted(pin_set),
                probe_rtl=probe_rtl,
                report_path=report_path,
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                str(vivado_executable),
                "-mode",
                "batch",
                "-nojournal",
                "-nolog",
                "-source",
                str(script_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise EmuFlowError(
                f"Vivado package-pin derivation failed for {part}: {detail[-1000:]}"
            )
        rows_by_part[part] = parse_vivado_pin_site_report(
            report_path, sorted(pin_set)
        )
        part_artifacts.append(
            {
                "part": part,
                "package_pins": len(pin_set),
                "sites": len(
                    {row["site"] for row in rows_by_part[part].values()}
                ),
                "script": script_path.name,
                "script_sha256": _sha256(script_path),
                "raw_report": report_path.name,
                "raw_report_sha256": _sha256(report_path),
                "log": log_path.name,
                "log_sha256": _sha256(log_path),
            }
        )
    site_records = validate_lane_site_mapping(lane_records, rows_by_part)
    report = {
        "schema": VIVADO_PIN_SITE_MAP_SCHEMA,
        "status": "pass",
        "qualification": (
            "vendor_device_db_derived_from_source_backed_package_pins"
        ),
        "platform": platform.name,
        "platform_sha256": _sha256(platform_path),
        "tool": {
            "name": "vivado",
            "version": (version.stdout + version.stderr).strip(),
            "executable": str(vivado_executable),
        },
        "probe_rtl": probe_rtl.name,
        "probe_rtl_sha256": _sha256(probe_rtl),
        "parts": part_artifacts,
        "transceiver_sites": site_records,
        "validation": {
            "fpgas": len({record["fpga"] for record in site_records}),
            "endpoint_lanes": len(site_records),
            "package_pins": sum(len(pins) for pins in pins_by_part.values()),
            "pin_function_mismatches": 0,
            "cross_site_lane_mismatches": 0,
        },
    }
    write_json(output_dir / "vivado_pin_site_map.json", report)
    return report
