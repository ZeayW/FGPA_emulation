from pathlib import Path
from typing import Any, Dict, Optional

from .io import read_json, write_json
from .platform import Platform
from .routing import (
    load_route_constraints,
    route_system,
    validate_system_routes,
)


PHASE4_REPORT_SCHEMA = "emuflow.phase4-report/v1"


def run_phase4(
    assignment_path: Path,
    platform_path: Path,
    output_dir: Path,
    constraints_path: Optional[Path] = None,
    frame_slots: Optional[int] = None,
    max_iterations: Optional[int] = None,
) -> Dict[str, Any]:
    assignment = read_json(assignment_path)
    platform = Platform.load(platform_path)
    constraints = load_route_constraints(
        constraints_path,
        platform,
        frame_slots=frame_slots,
        max_iterations=max_iterations,
    )
    routes = route_system(assignment, platform, constraints)
    validation = validate_system_routes(assignment, platform, routes)
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
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "route_constraints.normalized.json", constraints)
    write_json(output_dir / "routes.json", routes)
    write_json(output_dir / "phase4_report.json", report)
    return report


def validate_phase4(
    assignment_path: Path,
    platform_path: Path,
    routes_path: Path,
) -> Dict[str, Any]:
    return validate_system_routes(
        read_json(assignment_path),
        Platform.load(platform_path),
        read_json(routes_path),
    )
