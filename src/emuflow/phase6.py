from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .equivalence import simulate_partition_equivalence
from .errors import ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .netlist import (
    anchors_to_xdc_template,
    build_split_artifacts,
    transport_to_systemverilog,
    validate_split_artifacts,
)
from .platform import Platform
from .pin_planning import CHIMEW_PIN_PLAN_PROVIDER, validate_pin_plan
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
    pin_plan_path: Optional[Path] = None,
    position_hints_path: Optional[Path] = None,
    electrical_binding_path: Optional[Path] = None,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    assignment = read_json(assignment_path)
    schedule = read_json(schedule_path)
    platform = Platform.load(platform_path)
    pin_plan = read_json(pin_plan_path) if pin_plan_path is not None else None
    position_hints = (
        read_json(position_hints_path)
        if position_hints_path is not None
        else None
    )
    pin_validation = None
    electrical_validation = None
    electrical_binding = None
    if pin_plan is not None:
        if position_hints is None:
            raise ValueError(
                "position_hints_path is required with pin_plan_path"
            )
        pin_validation = validate_pin_plan(
            schedule,
            platform,
            position_hints,
            pin_plan,
        )
        if pin_plan.get("provider") == CHIMEW_PIN_PLAN_PROVIDER:
            if electrical_binding_path is None:
                raise ValueError(
                    "electrical_binding_path is required with a Chimew pin plan"
                )
            from .chimew_phase6 import validate_chimew_phase6_binding

            electrical_binding = read_json(electrical_binding_path)
            electrical_validation = validate_chimew_phase6_binding(
                schedule,
                platform,
                pin_plan,
                electrical_binding,
            )
        elif electrical_binding_path is not None:
            raise ValueError(
                "electrical_binding_path is supported only with a Chimew pin plan"
            )
    elif position_hints is not None:
        raise ValueError(
            "pin_plan_path is required with position_hints_path"
        )
    elif electrical_binding_path is not None:
        raise ValueError("pin_plan_path is required with electrical_binding_path")
    artifacts = build_split_artifacts(
        ir, assignment, schedule, platform, pin_plan
    )
    validation = validate_split_artifacts(
        ir, assignment, schedule, platform, artifacts, pin_plan
    )
    equivalence = simulate_partition_equivalence(
        ir,
        assignment,
        schedule,
        cycles=equivalence_cycles,
        seed=equivalence_seed,
    )
    if electrical_binding is not None:
        artifacts["manifest"]["electrical_binding"] = "electrical_binding.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "manifest.json", artifacts["manifest"])
    write_json(output_dir / "lane_map.json", artifacts["lane_map"])
    if pin_plan is not None:
        write_json(output_dir / "pin_plan.json", pin_plan)
        write_json(output_dir / "position_hints.json", position_hints)
        if electrical_binding_path is not None:
            write_json(
                output_dir / "electrical_binding.json",
                electrical_binding,
            )
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
        **(
            {"pin_plan_validation": pin_validation}
            if pin_validation is not None
            else {}
        ),
        **(
            {"electrical_binding_validation": electrical_validation}
            if electrical_validation is not None
            else {}
        ),
        "artifacts": {
            "manifest": "manifest.json",
            "lane_map": "lane_map.json",
            "runtime_controller_rtl": "virtual_runtime_controller.sv",
            "report": "phase6_report.json",
            **(
                {"pin_plan": "pin_plan.json"}
                if pin_plan is not None
                else {}
            ),
            **(
                {"position_hints": "position_hints.json"}
                if pin_plan is not None
                else {}
            ),
            **(
                {"electrical_binding": "electrical_binding.json"}
                if electrical_validation is not None
                else {}
            ),
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
    pin_plan_path: Optional[Path] = None,
    position_hints_path: Optional[Path] = None,
    electrical_binding_path: Optional[Path] = None,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    assignment = read_json(assignment_path)
    schedule = read_json(schedule_path)
    platform = Platform.load(platform_path)
    manifest = read_json(manifest_path)
    artifacts = _load_artifacts(manifest_path.parent, manifest)
    pin_plan = (
        read_json(pin_plan_path)
        if pin_plan_path is not None
        else (
            read_json(manifest_path.parent / manifest["pin_plan"])
            if "pin_plan" in manifest
            else None
        )
    )
    if pin_plan is not None:
        resolved_positions = (
            position_hints_path
            if position_hints_path is not None
            else manifest_path.parent / manifest["position_hints"]
        )
        validate_pin_plan(
            schedule,
            platform,
            read_json(resolved_positions),
            pin_plan,
        )
        if pin_plan.get("provider") == CHIMEW_PIN_PLAN_PROVIDER:
            if electrical_binding_path is None and "electrical_binding" not in manifest:
                raise ValidationError(
                    "Chimew Phase 6 manifest has no electrical binding"
                )
            resolved_binding = (
                electrical_binding_path
                if electrical_binding_path is not None
                else manifest_path.parent / manifest["electrical_binding"]
            )
            from .chimew_phase6 import validate_chimew_phase6_binding

            validate_chimew_phase6_binding(
                schedule, platform, pin_plan, read_json(resolved_binding)
            )
        elif electrical_binding_path is not None:
            raise ValueError(
                "electrical_binding_path is supported only with a Chimew pin plan"
            )
    elif position_hints_path is not None:
        raise ValueError(
            "pin_plan_path or manifest pin plan is required with "
            "position_hints_path"
        )
    elif electrical_binding_path is not None:
        raise ValueError(
            "pin_plan_path or manifest pin plan is required with "
            "electrical_binding_path"
        )
    artifacts["manifest"].pop("electrical_binding", None)
    return validate_split_artifacts(
        ir, assignment, schedule, platform, artifacts, pin_plan
    )
