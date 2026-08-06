"""Tool-backed elaboration gate for generated shells and serial PHY sources."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .platform import Platform
from .serial_phy_provider import validate_serial_phy_provider


SERIAL_PHY_ELABORATION_SCHEMA = "emuflow.serial-phy-elaboration/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yosys_quote(path: Path) -> str:
    value = str(path)
    if any(character in value for character in " \t\n\"\\"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _sv_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"n_{name}" if not name or name[0].isdigit() else name


def build_yosys_elaboration_script(
    sources: Sequence[Path], top: str
) -> str:
    if not sources:
        raise ValidationError("serial PHY elaboration source list is empty")
    if not isinstance(top, str) or not top:
        raise ValidationError("serial PHY elaboration top is invalid")
    return "; ".join(
        [
            "read_verilog -sv " + " ".join(_yosys_quote(path) for path in sources),
            f"hierarchy -check -top {top}",
            "check -assert",
            "stat",
        ]
    )


def run_serial_phy_elaboration(
    *,
    platform_path: Path,
    provider_manifest_path: Path,
    phase6c_dir: Path,
    runtime_controller_path: Path,
    transport_rtl_paths: Mapping[str, Path],
    yosys_executable: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    provider_raw = read_json(provider_manifest_path)
    provider_result = validate_serial_phy_provider(
        provider_raw, provider_manifest_path, platform
    )
    provider = provider_result["normalized"]
    manifest_path = phase6c_dir / "serial_wrapper_manifest.json"
    report_path = phase6c_dir / "phase6c_report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise ValidationError("Phase 6C manifest/report is missing")
    manifest = read_json(manifest_path)
    phase6c_report = read_json(report_path)
    if (
        manifest.get("schema") != "emuflow.serial-wrapper/v1"
        or manifest.get("status") != "provider_source_bound"
        or phase6c_report.get("schema") != "emuflow.phase6c-report/v1"
        or phase6c_report.get("status") != "pass"
        or manifest.get("platform") != platform.name
        or phase6c_report.get("platform") != platform.name
        or manifest.get("design") != phase6c_report.get("design")
    ):
        raise ValidationError("Phase 6C provider-bound identity is invalid")
    expected_provider_hash = hashlib.sha256(
        provider_manifest_path.read_bytes()
    ).hexdigest()
    if manifest.get("phy_provider_manifest_sha256") != expected_provider_hash:
        raise ValidationError("Phase 6C/provider manifest hash mismatch")
    bound_provider = manifest.get("phy_contract", {}).get("provider", {})
    if (
        bound_provider.get("status") != "source_inventory_bound"
        or bound_provider.get("id") != provider["id"]
        or bound_provider.get("sources") != provider["sources"]
    ):
        raise ValidationError("Phase 6C provider source inventory mismatch")
    fpga_records = manifest.get("fpgas")
    if not isinstance(fpga_records, list):
        raise ValidationError("Phase 6C FPGA wrapper inventory is invalid")
    fpga_ids = {fpga.id for fpga in platform.fpgas}
    if (
        {record.get("fpga") for record in fpga_records} != fpga_ids
        or set(transport_rtl_paths) != fpga_ids
    ):
        raise ValidationError(
            "provider elaboration must cover every FPGA transport exactly once"
        )
    if not runtime_controller_path.is_file():
        raise ValidationError("runtime controller RTL is missing")
    if not yosys_executable.is_file():
        raise ValidationError("Yosys elaboration executable is missing")

    source_root = (provider_manifest_path.parent / provider["source_root"]).resolve()
    provider_hdl = [
        (source_root / source["path"]).resolve()
        for source in provider["sources"]
        if source["language"] in {"systemverilog", "verilog"}
    ]
    if not provider_hdl:
        raise ValidationError("provider has no HDL source for elaboration")
    output_dir.mkdir(parents=True, exist_ok=True)
    version = subprocess.run(
        [str(yosys_executable), "-V"],
        check=False,
        capture_output=True,
        text=True,
    )
    if version.returncode != 0:
        raise EmuFlowError("failed to query Yosys version")
    fpga_results = []
    for record in sorted(fpga_records, key=lambda item: item["fpga"]):
        fpga = record["fpga"]
        wrapper_path = phase6c_dir / record["rtl"]
        shell_name = record.get("integration_shell")
        transport_path = transport_rtl_paths[fpga]
        if (
            not isinstance(shell_name, str)
            or not wrapper_path.is_file()
            or not (phase6c_dir / shell_name).is_file()
            or not transport_path.is_file()
        ):
            raise ValidationError(f"{fpga}: elaboration RTL input is missing")
        shell_path = phase6c_dir / shell_name
        sources = [
            *provider_hdl,
            runtime_controller_path,
            transport_path,
            wrapper_path,
            shell_path,
        ]
        top = f"emuflow_partition_shell_{_sv_name(fpga)}"
        script = build_yosys_elaboration_script(sources, top)
        completed = subprocess.run(
            [str(yosys_executable), "-q", "-p", script],
            check=False,
            capture_output=True,
            text=True,
        )
        log_path = output_dir / f"{fpga}.yosys.log"
        log_path.write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise EmuFlowError(
                f"{fpga}: provider elaboration failed: {detail[-1000:]}"
            )
        fpga_results.append(
            {
                "fpga": fpga,
                "top": top,
                "status": "pass",
                "sources": [
                    {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
                    for path in sources
                ],
                "log": log_path.name,
                "log_sha256": _sha256(log_path),
            }
        )
    report = {
        "schema": SERIAL_PHY_ELABORATION_SCHEMA,
        "status": "pass",
        "qualification": "open_rtl_elaboration_only",
        "design": manifest["design"],
        "platform": platform.name,
        "provider": provider["id"],
        "provider_qualification": provider["qualification"],
        "tool": {
            "name": "yosys",
            "version": version.stdout.strip(),
            "executable": str(yosys_executable),
        },
        "phase6c_manifest_sha256": _sha256(manifest_path),
        "provider_manifest_sha256": expected_provider_hash,
        "runtime_controller_sha256": _sha256(runtime_controller_path),
        "fpgas": fpga_results,
        "validation": {
            "fpgas": len(fpga_results),
            "elaboration_failures": 0,
            "hardware_release_authorized": False,
        },
    }
    write_json(output_dir / "serial_phy_elaboration_report.json", report)
    return report
