from pathlib import Path
from typing import Any, Dict, Mapping

from .equivalence import simulate_partition_equivalence
from .io import read_json, write_json
from .ir import EmuIR
from .netlist import (
    anchors_to_xdc_template,
    build_split_artifacts,
    transport_to_systemverilog,
    validate_split_artifacts,
)
from .platform import Platform
from .runtime import virtual_runtime_controller_to_systemverilog


PHASE6_REPORT_SCHEMA = "emuflow.phase6-report/v1"


def _load_artifacts(root: Path, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "manifest": dict(manifest),
        "lane_map": read_json(root / manifest["lane_map"]),
        "netlists": {
            item["fpga"]: read_json(root / item["netlist"])
            for item in manifest["fpgas"]
        },
        "transports": {
            item["fpga"]: read_json(root / item["transport"])
            for item in manifest["fpgas"]
        },
        "anchors": {
            item["fpga"]: read_json(root / item["virtual_anchors"])
            for item in manifest["fpgas"]
        },
    }


def run_phase6(
    ir_path: Path,
    assignment_path: Path,
    schedule_path: Path,
    platform_path: Path,
    output_dir: Path,
    equivalence_cycles: int = 16,
    equivalence_seed: int = 20260727,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    assignment = read_json(assignment_path)
    schedule = read_json(schedule_path)
    platform = Platform.load(platform_path)
    artifacts = build_split_artifacts(ir, assignment, schedule, platform)
    validation = validate_split_artifacts(
        ir, assignment, schedule, platform, artifacts
    )
    equivalence = simulate_partition_equivalence(
        ir,
        assignment,
        schedule,
        cycles=equivalence_cycles,
        seed=equivalence_seed,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "manifest.json", artifacts["manifest"])
    write_json(output_dir / "lane_map.json", artifacts["lane_map"])
    (output_dir / "virtual_runtime_controller.sv").write_text(
        virtual_runtime_controller_to_systemverilog(),
        encoding="utf-8",
    )
    for item in artifacts["manifest"]["fpgas"]:
        fpga_id = item["fpga"]
        fpga_root = output_dir / fpga_id
        write_json(fpga_root / "netlist.json", artifacts["netlists"][fpga_id])
        write_json(
            fpga_root / "transport.json", artifacts["transports"][fpga_id]
        )
        write_json(
            fpga_root / "virtual_anchors.json",
            artifacts["anchors"][fpga_id],
        )
        (fpga_root / "transport_schedule.sv").write_text(
            transport_to_systemverilog(
                artifacts["transports"][fpga_id], platform
            ),
            encoding="utf-8",
        )
        (fpga_root / "virtual_anchors.xdc.template").write_text(
            anchors_to_xdc_template(artifacts["anchors"][fpga_id]),
            encoding="utf-8",
        )
    report = {
        "schema": PHASE6_REPORT_SCHEMA,
        "phase": 6,
        "increment": "board-independent-netlist-and-lane-planning",
        "status": "pass",
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "provider": artifacts["manifest"]["provider"],
        "validation": validation,
        "equivalence": equivalence,
        "board_binding": artifacts["manifest"]["board_binding"],
        "artifacts": {
            "manifest": "manifest.json",
            "lane_map": "lane_map.json",
            "runtime_controller_rtl": "virtual_runtime_controller.sv",
            "report": "phase6_report.json",
        },
    }
    write_json(output_dir / "phase6_report.json", report)
    return report


def validate_phase6(
    ir_path: Path,
    assignment_path: Path,
    schedule_path: Path,
    platform_path: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    assignment = read_json(assignment_path)
    schedule = read_json(schedule_path)
    platform = Platform.load(platform_path)
    manifest = read_json(manifest_path)
    artifacts = _load_artifacts(manifest_path.parent, manifest)
    return validate_split_artifacts(
        ir, assignment, schedule, platform, artifacts
    )
