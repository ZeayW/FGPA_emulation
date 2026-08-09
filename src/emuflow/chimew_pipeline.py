"""End-to-end orchestration for the source-qualified Chimew Phase 6 path."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .chimew_bank_channel import evaluate_chimew_bank_channel_assignment
from .chimew_grouping import build_chimew_initial_groups
from .chimew_phase6 import run_chimew_phase6_adapter
from .chimew_qualification import (
    build_chimew_phase6_qualification,
    canonical_sha256,
)
from .chimew_refinement import refine_chimew_groups
from .chimew_rudy import evaluate_chimew_rudy
from .errors import ValidationError
from .io import read_json, write_json


CHIMEW_PIPELINE_REPORT_SCHEMA = "emuflow.chimew-phase6-pipeline-report/v1"
CHIMEW_PIPELINE_PROVIDER = "source-qualified-chimew-phase6-pipeline-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_chimew_phase6_pipeline(
    schedule_path: Path,
    platform_path: Path,
    crossings_path: Path,
    positions_path: Path,
    rudy_input_path: Path,
    bank_channel_input_path: Path,
    electrical_map_path: Path,
    output_dir: Path,
    *,
    grouper: Optional[str] = None,
    refiner: Optional[str] = None,
    rudy: Optional[str] = None,
    assigner: Optional[str] = None,
    region_count: int = 31,
) -> Dict[str, Any]:
    """Run, certify, and electrically bind every Chimew Phase 6 kernel."""

    schedule = read_json(schedule_path)
    crossings = read_json(crossings_path)
    positions = read_json(positions_path)
    rudy_input = read_json(rudy_input_path)
    bank_input = read_json(bank_channel_input_path)

    initial = build_chimew_initial_groups(
        schedule, crossings, executable=grouper
    )
    refined = refine_chimew_groups(
        schedule,
        crossings,
        initial,
        positions,
        executable=refiner,
    )
    provenance = bank_input.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("grouping_sha256") != canonical_sha256(refined)
    ):
        raise ValidationError(
            "Chimew bank/channel input does not bind the refined grouping"
        )
    rudy_report = evaluate_chimew_rudy(rudy_input, executable=rudy)
    if rudy_report.get("gate_status") != "pass":
        raise ValidationError("Chimew RUDY qualification gate did not pass")
    bank_report = evaluate_chimew_bank_channel_assignment(
        bank_input, executable=assigner
    )
    qualification = build_chimew_phase6_qualification(
        schedule,
        crossings,
        initial,
        positions,
        refined,
        rudy_input,
        rudy_report,
        bank_input,
        bank_report,
    )

    inputs_dir = output_dir / "inputs"
    kernels_dir = output_dir / "kernels"
    adapter_dir = output_dir / "phase6-adapter"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    kernels_dir.mkdir(parents=True, exist_ok=True)
    input_sources = {
        "schedule": schedule_path,
        "platform": platform_path,
        "crossings": crossings_path,
        "positions": positions_path,
        "rudy_input": rudy_input_path,
        "bank_channel_input": bank_channel_input_path,
        "electrical_map": electrical_map_path,
    }
    input_names = {
        "schedule": "schedule.json",
        "platform": "platform.json",
        "crossings": "crossings.json",
        "positions": "positions.json",
        "rudy_input": "rudy_input.json",
        "bank_channel_input": "bank_channel_input.json",
        "electrical_map": "electrical_map.json",
    }
    artifact_paths: Dict[str, Path] = {}
    for label, source in input_sources.items():
        destination = inputs_dir / input_names[label]
        shutil.copy2(source, destination)
        artifact_paths[label] = destination
    kernel_documents = {
        "initial_grouping": initial,
        "refined_grouping": refined,
        "rudy_report": rudy_report,
        "bank_channel_report": bank_report,
        "qualification": qualification,
    }
    for label, document in kernel_documents.items():
        path = kernels_dir / f"{label}.json"
        write_json(path, document)
        artifact_paths[label] = path

    adapter_report = run_chimew_phase6_adapter(
        artifact_paths["schedule"],
        artifact_paths["platform"],
        artifact_paths["bank_channel_input"],
        artifact_paths["electrical_map"],
        adapter_dir,
        qualification_path=artifact_paths["qualification"],
        bank_channel_report_path=artifact_paths["bank_channel_report"],
        executable=assigner,
        region_count=region_count,
    )
    if adapter_report.get("lookahead_qualification") != "complete-artifact-chain":
        raise ValidationError("Chimew pipeline did not produce a complete binding")
    artifact_paths["adapter_report"] = adapter_dir / "adapter_report.json"
    for label, name in adapter_report["artifacts"].items():
        artifact_paths[f"adapter_{label}"] = adapter_dir / name

    report = {
        "schema": CHIMEW_PIPELINE_REPORT_SCHEMA,
        "status": "pass",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "provider": CHIMEW_PIPELINE_PROVIDER,
        "qualification_sha256": qualification["qualification_sha256"],
        "metrics": {
            "signals": qualification["metrics"]["signals"],
            "groups": qualification["metrics"]["groups"],
            "rudy_peak_utilization": qualification["metrics"][
                "rudy_peak_utilization"
            ],
            "rudy_overloaded_bins": 0,
            "artifact_chain_disagreements": 0,
        },
        "artifacts": {
            label: {
                "path": str(path.relative_to(output_dir)),
                "sha256": _sha256(path),
            }
            for label, path in sorted(artifact_paths.items())
        },
    }
    write_json(output_dir / "pipeline_report.json", report)
    return report
