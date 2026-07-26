from pathlib import Path
from typing import Any, Dict

from .io import read_json, write_json
from .platform import Platform
from .tdm import (
    build_tdm_schedule,
    build_transport_manifest,
    schedule_to_systemverilog_testbench,
    schedule_to_tsv,
    simulate_tdm_schedule,
    validate_tdm_schedule,
)


PHASE5_REPORT_SCHEMA = "emuflow.phase5-report/v1"


def run_phase5(
    routes_path: Path,
    platform_path: Path,
    output_dir: Path,
    simulation_frames: int = 16,
) -> Dict[str, Any]:
    routes = read_json(routes_path)
    platform = Platform.load(platform_path)
    schedule = build_tdm_schedule(routes, platform)
    validation = validate_tdm_schedule(routes, platform, schedule)
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
    output_dir.mkdir(parents=True, exist_ok=True)
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
) -> Dict[str, Any]:
    return validate_tdm_schedule(
        read_json(routes_path),
        Platform.load(platform_path),
        read_json(schedule_path),
    )
