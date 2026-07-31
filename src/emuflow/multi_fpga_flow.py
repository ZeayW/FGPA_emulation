"""Checked, board-independent multi-FPGA compilation orchestration."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import EmuFlowError, ValidationError
from .frame_search import (
    run_frame_length_search,
    validate_frame_search_report,
)
from .io import read_json, write_json
from .multi_fpga_physical_flow import (
    run_multi_fpga_physical_flow,
    validate_multi_fpga_physical_report,
)
from .opensta import DEFAULT_TIMING_MODEL, run_opensta_path_database
from .phase1 import run_phase1
from .phase3 import run_phase3
from .phase4 import run_phase4
from .phase5 import run_phase5
from .phase6 import run_phase6
from .phase7c import run_phase7c
from .sta import (
    derive_partition_net_weights,
    project_sta_path_database,
)
from .synthesis import run_generic_yosys
from .vpr import VTR_HARD_BLOCK_PROFILE, run_vtr_yosys
from .vtr_netlist import normalize_vtr_hard_block_json


MULTI_FPGA_FLOW_SCHEMA = "emuflow.multi-fpga-flow/v2"
MULTI_FPGA_FLOW_PROVIDER = (
    "profiled-yosys+partition+system-route+tdm+split-transport+runtime"
)
MULTI_FPGA_MAPPING_PROFILES = ("vtr-hard-blocks", "generic-soft")
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
    if (
        not isinstance(stages, dict)
        or set(stages) != set(_REQUIRED_STAGES)
    ):
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
    runtime = report.get("runtime")
    if (
        not isinstance(runtime, dict)
        or runtime.get("design") != design
        or runtime.get("platform") != platform
        or runtime.get("validation", {}).get("status") != "pass"
        or runtime.get("runtime_timing", {}).get("status") == "fail"
    ):
        raise ValidationError("multi-FPGA runtime contract did not pass")
    physical = report.get("physical")
    if physical is not None:
        physical_validation = validate_multi_fpga_physical_report(physical)
        if physical_validation["original_cells"] != partition_validation.get(
            "instances"
        ):
            raise ValidationError(
                "multi-FPGA physical original-cell coverage disagrees"
            )
        if runtime.get("physical", {}).get("status") != "pass":
            raise ValidationError(
                "multi-FPGA runtime is not closed against physical results"
            )
    frame_search = report.get("frame_search")
    if frame_search is not None:
        frame_validation = validate_frame_search_report(frame_search)
        if (
            frame_validation["selected_frame_slots"]
            != tdm_validation.get("frame_slots")
            or frame_validation["selected_frame_slots"]
            != runtime["validation"].get("frame_slots")
        ):
            raise ValidationError(
                "frame-search selection disagrees with TDM/runtime"
            )
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
        "frame_slots": runtime["validation"].get("frame_slots"),
        "nominal_virtual_frequency_mhz": runtime["validation"].get(
            "nominal_virtual_frequency_mhz"
        ),
        "physical_status": (
            "pass" if physical is not None else "not-requested"
        ),
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
    mapping_profile: str = "vtr-hard-blocks",
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
    timing_driven: bool = False,
    clock_periods: Optional[Dict[str, float]] = None,
    timing_model: Path = DEFAULT_TIMING_MODEL,
    architecture_timing_db: Optional[Path] = None,
    opensta: Optional[str] = None,
    sta_max_paths: int = 200000,
    timing_criticality_scale: float = 9.0,
    timing_criticality_exponent: float = 2.0,
    route_constraints: Optional[Path] = None,
    timing_paths: Optional[Path] = None,
    router: Optional[str] = None,
    frame_slots: Optional[int] = None,
    optimize_frame_slots: bool = False,
    route_max_iterations: Optional[int] = None,
    ratio_optimizer: Optional[str] = None,
    simulation_frames: int = 16,
    equivalence_cycles: int = 16,
    equivalence_seed: int = 20260727,
    physical: bool = False,
    physical_architecture: Optional[Path] = None,
    physical_architecture_id: str = VTR_HARD_BLOCK_PROFILE,
    physical_vpr: Optional[str] = None,
    physical_architecture_importer: Optional[str] = None,
    physical_packed_importer: Optional[str] = None,
    physical_route_checker: Optional[str] = None,
    physical_openparf_install: Optional[Path] = None,
    physical_openparf_python: Optional[Path] = None,
    physical_seed: int = 1,
    physical_route_channel_width: int = 300,
) -> Dict[str, Any]:
    """Compile RTL/EmuIR through the checked board-independent split."""

    if mapping_profile not in MULTI_FPGA_MAPPING_PROFILES:
        raise EmuFlowError(
            "unsupported multi-FPGA mapping profile "
            f"{mapping_profile!r}; expected one of "
            f"{', '.join(MULTI_FPGA_MAPPING_PROFILES)}"
        )
    timing_driven = timing_driven or architecture_timing_db is not None
    if timing_driven and timing_paths is not None:
        raise EmuFlowError(
            "--timing-driven/--architecture-timing-db cannot be combined "
            "with externally projected --timing-paths"
        )
    if timing_driven and not clock_periods:
        raise EmuFlowError(
            "timing-driven multi-FPGA compilation requires at least one "
            "--clock-period CLOCK=PERIOD_NS"
        )

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
        if mapping_profile == "vtr-hard-blocks":
            raw_json = frontend_root / "vtr-hard-block-atoms.json"
            eblif = frontend_root / "design.eblif"
            synthesis_report = run_vtr_yosys(
                source_list,
                top,
                eblif,
                executable=yosys,
                log_path=frontend_root / "yosys.log",
                hard_blocks=True,
                json_output=raw_json,
            )
            normalization_report = normalize_vtr_hard_block_json(
                raw_json,
                synthesized_json,
                top=top,
            )
            synthesis_mode = "vtr-lut6-ff-hard-blocks"
        else:
            synthesis_report = run_generic_yosys(
                source_list,
                top,
                synthesized_json,
                executable=yosys,
                log_path=frontend_root / "yosys.log",
            )
            normalization_report = None
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
            "mapping_profile": (
                VTR_HARD_BLOCK_PROFILE
                if synthesis_mode == "vtr-lut6-ff-hard-blocks"
                else (
                    "provided-yosys-json"
                    if synthesis_mode == "provided-yosys-json"
                    else mapping_profile
                )
            ),
            "sources": [str(path) for path in source_list],
            "yosys_json_sha256": _sha256(synthesized_json),
            **(
                {"tool_report": synthesis_report}
                if yosys_json is None
                else {}
            ),
            **(
                {"normalization": normalization_report}
                if yosys_json is None
                and normalization_report is not None
                else {}
            ),
        },
    }
    ir_path = phase1_root / "design.emuir.json"

    timing_root = output_dir / "timing"
    path_database_path = timing_root / "path-database.json"
    net_weights_path = timing_root / "partition-net-weights.json"
    timing_report = None
    if timing_driven:
        timing_root.mkdir(parents=True, exist_ok=True)
        sta_report = run_opensta_path_database(
            ir_path=ir_path,
            output_path=path_database_path,
            clocks=clock_periods,
            timing_model_path=timing_model,
            architecture_timing_db_path=architecture_timing_db,
            executable=opensta,
            max_paths=sta_max_paths,
            log_path=timing_root / "opensta.log",
        )
        weights_report = derive_partition_net_weights(
            path_database_path,
            ir_path,
            net_weights_path,
            criticality_scale=timing_criticality_scale,
            criticality_exponent=timing_criticality_exponent,
        )
        timing_report = {
            "status": "pass",
            "mode": "opensta-preplacement",
            "sta": sta_report,
            "partition_weights": weights_report,
            "partition_weights_applied": partition_provider != "greedy",
        }

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
        net_weights_path=(
            net_weights_path
            if timing_driven and partition_provider != "greedy"
            else None
        ),
    )
    assignment_path = phase3_root / "assignment.json"

    projected_timing_paths = timing_paths
    if timing_driven:
        projected_timing_paths = timing_root / "cut-timing-paths.json"
        projection_report = project_sta_path_database(
            path_database_path,
            assignment_path,
            projected_timing_paths,
        )
        timing_report["cut_path_projection"] = projection_report

    phase4_root = output_dir / "system-route"
    phase5_root = output_dir / "tdm"
    frame_search_report = None
    if optimize_frame_slots:
        if frame_slots is None:
            raise EmuFlowError(
                "--optimize-frame-slots requires --frame-slots as its "
                "feasible upper bound"
            )
        frame_search_report = run_frame_length_search(
            assignment_path,
            platform_path,
            projected_timing_paths,
            output_dir / "frame-search",
            phase4_root,
            phase5_root,
            max_frame_slots=frame_slots,
            route_constraints=route_constraints,
            route_max_iterations=route_max_iterations,
            router=router,
            ratio_optimizer=ratio_optimizer,
            simulation_frames=simulation_frames,
        )
        phase4_report = read_json(phase4_root / "phase4_report.json")
        phase5_report = read_json(phase5_root / "phase5_report.json")
    else:
        phase4_report = run_phase4(
            assignment_path,
            platform_path,
            phase4_root,
            constraints_path=route_constraints,
            frame_slots=frame_slots,
            max_iterations=route_max_iterations,
            timing_paths_path=projected_timing_paths,
            router=router,
        )
        phase5_report = run_phase5(
            phase4_root / "routes.json",
            platform_path,
            phase5_root,
            simulation_frames=simulation_frames,
            ratio_optimizer=ratio_optimizer,
        )
    routes_path = phase4_root / "routes.json"
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

    physical_report = None
    physical_summary_path = None
    if physical:
        physical_report = run_multi_fpga_physical_flow(
            phase6_root,
            platform_path,
            schedule_path,
            output_dir / "physical",
            architecture=physical_architecture,
            architecture_id=physical_architecture_id,
            yosys=yosys,
            vpr=physical_vpr,
            architecture_importer=physical_architecture_importer,
            packed_importer=physical_packed_importer,
            route_checker=physical_route_checker,
            openparf_install=physical_openparf_install,
            openparf_python=physical_openparf_python,
            seed=physical_seed,
            route_channel_width=physical_route_channel_width,
        )
        physical_summary_path = output_dir / "physical/physical-summary.json"

    runtime_root = output_dir / "runtime"
    runtime_report = run_phase7c(
        schedule_path,
        platform_path,
        phase3_root / "phase3_report.json",
        phase4_root / "phase4_report.json",
        phase5_root / "phase5_report.json",
        phase6_root / "phase6_report.json",
        runtime_root,
        physical_summary_path=physical_summary_path,
    )

    report = {
        "schema": MULTI_FPGA_FLOW_SCHEMA,
        "status": "pass",
        "provider": MULTI_FPGA_FLOW_PROVIDER,
        "architecture_policy": "provider-neutral",
        **({"timing": timing_report} if timing_report is not None else {}),
        **(
            {"frame_search": frame_search_report}
            if frame_search_report is not None
            else {}
        ),
        "runtime": runtime_report,
        **(
            {"physical": physical_report}
            if physical_report is not None
            else {}
        ),
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
            "runtime_contract": {
                "path": "runtime/runtime_contract.json",
                "sha256": _sha256(runtime_root / "runtime_contract.json"),
            },
            "qor_report": {
                "path": "runtime/qor_report.json",
                "sha256": _sha256(runtime_root / "qor_report.json"),
            },
            **(
                {
                    "physical_flow_report": {
                        "path": "physical/multi-fpga-physical-flow-report.json",
                        "sha256": _sha256(
                            output_dir
                            / "physical/multi-fpga-physical-flow-report.json"
                        ),
                    },
                    "physical_summary": {
                        "path": "physical/physical-summary.json",
                        "sha256": _sha256(
                            output_dir / "physical/physical-summary.json"
                        ),
                    },
                }
                if physical_report is not None
                else {}
            ),
            **(
                {
                    "frame_search_report": {
                        "path": "frame-search/frame-search-report.json",
                        "sha256": _sha256(
                            output_dir
                            / "frame-search/frame-search-report.json"
                        ),
                    }
                }
                if frame_search_report is not None
                else {}
            ),
            **(
                {
                    "timing_path_database": {
                        "path": "timing/path-database.json",
                        "sha256": _sha256(path_database_path),
                    },
                    "partition_net_weights": {
                        "path": "timing/partition-net-weights.json",
                        "sha256": _sha256(net_weights_path),
                    },
                    "cut_timing_paths": {
                        "path": "timing/cut-timing-paths.json",
                        "sha256": _sha256(projected_timing_paths),
                    },
                }
                if timing_driven
                else {}
            ),
        },
    }
    report["summary"] = validate_multi_fpga_flow_report(report)
    write_json(output_dir / "multi-fpga-flow-report.json", report)
    return report
