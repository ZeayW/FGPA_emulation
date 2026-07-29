from pathlib import Path
from typing import Any, Dict, Optional

from .io import read_json, write_json
from .platform import Platform
from .routing import (
    demands_from_assignment,
    load_route_constraints,
    route_system,
    validate_system_routes,
)
from .timing_routing import (
    ROUTE_TDM_PROVIDER,
    TLR_PROVIDER,
    load_sta_paths,
    route_system_timing_aware,
    validate_timing_aware_system_routes,
)


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
) -> Dict[str, Any]:
    assignment = read_json(assignment_path)
    platform = Platform.load(platform_path)
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
            else "negotiated-shortest-path-tree-v1"
        )
    timing_paths = None
    if provider == "negotiated-shortest-path-tree-v1":
        if timing_paths_path is not None:
            raise ValueError(
                "--timing-paths requires "
                f"--provider {ROUTE_TDM_PROVIDER}"
            )
        routes = route_system(assignment, platform, constraints)
        validation = validate_system_routes(assignment, platform, routes)
    elif provider in {TLR_PROVIDER, ROUTE_TDM_PROVIDER}:
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
        routes = route_system_timing_aware(
            assignment,
            platform,
            constraints,
            timing_paths,
            executable=router,
            provider=provider,
        )
        validation = validate_timing_aware_system_routes(
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
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "route_constraints.normalized.json", constraints)
    if timing_paths is not None:
        write_json(output_dir / "timing_paths.normalized.json", timing_paths)
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
    if routes.get("provider") in {TLR_PROVIDER, ROUTE_TDM_PROVIDER}:
        if timing_paths_path is None:
            raise ValueError(
                f"validating {TLR_PROVIDER} requires --timing-paths"
            )
        timing_paths = load_sta_paths(
            timing_paths_path,
            demands_from_assignment(assignment, platform),
        )
        return validate_timing_aware_system_routes(
            assignment, platform, routes, timing_paths
        )
    return validate_system_routes(assignment, platform, routes)
