"""Checked, board-independent multi-FPGA compilation orchestration."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import EmuFlowError, ValidationError
from .io import write_json
from .phase1 import run_phase1
from .phase3 import run_phase3
from .phase4 import run_phase4
from .phase5 import run_phase5
from .phase6 import run_phase6
from .synthesis import run_generic_yosys


MULTI_FPGA_FLOW_SCHEMA = "emuflow.multi-fpga-flow/v1"
MULTI_FPGA_FLOW_PROVIDER = (
    "generic-yosys+partition+system-route+tdm+split-transport"
)
_REQUIRED_STAGES = ("frontend", "partition", "system_route", "tdm", "split")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_multi_fpga_flow_report(
    report: Dict[str, Any],
) -> Dict[str, Any]:
    if report.get("schema") != MULTI_FPGA_FLOW_SCHEMA:
        raise ValidationError("multi-FPGA flow report schema is invalid")
    if report.get("status") != "pass":
        raise ValidationError("multi-FPGA flow report did not pass")
    stages = report.get("stages")
    if not isinstance(stages, dict) or tuple(stages) != _REQUIRED_STAGES:
        raise ValidationError("multi-FPGA flow stages are incomplete")
    for name in _REQUIRED_STAGES:
        stage = stages[name]
        if not isinstance(stage, dict) or stage.get("status") != "pass":
            raise ValidationError(
                f"multi-FPGA flow stage {name!r} did not pass"
            )

    design = stages["frontend"].get("design")
    platform = stages["frontend"].get("platform")
    for name in _REQUIRED_STAGES[1:]:
        if stages[name].get("design") != design:
            raise ValidationError(
                f"multi-FPGA flow stage {name!r} design identity disagrees"
            )
        if stages[name].get("platform") != platform:
            raise ValidationError(
                f"multi-FPGA flow stage {name!r} platform identity disagrees"
            )

    partition_validation = stages["partition"].get("validation", {})
    route_validation = stages["system_route"].get("validation", {})
    tdm_validation = stages["tdm"].get("validation", {})
    split_validation = stages["split"].get("validation", {})
    equivalence = stages["split"].get("equivalence", {})
    if any(
        item.get("status") != "pass"
        for item in (
            partition_validation,
            route_validation,
            tdm_validation,
            split_validation,
            equivalence,
        )
    ):
        raise ValidationError(
            "one or more independent multi-FPGA checks did not pass"
        )

    return {
        "status": "pass",
        "design": design,
        "platform": platform,
        "instances": partition_validation.get("instances"),
        "used_fpgas": partition_validation.get("used_fpgas"),
        "cut_nets": partition_validation.get("cut_nets"),
        "scheduled_hops": split_validation.get("scheduled_hops"),
        "equivalence_mismatches": equivalence.get("mismatches"),
    }


def run_multi_fpga_flow(
    platform_path: Path,
    output_dir: Path,
    *,
    sources: Iterable[Path] = (),
    top: Optional[str] = None,
    clocks: Iterable[str] = (),
    yosys_json: Optional[Path] = None,
    yosys: Optional[str] = None,
    partition_constraints: Optional[Path] = None,
    partition_provider: str = "tritonpart",
    seed: int = 0,
    min_used_fpgas: Optional[int] = None,
    balance_tolerance: Optional[float] = None,
    openroad: Optional[str] = None,
    repart: Optional[str] = None,
    partition_timeout_seconds: int = 3600,
    partition_seed_attempts: int = 1,
    partition_repair_min_used_fpgas: bool = False,
    partition_repair_balance: bool = False,
    route_constraints: Optional[Path] = None,
    timing_paths: Optional[Path] = None,
    router: Optional[str] = None,
    frame_slots: Optional[int] = None,
    route_max_iterations: Optional[int] = None,
    ratio_optimizer: Optional[str] = None,
    simulation_frames: int = 16,
    equivalence_cycles: int = 16,
    equivalence_seed: int = 20260727,
) -> Dict[str, Any]:
    """Compile RTL/EmuIR through the checked board-independent split."""

    output_dir = output_dir.resolve()
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise EmuFlowError(
                "multi-FPGA output path must be an empty directory: "
                f"{output_dir}"
            )
    output_dir.mkdir(parents=True, exist_ok=True)

    source_list = [path.resolve() for path in sources]
    frontend_root = output_dir / "frontend"
    frontend_root.mkdir(parents=True, exist_ok=True)
    synthesized_json = frontend_root / "synthesized.json"
    synthesis_mode: str
    if yosys_json is not None:
        if source_list:
            raise EmuFlowError(
                "provide RTL sources or --yosys-json, not both"
            )
        source_json = yosys_json.resolve()
        if not source_json.is_file():
            raise EmuFlowError(f"Yosys JSON does not exist: {source_json}")
        shutil.copyfile(source_json, synthesized_json)
        synthesis_mode = "provided-yosys-json"
    else:
        if not source_list:
            raise EmuFlowError(
                "multi-FPGA compilation requires RTL sources or --yosys-json"
            )
        if top is None:
            raise EmuFlowError("--top is required when compiling RTL sources")
        run_generic_yosys(
            source_list,
            top,
            synthesized_json,
            executable=yosys,
            log_path=frontend_root / "yosys.log",
        )
        synthesis_mode = "generic-lut6-ff"

    phase1_root = frontend_root / "phase1"
    frontend_report = run_phase1(
        synthesized_json,
        platform_path,
        phase1_root,
        top=top,
        clocks=clocks,
    )
    if frontend_report["status"] != "pass":
        raise EmuFlowError(
            "multi-FPGA frontend failed capacity or clock-topology checks"
        )
    frontend_report = {
        **frontend_report,
        "synthesis": {
            "provider": "yosys",
            "mode": synthesis_mode,
            "sources": [str(path) for path in source_list],
            "yosys_json_sha256": _sha256(synthesized_json),
        },
    }
    ir_path = phase1_root / "design.emuir.json"

    phase3_root = output_dir / "partition"
    phase3_report = run_phase3(
        ir_path,
        platform_path,
        phase3_root,
        constraints_path=partition_constraints,
        seed=seed,
        min_used_fpgas=min_used_fpgas,
        balance_tolerance=balance_tolerance,
        provider=partition_provider,
        openroad=openroad,
        tritonpart_timeout_seconds=partition_timeout_seconds,
        tritonpart_seed_attempts=partition_seed_attempts,
        tritonpart_repair_min_used_fpgas=(
            partition_repair_min_used_fpgas
        ),
        tritonpart_repair_balance=partition_repair_balance,
        repart=repart,
        repart_timeout_seconds=partition_timeout_seconds,
    )
    assignment_path = phase3_root / "assignment.json"

    phase4_root = output_dir / "system-route"
    phase4_report = run_phase4(
        assignment_path,
        platform_path,
        phase4_root,
        constraints_path=route_constraints,
        frame_slots=frame_slots,
        max_iterations=route_max_iterations,
        timing_paths_path=timing_paths,
        router=router,
    )
    routes_path = phase4_root / "routes.json"

    phase5_root = output_dir / "tdm"
    phase5_report = run_phase5(
        routes_path,
        platform_path,
        phase5_root,
        simulation_frames=simulation_frames,
        ratio_optimizer=ratio_optimizer,
    )
    schedule_path = phase5_root / "schedule.json"

    phase6_root = output_dir / "split"
    phase6_report = run_phase6(
        ir_path,
        assignment_path,
        schedule_path,
        platform_path,
        phase6_root,
        equivalence_cycles=equivalence_cycles,
        equivalence_seed=equivalence_seed,
    )

    report = {
        "schema": MULTI_FPGA_FLOW_SCHEMA,
        "status": "pass",
        "provider": MULTI_FPGA_FLOW_PROVIDER,
        "architecture_policy": "provider-neutral",
        "stages": {
            "frontend": frontend_report,
            "partition": phase3_report,
            "system_route": phase4_report,
            "tdm": phase5_report,
            "split": phase6_report,
        },
        "artifacts": {
            "emuir": {
                "path": "frontend/phase1/design.emuir.json",
                "sha256": _sha256(ir_path),
            },
            "assignment": {
                "path": "partition/assignment.json",
                "sha256": _sha256(assignment_path),
            },
            "routes": {
                "path": "system-route/routes.json",
                "sha256": _sha256(routes_path),
            },
            "schedule": {
                "path": "tdm/schedule.json",
                "sha256": _sha256(schedule_path),
            },
            "split_manifest": {
                "path": "split/manifest.json",
                "sha256": _sha256(phase6_root / "manifest.json"),
            },
        },
    }
    report["summary"] = validate_multi_fpga_flow_report(report)
    write_json(output_dir / "multi-fpga-flow-report.json", report)
    return report
