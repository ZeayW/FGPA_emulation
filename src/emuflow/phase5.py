from pathlib import Path
from typing import Any, Dict, Optional

from .io import read_json, write_json
from .platform import Platform
from .tdm import (
    TDM_BASELINE_PROVIDER,
    build_tdm_schedule,
    build_transport_manifest,
    schedule_to_systemverilog_testbench,
    schedule_to_tsv,
    simulate_tdm_schedule,
    validate_tdm_schedule,
)
from .tdm_ratio import (
    TDM_RATIO_PROVIDER,
    build_tdm_ratio_plan,
    validate_tdm_ratio_plan,
)


PHASE5_REPORT_SCHEMA = "emuflow.phase5-report/v1"


def run_phase5(
    routes_path: Path,
    platform_path: Path,
    output_dir: Path,
    simulation_frames: int = 16,
    provider: Optional[str] = None,
    ratio_optimizer: Optional[str] = None,
    ratio_max_iterations: int = 500,
    max_ratio: Optional[int] = None,
    ratio_quantum: int = 8,
    post_refinement_iterations: int = 200,
    convergence: float = 1.0e-9,
) -> Dict[str, Any]:
    routes = read_json(routes_path)
    platform = Platform.load(platform_path)
    if provider is None:
        provider = (
            TDM_RATIO_PROVIDER
            if isinstance(routes.get("timing"), dict)
            else TDM_BASELINE_PROVIDER
        )
    ratio_plan = None
    ratio_validation = None
    if provider == TDM_BASELINE_PROVIDER:
        if ratio_optimizer is not None:
            raise ValueError(
                "--ratio-optimizer requires the academic Phase 5 provider"
            )
    elif provider == TDM_RATIO_PROVIDER:
        ratio_plan = build_tdm_ratio_plan(
            routes,
            platform,
            executable=ratio_optimizer,
            max_iterations=ratio_max_iterations,
            max_ratio=max_ratio,
            ratio_quantum=ratio_quantum,
            post_refinement_iterations=post_refinement_iterations,
            convergence=convergence,
        )
        ratio_validation = validate_tdm_ratio_plan(
            routes, platform, ratio_plan
        )
    else:
        raise ValueError(f"unsupported Phase 5 provider {provider!r}")
    schedule = build_tdm_schedule(routes, platform, ratio_plan)
    validation = validate_tdm_schedule(
        routes, platform, schedule, ratio_plan
    )
    simulation = simulate_tdm_schedule(
        routes,
        schedule,
        frames=simulation_frames,
    )
    manifest = build_transport_manifest(routes, schedule, platform)
    report: Dict[str, Any] = {
        "schema": PHASE5_REPORT_SCHEMA,
        "phase": 5,
        "status": "pass",
        "design": schedule["design"],
        "platform": platform.name,
        "provider": schedule["provider"],
        **(
            {
                "optimization_provider": ratio_plan["provider"],
                "ratio_validation": ratio_validation,
            }
            if ratio_plan is not None
            else {}
        ),
        "validation": validation,
        "simulation": simulation,
        "artifacts": {
            "schedule": "schedule.json",
            "schedule_tsv": "schedule.tsv",
            "transport_manifest": "transport_manifest.json",
            "rtl_testbench": "transport_schedule_tb.sv",
            "report": "phase5_report.json",
        },
    }
    if ratio_plan is not None:
        report["artifacts"]["ratio_plan"] = "ratio_plan.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    if ratio_plan is not None:
        write_json(output_dir / "ratio_plan.json", ratio_plan)
    write_json(output_dir / "schedule.json", schedule)
    (output_dir / "schedule.tsv").write_text(
        schedule_to_tsv(schedule), encoding="utf-8"
    )
    write_json(output_dir / "transport_manifest.json", manifest)
    (output_dir / "transport_schedule_tb.sv").write_text(
        schedule_to_systemverilog_testbench(
            routes,
            schedule,
            platform,
            frames=simulation_frames,
        ),
        encoding="utf-8",
    )
    write_json(output_dir / "phase5_report.json", report)
    return report


def validate_phase5(
    routes_path: Path,
    platform_path: Path,
    schedule_path: Path,
    ratio_plan_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return validate_tdm_schedule(
        read_json(routes_path),
        Platform.load(platform_path),
        read_json(schedule_path),
        (
            read_json(ratio_plan_path)
            if ratio_plan_path is not None
            else None
        ),
    )
