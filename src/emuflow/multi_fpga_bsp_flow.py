"""Checked hardware-BSP continuation for a completed multi-FPGA compile."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .physical_pins import run_phase6b
from .platform import Platform
from .runtime_sync import run_runtime_sync_materialization
from .serial_phy_elaboration import run_serial_phy_elaboration
from .serial_wrapper import run_phase6c
from .vivado_pin_sites import (
    derive_vivado_pin_sites,
    validate_vivado_pin_site_map_file,
)


MULTI_FPGA_BSP_FLOW_SCHEMA = "emuflow.multi-fpga-bsp-flow/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_multi_fpga_bsp_flow_report(
    report: Dict[str, Any],
) -> Dict[str, Any]:
    if report.get("schema") != MULTI_FPGA_BSP_FLOW_SCHEMA:
        raise ValidationError("multi-FPGA BSP flow report schema is invalid")
    if report.get("status") != "pass":
        raise ValidationError("multi-FPGA BSP flow did not pass")
    stages = report.get("stages")
    expected = {"phase6b", "runtime_sync", "phase6c", "phy_elaboration"}
    if not isinstance(stages, dict) or set(stages) != expected:
        raise ValidationError("multi-FPGA BSP flow stages are incomplete")
    for name in sorted(expected):
        if not isinstance(stages[name], dict) or stages[name].get("status") != "pass":
            raise ValidationError(f"multi-FPGA BSP stage {name!r} did not pass")
    design = report.get("design")
    platform = report.get("platform")
    for name in ("phase6b", "phase6c", "phy_elaboration"):
        stage = stages[name]
        if stage.get("design") != design or stage.get("platform") != platform:
            raise ValidationError(
                f"multi-FPGA BSP stage {name!r} identity disagrees"
            )
    validation = report.get("validation")
    if (
        not isinstance(validation, dict)
        or validation.get("hardware_release_authorized") is not False
        or validation.get("elaboration_failures") != 0
    ):
        raise ValidationError("multi-FPGA BSP release boundary is invalid")
    return {
        "status": "pass",
        "design": design,
        "platform": platform,
        "hardware_release_status": report.get("hardware_release_status"),
        "elaboration_tool": stages["phy_elaboration"]["tool"]["name"],
    }


def run_multi_fpga_bsp_flow(
    *,
    flow_root: Path,
    platform_path: Path,
    phy_provider_path: Path,
    runtime_sync_provider_path: Path,
    output_dir: Path,
    board_overlay_path: Optional[Path] = None,
    gt_site_map_path: Optional[Path] = None,
    vivado_executable: Optional[Path] = None,
    yosys_executable: Optional[Path] = None,
    runtime_sync_root: Optional[str] = None,
    ready_stable_cycles: int = 4,
) -> Dict[str, Any]:
    """Continue an existing board-independent result through Phase 6B/6C."""

    flow_root = flow_root.resolve()
    platform_path = platform_path.resolve()
    phy_provider_path = phy_provider_path.resolve()
    runtime_sync_provider_path = runtime_sync_provider_path.resolve()
    output_dir = output_dir.resolve()
    if (vivado_executable is None) == (yosys_executable is None):
        raise ValidationError("select exactly one BSP elaboration tool")
    if output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    ):
        raise EmuFlowError(
            f"multi-FPGA BSP output path must be empty: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    platform = Platform.load(platform_path)
    flow_report_path = flow_root / "board-independent-flow-report.json"
    if not flow_report_path.is_file():
        flow_report_path = flow_root / "multi-fpga-flow-report.json"
    schedule_path = flow_root / "tdm/schedule.json"
    phase6_report_path = flow_root / "split/phase6_report.json"
    if not all(
        path.is_file()
        for path in (flow_report_path, schedule_path, phase6_report_path)
    ):
        raise ValidationError("completed multi-FPGA compile artifacts are missing")
    flow_report = read_json(flow_report_path)
    schedule = read_json(schedule_path)
    phase6_report = read_json(phase6_report_path)
    # Local import avoids a module-level cycle while still requiring the
    # source compilation report to pass its complete independent validator.
    from .multi_fpga_flow import validate_multi_fpga_flow_report

    flow_validation = validate_multi_fpga_flow_report(flow_report)
    if (
        schedule.get("platform") != platform.name
        or phase6_report.get("platform") != platform.name
        or schedule.get("design") != phase6_report.get("design")
        or flow_validation.get("platform") != platform.name
        or flow_validation.get("design") != schedule.get("design")
    ):
        raise ValidationError("multi-FPGA compile/BSP identity is inconsistent")
    design = schedule["design"]

    anchor_paths = {
        fpga.id: flow_root / "split" / fpga.id / "virtual_anchors.json"
        for fpga in platform.fpgas
    }
    transport_json_paths = {
        fpga.id: flow_root / "split" / fpga.id / "transport.json"
        for fpga in platform.fpgas
    }
    transport_rtl_paths = {
        fpga.id: flow_root / "split" / fpga.id / "transport_schedule.sv"
        for fpga in platform.fpgas
    }
    runtime_controller = flow_root / "split/virtual_runtime_controller.sv"
    required = [
        *anchor_paths.values(),
        *transport_json_paths.values(),
        *transport_rtl_paths.values(),
        runtime_controller,
    ]
    if any(not path.is_file() for path in required):
        raise ValidationError("Phase 6 BSP/transport artifact inventory is incomplete")

    phase6b_root = output_dir / "phase6b"
    phase6b_report = run_phase6b(
        schedule_path,
        platform_path,
        None,
        None,
        anchor_paths,
        None,
        phase6b_root,
    )
    runtime_sync_root_dir = output_dir / "runtime-sync"
    runtime_sync_report = run_runtime_sync_materialization(
        platform_path,
        runtime_sync_provider_path,
        runtime_sync_root_dir,
        root=runtime_sync_root,
        ready_stable_cycles=ready_stable_cycles,
    )

    site_map_report = None
    effective_site_map = gt_site_map_path.resolve() if gt_site_map_path else None
    if effective_site_map is not None:
        site_map_report = validate_vivado_pin_site_map_file(
            platform_path=platform_path, site_map_path=effective_site_map
        )
    elif vivado_executable is not None:
        site_map_root = output_dir / "gt-site-map"
        site_map_report = derive_vivado_pin_sites(
            platform_path=platform_path,
            vivado_executable=vivado_executable.resolve(),
            output_dir=site_map_root,
        )
        effective_site_map = site_map_root / "vivado_pin_site_map.json"

    phase6c_root = output_dir / "phase6c"
    phase6c_report = run_phase6c(
        platform_path,
        phase6b_root / "package_pin_binding.json",
        phase6c_root,
        transport_paths=transport_json_paths,
        board_overlay_path=board_overlay_path,
        phy_provider_path=phy_provider_path,
        gt_site_map_path=effective_site_map,
        runtime_sync_topology_path=(
            runtime_sync_root_dir / "runtime_sync_topology.json"
        ),
        runtime_sync_provider_path=runtime_sync_provider_path,
    )
    elaboration_root = output_dir / "phy-elaboration"
    elaboration_report = run_serial_phy_elaboration(
        platform_path=platform_path,
        provider_manifest_path=phy_provider_path,
        phase6c_dir=phase6c_root,
        runtime_controller_path=runtime_controller,
        transport_rtl_paths=transport_rtl_paths,
        yosys_executable=(
            yosys_executable.resolve() if yosys_executable is not None else None
        ),
        vivado_executable=(
            vivado_executable.resolve() if vivado_executable is not None else None
        ),
        output_dir=elaboration_root,
    )
    report = {
        "schema": MULTI_FPGA_BSP_FLOW_SCHEMA,
        "status": "pass",
        "design": design,
        "platform": platform.name,
        "qualification": "source_bound_bsp_structure_validation",
        "hardware_release_status": phase6c_report["hardware_release_status"],
        "source_flow_report_sha256": _sha256(flow_report_path),
        "stages": {
            "phase6b": phase6b_report,
            "runtime_sync": runtime_sync_report,
            "phase6c": phase6c_report,
            "phy_elaboration": elaboration_report,
        },
        "validation": {
            "fpgas": len(platform.fpgas),
            "elaboration_failures": elaboration_report["validation"][
                "elaboration_failures"
            ],
            "hardware_release_authorized": False,
            "gt_site_map_status": (
                site_map_report["status"]
                if site_map_report is not None
                else "not-provided"
            ),
        },
        "artifacts": {
            "phase6b": "phase6b/phase6b_report.json",
            "runtime_sync": "runtime-sync/runtime_sync_topology.json",
            "phase6c": "phase6c/phase6c_report.json",
            "phy_elaboration": (
                "phy-elaboration/serial_phy_elaboration_report.json"
            ),
            **(
                {"gt_site_map": "phase6c/vivado_pin_site_map.bound.json"}
                if effective_site_map is not None
                else {}
            ),
        },
    }
    validation = validate_multi_fpga_bsp_flow_report(report)
    report["summary"] = validation
    write_json(output_dir / "multi-fpga-bsp-flow-report.json", report)
    return report
