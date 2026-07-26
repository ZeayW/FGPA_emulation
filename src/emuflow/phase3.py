from pathlib import Path
from typing import Any, Dict, Optional

from .io import read_json, write_json
from .ir import EmuIR
from .partition import (
    assign_clusters,
    build_clusters,
    load_partition_constraints,
    validate_partition_artifacts,
)
from .platform import Platform


PHASE3_REPORT_SCHEMA = "emuflow.phase3-report/v1"


def run_phase3(
    ir_path: Path,
    platform_path: Path,
    output_dir: Path,
    constraints_path: Optional[Path] = None,
    seed: int = 0,
    min_used_fpgas: Optional[int] = None,
    balance_tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    platform = Platform.load(platform_path)
    constraints = load_partition_constraints(
        constraints_path,
        ir,
        platform,
        min_used_fpgas=min_used_fpgas,
        balance_tolerance=balance_tolerance,
    )
    clusters = build_clusters(ir, constraints)
    assignment = assign_clusters(
        ir,
        platform,
        clusters,
        constraints,
        seed=seed,
    )
    validation = validate_partition_artifacts(
        ir,
        platform,
        clusters,
        assignment,
    )
    report: Dict[str, Any] = {
        "schema": PHASE3_REPORT_SCHEMA,
        "phase": 3,
        "status": "pass",
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "provider": assignment["provider"],
        "seed": seed,
        "validation": validation,
        "partitions": assignment["partitions"],
        "artifacts": {
            "clusters": "clusters.json",
            "constraints": "constraints.normalized.json",
            "assignment": "assignment.json",
            "report": "phase3_report.json",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "clusters.json", clusters)
    write_json(output_dir / "constraints.normalized.json", constraints)
    write_json(output_dir / "assignment.json", assignment)
    write_json(output_dir / "phase3_report.json", report)
    return report


def validate_phase3(
    ir_path: Path,
    platform_path: Path,
    clusters_path: Path,
    assignment_path: Path,
) -> Dict[str, Any]:
    return validate_partition_artifacts(
        EmuIR.load(ir_path),
        Platform.load(platform_path),
        read_json(clusters_path),
        read_json(assignment_path),
    )
