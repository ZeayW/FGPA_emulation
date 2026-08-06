"""Checked, board-independent multi-FPGA compilation orchestration."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .board_link_timing import (
    directed_route_link_delays,
    validate_board_link_timing,
)
from .cross_stage import run_cross_stage_optimization
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
from .multi_fpga_bsp_flow import (
    run_multi_fpga_bsp_flow,
    validate_multi_fpga_bsp_flow_report,
)
from .opensta import DEFAULT_TIMING_MODEL, run_opensta_path_database
from .phase1 import run_phase1
from .phase3 import run_phase3
from .phase4 import run_phase4
from .phase5 import run_phase5
from .phase6 import run_phase6
from .phase7c import run_phase7c
from .platform import Platform
from .routing import SYSTEM_ROUTE_CONSTRAINTS_SCHEMA
from .sta import (
    derive_partition_net_weights,
    project_sta_path_database,
)
from .synthesis import run_generic_yosys
from .vivado_backend import run_vivado_timing_path_database
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
    link_timing = report.get("board_link_timing")
    if link_timing is not None:
        link_validation = (
            link_timing.get("validation")
            if isinstance(link_timing, dict)
            else None
        )
        route_projection = (
            link_timing.get("routing_projection")
            if isinstance(link_timing, dict)
            else None
        )
        if (
            not isinstance(link_timing, dict)
            or link_timing.get("status") != "pass"
            or not isinstance(link_validation, dict)
            or link_validation.get("status") != "pass"
            or not isinstance(route_projection, dict)
            or route_projection.get("status") != "pass"
            or link_timing.get("applied_to")
            != [
                "phase4-system-routing",
                "phase5-tdm-ratio-and-schedule-timing",
                "phase7c-system-timing-when-physical",
            ]
        ):
            raise ValidationError(
                "multi-FPGA BoardLinkTimingDB application is invalid"
            )
    hardware_bsp = report.get("hardware_bsp")
    if hardware_bsp is not None:
        bsp_validation = validate_multi_fpga_bsp_flow_report(hardware_bsp)
        if (
            bsp_validation["design"] != design
            or bsp_validation["platform"] != platform
        ):
            raise ValidationError(
                "multi-FPGA hardware BSP identity disagrees"
            )
        source_artifact = report.get("artifacts", {}).get(
            "board_independent_flow_report", {}
        )
        if (
            source_artifact.get("sha256")
            != hardware_bsp.get("source_flow_report_sha256")
        ):
            raise ValidationError(
                "multi-FPGA hardware BSP source-flow hash disagrees"
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
    cross_stage = report.get("cross_stage")
    cross_stage_iteration = None
    if cross_stage is not None:
        if (
            not isinstance(cross_stage, dict)
            or cross_stage.get("status") != "pass"
            or cross_stage.get("design") != design
            or cross_stage.get("platform") != platform
        ):
            raise ValidationError(
                "multi-FPGA cross-stage identity disagrees"
            )
        candidates = cross_stage.get("candidates")
        selected_iteration = cross_stage.get("selected_iteration")
        if (
            not isinstance(candidates, list)
            or isinstance(selected_iteration, bool)
            or not isinstance(selected_iteration, int)
            or selected_iteration < 0
            or selected_iteration >= len(candidates)
        ):
            raise ValidationError(
                "multi-FPGA cross-stage selection is invalid"
            )
        selected = candidates[selected_iteration]
        if (
            not isinstance(selected, dict)
            or selected.get("status") != "pass"
            or selected.get("iteration") != selected_iteration
            or selected.get("candidate_id")
            != cross_stage.get("selected_candidate_id")
            or selected.get("phase3_validation")
            != partition_validation
            or selected.get("phase4_validation") != route_validation
            or selected.get("phase5_validation") != tdm_validation
        ):
            raise ValidationError(
                "selected cross-stage candidate disagrees with canonical "
                "Phase 3--5 stages"
            )
        cross_stage_iteration = selected_iteration
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
        "cross_stage_iteration": cross_stage_iteration,
        "nominal_virtual_frequency_mhz": runtime["validation"].get(
            "nominal_virtual_frequency_mhz"
        ),
        "physical_status": (
            "pass" if physical is not None else "not-requested"
        ),
        "hardware_bsp_status": (
            "pass" if hardware_bsp is not None else "not-requested"
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
    timing_backend: str = "opensta",
    clock_periods: Optional[Dict[str, float]] = None,
    timing_model: Path = DEFAULT_TIMING_MODEL,
    architecture_timing_db: Optional[Path] = None,
    opensta: Optional[str] = None,
    timing_vivado: Optional[str] = None,
    sta_max_paths: int = 200000,
    timing_criticality_scale: float = 9.0,
    timing_criticality_exponent: float = 2.0,
    route_constraints: Optional[Path] = None,
    board_link_timing_db: Optional[Path] = None,
    timing_paths: Optional[Path] = None,
    router: Optional[str] = None,
    frame_slots: Optional[int] = None,
    optimize_frame_slots: bool = False,
    route_max_iterations: Optional[int] = None,
    ratio_optimizer: Optional[str] = None,
    cross_stage_iterations: int = 0,
    cross_stage_feedback_optimizer: Optional[str] = None,
    cross_stage_pair_pressure_weight: float = 1.0,
    simulation_frames: int = 16,
    equivalence_cycles: int = 16,
    equivalence_seed: int = 20260727,
    physical: bool = False,
    physical_backend: str = "open",
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
    physical_vivado: Optional[str] = None,
    physical_vivado_max_timing_paths: int = 10000,
    physical_vivado_place_directive: str = "Default",
    physical_vivado_route_directive: str = "Default",
    serial_bsp_phy_provider: Optional[Path] = None,
    serial_bsp_runtime_sync_provider: Optional[Path] = None,
    serial_bsp_board_overlay: Optional[Path] = None,
    serial_bsp_gt_site_map: Optional[Path] = None,
    serial_bsp_vivado: Optional[Path] = None,
    serial_bsp_yosys: Optional[Path] = None,
    serial_bsp_runtime_sync_root: Optional[str] = None,
    serial_bsp_ready_stable_cycles: int = 4,
) -> Dict[str, Any]:
    """Compile RTL/EmuIR through the checked board-independent split."""

    if mapping_profile not in MULTI_FPGA_MAPPING_PROFILES:
        raise EmuFlowError(
            "unsupported multi-FPGA mapping profile "
            f"{mapping_profile!r}; expected one of "
            f"{', '.join(MULTI_FPGA_MAPPING_PROFILES)}"
        )
    if timing_backend not in {"opensta", "vivado"}:
        raise EmuFlowError(
            "timing backend must be 'opensta' or 'vivado'"
        )
    if timing_backend == "vivado" and architecture_timing_db is not None:
        raise EmuFlowError(
            "--architecture-timing-db applies only to timing-backend=opensta"
        )
    if timing_backend == "vivado" and opensta is not None:
        raise EmuFlowError("--opensta applies only to timing-backend=opensta")
    if timing_backend == "opensta" and timing_vivado is not None:
        raise EmuFlowError(
            "--timing-vivado applies only to timing-backend=vivado"
        )
    timing_driven = timing_driven or architecture_timing_db is not None
    if timing_driven and timing_paths is not None:
        raise EmuFlowError(
            "--timing-driven/--architecture-timing-db cannot be combined "
            "with externally projected --timing-paths"
        )
    if board_link_timing_db is not None and not timing_driven:
        raise EmuFlowError(
            "--board-link-timing-db requires --timing-driven so its bounds "
            "participate in partition-crossing route/TDM optimization"
        )
    if (
        isinstance(cross_stage_iterations, bool)
        or not isinstance(cross_stage_iterations, int)
        or cross_stage_iterations < 0
    ):
        raise EmuFlowError("--cross-stage-iterations must be non-negative")
    if cross_stage_iterations and not timing_driven:
        raise EmuFlowError(
            "--cross-stage-iterations requires --timing-driven"
        )
    if timing_driven and not clock_periods:
        raise EmuFlowError(
            "timing-driven multi-FPGA compilation requires at least one "
            "--clock-period CLOCK=PERIOD_NS"
        )
    serial_bsp_requested = serial_bsp_phy_provider is not None
    serial_bsp_auxiliary = any(
        value is not None
        for value in (
            serial_bsp_runtime_sync_provider,
            serial_bsp_board_overlay,
            serial_bsp_gt_site_map,
            serial_bsp_vivado,
            serial_bsp_yosys,
            serial_bsp_runtime_sync_root,
        )
    )
    if serial_bsp_auxiliary and not serial_bsp_requested:
        raise EmuFlowError(
            "serial BSP options require --serial-bsp-phy-provider"
        )
    if serial_bsp_requested and serial_bsp_runtime_sync_provider is None:
        raise EmuFlowError(
            "serial BSP continuation requires --serial-bsp-runtime-sync-provider"
        )
    if serial_bsp_requested and (
        (serial_bsp_vivado is None) == (serial_bsp_yosys is None)
    ):
        raise EmuFlowError(
            "serial BSP continuation requires exactly one of "
            "--serial-bsp-vivado or --serial-bsp-yosys"
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
        if timing_backend == "opensta":
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
            timing_mode = "opensta-preplacement"
        else:
            platform = Platform.load(platform_path)
            parts = {fpga.part for fpga in platform.fpgas}
            if len(parts) != 1:
                raise EmuFlowError(
                    "Vivado timing backend requires one common FPGA part"
                )
            sta_report = run_vivado_timing_path_database(
                ir_path=ir_path,
                output_path=path_database_path,
                clocks=clock_periods,
                part=next(iter(parts)),
                executable=timing_vivado,
                max_paths=sta_max_paths,
            )
            timing_mode = "vivado-post-synthesis"
        weights_report = derive_partition_net_weights(
            path_database_path,
            ir_path,
            net_weights_path,
            criticality_scale=timing_criticality_scale,
            criticality_exponent=timing_criticality_exponent,
        )
        timing_report = {
            "status": "pass",
            "mode": timing_mode,
            "backend": timing_backend,
            "sta": sta_report,
            "partition_weights": weights_report,
            "partition_weights_applied": partition_provider != "greedy",
        }

    effective_route_constraints = route_constraints
    link_timing_report = None
    copied_link_timing_path = None
    effective_route_constraints_path = None
    if board_link_timing_db is not None:
        platform = Platform.load(platform_path)
        database = read_json(board_link_timing_db.resolve())
        validation = validate_board_link_timing(database, platform)
        link_delays, projection = directed_route_link_delays(
            database, platform
        )
        timing_root.mkdir(parents=True, exist_ok=True)
        copied_link_timing_path = timing_root / "board-link-timing.json"
        write_json(copied_link_timing_path, database)
        raw_constraints = (
            read_json(route_constraints.resolve())
            if route_constraints is not None
            else {"schema": SYSTEM_ROUTE_CONSTRAINTS_SCHEMA}
        )
        if not isinstance(raw_constraints, dict):
            raise ValidationError("route constraints must be an object")
        raw_constraints = dict(raw_constraints)
        raw_constraints["directed_link_delay_ns"] = link_delays
        effective_route_constraints_path = (
            timing_root / "board-link-route-constraints.json"
        )
        write_json(effective_route_constraints_path, raw_constraints)
        effective_route_constraints = effective_route_constraints_path
        link_timing_report = {
            "status": "pass",
            "validation": validation,
            "routing_projection": projection,
            "applied_to": [
                "phase4-system-routing",
                "phase5-tdm-ratio-and-schedule-timing",
                "phase7c-system-timing-when-physical",
            ],
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
    if timing_driven and not cross_stage_iterations:
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
    cross_stage_report = None
    if cross_stage_iterations:
        cross_stage_root = output_dir / "cross-stage"
        cross_stage_report = run_cross_stage_optimization(
            ir_path=ir_path,
            platform_path=platform_path,
            database_path=path_database_path,
            initial_assignment_path=assignment_path,
            output_dir=cross_stage_root,
            phase3_constraints_path=(
                phase3_root / "constraints.normalized.json"
            ),
            route_constraints_path=route_constraints,
            board_link_timing_path=board_link_timing_db,
            phase3_provider=partition_provider,
            max_outer_iterations=cross_stage_iterations,
            seed=seed,
            min_used_fpgas=min_used_fpgas,
            balance_tolerance=balance_tolerance,
            openroad=openroad,
            repart=repart,
            partition_timeout_seconds=partition_timeout_seconds,
            partition_seed_attempts=partition_seed_attempts,
            partition_repair_min_used_fpgas=(
                partition_repair_min_used_fpgas
            ),
            partition_repair_balance=partition_repair_balance,
            router=router,
            frame_slots=frame_slots,
            optimize_frame_slots=optimize_frame_slots,
            route_max_iterations=route_max_iterations,
            ratio_optimizer=ratio_optimizer,
            feedback_optimizer=cross_stage_feedback_optimizer,
            simulation_frames=simulation_frames,
            pair_pressure_weight=cross_stage_pair_pressure_weight,
        )
        selected = cross_stage_report["candidates"][
            cross_stage_report["selected_iteration"]
        ]
        if cross_stage_report["selected_iteration"] != 0:
            selected_phase3_root = (
                cross_stage_root / selected["assignment"]
            ).parent
            shutil.rmtree(phase3_root)
            shutil.copytree(selected_phase3_root, phase3_root)
            phase3_report = read_json(phase3_root / "phase3_report.json")
            assignment_path = phase3_root / "assignment.json"
        projected_timing_paths = timing_root / "cut-timing-paths.json"
        shutil.copy2(
            cross_stage_root / selected["timing_paths"],
            projected_timing_paths,
        )
        timing_report["cut_path_projection"] = selected["projection"]
        shutil.copytree(
            (cross_stage_root / selected["routes"]).parent,
            phase4_root,
        )
        shutil.copytree(
            (cross_stage_root / selected["schedule"]).parent,
            phase5_root,
        )
        phase4_report = read_json(phase4_root / "phase4_report.json")
        phase5_report = read_json(phase5_root / "phase5_report.json")
        if optimize_frame_slots:
            selected_frame_search = cross_stage_root / selected[
                "frame_search"
            ]
            shutil.copytree(
                selected_frame_search.parent,
                output_dir / "frame-search",
            )
            frame_search_report = read_json(
                output_dir / "frame-search/frame-search-report.json"
            )
    elif optimize_frame_slots:
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
            route_constraints=effective_route_constraints,
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
            constraints_path=effective_route_constraints,
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
            backend=physical_backend,
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
            vivado=physical_vivado,
            vivado_max_timing_paths=physical_vivado_max_timing_paths,
            vivado_place_directive=physical_vivado_place_directive,
            vivado_route_directive=physical_vivado_route_directive,
            original_ir_path=ir_path if timing_driven else None,
            assignment_path=assignment_path if timing_driven else None,
            routes_path=routes_path if timing_driven else None,
            path_database_path=(
                path_database_path if timing_driven else None
            ),
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
        routes_path=routes_path if physical_summary_path is not None else None,
        board_link_timing_path=(
            copied_link_timing_path
            if physical_summary_path is not None
            else None
        ),
    )
    if physical and runtime_report.get("status") != "pass":
        raise ValidationError(
            "full physical flow did not close unified Phase 7C system timing"
        )

    report = {
        "schema": MULTI_FPGA_FLOW_SCHEMA,
        "status": "pass",
        "provider": MULTI_FPGA_FLOW_PROVIDER,
        "architecture_policy": "provider-neutral",
        **({"timing": timing_report} if timing_report is not None else {}),
        **(
            {"board_link_timing": link_timing_report}
            if link_timing_report is not None
            else {}
        ),
        **(
            {"frame_search": frame_search_report}
            if frame_search_report is not None
            else {}
        ),
        **(
            {"cross_stage": cross_stage_report}
            if cross_stage_report is not None
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
                    "cross_stage_report": {
                        "path": "cross-stage/cross_stage_report.json",
                        "sha256": _sha256(
                            output_dir
                            / "cross-stage/cross_stage_report.json"
                        ),
                    }
                }
                if cross_stage_report is not None
                else {}
            ),
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
                    "board_link_timing": {
                        "path": "timing/board-link-timing.json",
                        "sha256": _sha256(copied_link_timing_path),
                    },
                    "board_link_route_constraints": {
                        "path": "timing/board-link-route-constraints.json",
                        "sha256": _sha256(
                            effective_route_constraints_path
                        ),
                    },
                }
                if copied_link_timing_path is not None
                and effective_route_constraints_path is not None
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
    if serial_bsp_requested:
        write_json(output_dir / "board-independent-flow-report.json", report)
        assert serial_bsp_phy_provider is not None
        assert serial_bsp_runtime_sync_provider is not None
        bsp_report = run_multi_fpga_bsp_flow(
            flow_root=output_dir,
            platform_path=platform_path,
            phy_provider_path=serial_bsp_phy_provider,
            runtime_sync_provider_path=serial_bsp_runtime_sync_provider,
            output_dir=output_dir / "hardware-bsp",
            board_overlay_path=serial_bsp_board_overlay,
            gt_site_map_path=serial_bsp_gt_site_map,
            vivado_executable=serial_bsp_vivado,
            yosys_executable=serial_bsp_yosys,
            runtime_sync_root=serial_bsp_runtime_sync_root,
            ready_stable_cycles=serial_bsp_ready_stable_cycles,
        )
        report["hardware_bsp"] = bsp_report
        report["artifacts"]["board_independent_flow_report"] = {
            "path": "board-independent-flow-report.json",
            "sha256": _sha256(output_dir / "board-independent-flow-report.json"),
        }
        report["artifacts"]["hardware_bsp_flow_report"] = {
            "path": "hardware-bsp/multi-fpga-bsp-flow-report.json",
            "sha256": _sha256(
                output_dir / "hardware-bsp/multi-fpga-bsp-flow-report.json"
            ),
        }
        report["summary"] = validate_multi_fpga_flow_report(report)
    write_json(output_dir / "multi-fpga-flow-report.json", report)
    return report
