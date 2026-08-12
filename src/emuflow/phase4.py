from pathlib import Path
from typing import Any, Dict, Optional

from .io import read_json, write_json
from .platform import Platform
from .routing import (
    demands_from_assignment,
    load_route_constraints,
    validate_system_routes,
)
from .timing_routing import (
    GLOBAL_CANDIDATE_PROVIDER,
    NATIVE_ROUTER_PROVIDER,
    ROUTE_TDM_PROVIDER,
    TLR_PROVIDER,
    load_sta_paths,
    route_system_native,
    validate_native_system_routes,
)
from .tdm_feedback import validate_tdm_feedback


PHASE4_REPORT_SCHEMA = "emuflow.phase4-report/v1"


def run_phase4(
    assignment_path: Path,
    platform_path: Path,
    output_dir: Path,
    constraints_path: Optional[Path] = None,
    frame_slots: Optional[int] = None,
    max_iterations: Optional[int] = None,
    provider: Optional[str] = None,
    timing_paths_path: Optional[Path] = None,
    router: Optional[str] = None,
    tdm_feedback_path: Optional[Path] = None,
    tdm_feedback_routes_path: Optional[Path] = None,
    tdm_feedback_schedule_path: Optional[Path] = None,
    tdm_feedback_ratio_plan_path: Optional[Path] = None,
    candidate_workers: int = 1,
) -> Dict[str, Any]:
    assignment = read_json(assignment_path)
    platform = Platform.load(platform_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_pool_path = output_dir / "route_candidate_pool.json"
    constraints = load_route_constraints(
        constraints_path,
        platform,
        frame_slots=frame_slots,
        max_iterations=max_iterations,
    )
    if provider is None:
        provider = (
            ROUTE_TDM_PROVIDER
            if timing_paths_path is not None
            else NATIVE_ROUTER_PROVIDER
        )
    timing_paths = None
    feedback_source_paths = (
        tdm_feedback_routes_path,
        tdm_feedback_schedule_path,
        tdm_feedback_ratio_plan_path,
    )
    if tdm_feedback_path is None and any(
        path is not None for path in feedback_source_paths
    ):
        raise ValueError(
            "TDM feedback source artifacts require --tdm-feedback"
        )
    tdm_feedback = None
    tdm_feedback_validation = None
    if tdm_feedback_path is not None:
        if (
            tdm_feedback_routes_path is None
            or tdm_feedback_schedule_path is None
        ):
            raise ValueError(
                "--tdm-feedback requires --tdm-feedback-routes and "
                "--tdm-feedback-schedule so Phase 4 can independently "
                "reconstruct the concrete prices"
            )
        tdm_feedback = read_json(tdm_feedback_path)
        feedback_routes = read_json(tdm_feedback_routes_path)
        feedback_schedule = read_json(tdm_feedback_schedule_path)
        feedback_ratio_plan = (
            read_json(tdm_feedback_ratio_plan_path)
            if tdm_feedback_ratio_plan_path is not None
            else None
        )
        tdm_feedback_validation = validate_tdm_feedback(
            feedback_routes,
            platform,
            feedback_schedule,
            tdm_feedback,
            feedback_ratio_plan,
        )
    if provider == NATIVE_ROUTER_PROVIDER:
        if timing_paths_path is not None:
            raise ValueError(
                f"--provider {NATIVE_ROUTER_PROVIDER} does not accept "
                f"--timing-paths; use {ROUTE_TDM_PROVIDER}"
            )
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            executable=router,
            provider=provider,
            candidate_pool_path=candidate_pool_path,
            tdm_feedback=tdm_feedback,
            candidate_workers=candidate_workers,
        )
        validation = validate_native_system_routes(
            assignment,
            platform,
            routes,
        )
    elif provider in {
        TLR_PROVIDER,
        ROUTE_TDM_PROVIDER,
        GLOBAL_CANDIDATE_PROVIDER,
    }:
        if timing_paths_path is None:
            raise ValueError(
                f"--provider {provider} requires --timing-paths"
            )
        if provider == TLR_PROVIDER:
            constraints = {**constraints, "lambda_tdm": 0.0}
        timing_paths = load_sta_paths(
            timing_paths_path,
            demands_from_assignment(assignment, platform),
        )
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            timing_paths,
            executable=router,
            provider=provider,
            candidate_pool_path=candidate_pool_path,
            tdm_feedback=tdm_feedback,
            candidate_workers=candidate_workers,
        )
        validation = validate_native_system_routes(
            assignment,
            platform,
            routes,
            timing_paths,
        )
    else:
        raise ValueError(f"unsupported Phase 4 provider {provider!r}")
    report: Dict[str, Any] = {
        "schema": PHASE4_REPORT_SCHEMA,
        "phase": 4,
        "status": "pass",
        "design": routes["design"],
        "platform": platform.name,
        "provider": routes["provider"],
        "validation": validation,
        "artifacts": {
            "constraints": "route_constraints.normalized.json",
            "routes": "routes.json",
            "report": "phase4_report.json",
        },
    }
    if timing_paths is not None:
        report["artifacts"]["timing_paths"] = "timing_paths.normalized.json"
    if candidate_pool_path.is_file():
        report["artifacts"]["candidate_pool"] = "route_candidate_pool.json"
    if tdm_feedback is not None:
        report["tdm_feedback"] = routes["joint_optimization"][
            "tdm_feedback"
        ]
        report["tdm_feedback"]["validation"] = tdm_feedback_validation
        report["artifacts"]["tdm_feedback"] = (
            "tdm_feedback.normalized.json"
        )
    write_json(output_dir / "route_constraints.normalized.json", constraints)
    if timing_paths is not None:
        write_json(output_dir / "timing_paths.normalized.json", timing_paths)
    if tdm_feedback is not None:
        write_json(
            output_dir / "tdm_feedback.normalized.json", tdm_feedback
        )
    write_json(output_dir / "routes.json", routes)
    write_json(output_dir / "phase4_report.json", report)
    return report


def validate_phase4(
    assignment_path: Path,
    platform_path: Path,
    routes_path: Path,
    timing_paths_path: Optional[Path] = None,
) -> Dict[str, Any]:
    assignment = read_json(assignment_path)
    platform = Platform.load(platform_path)
    routes = read_json(routes_path)
    provider = routes.get("provider")
    if provider == NATIVE_ROUTER_PROVIDER:
        if timing_paths_path is not None:
            raise ValueError(
                "native load-balanced route validation does not accept "
                "--timing-paths"
            )
        return validate_native_system_routes(
            assignment,
            platform,
            routes,
        )
    if provider in {
        TLR_PROVIDER,
        ROUTE_TDM_PROVIDER,
        GLOBAL_CANDIDATE_PROVIDER,
    }:
        if timing_paths_path is None:
            raise ValueError(
                f"validating {TLR_PROVIDER} requires --timing-paths"
            )
        timing_paths = load_sta_paths(
            timing_paths_path,
            demands_from_assignment(assignment, platform),
        )
        return validate_native_system_routes(
            assignment, platform, routes, timing_paths
        )
    validation = validate_system_routes(assignment, platform, routes)
    if timing_paths_path is not None:
        timing_paths = load_sta_paths(
            timing_paths_path,
            demands_from_assignment(assignment, platform),
        )
        return reconstruct_system_route_timing(
            assignment, platform, routes, timing_paths
        )
    return validation
