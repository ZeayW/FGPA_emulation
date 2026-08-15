"""Reusable stage runners for content-addressed Phase 6/7 experiments."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

from .academic_chimew import materialize_academic_chimew_inputs
from .chimew_pipeline import (
    run_chimew_phase6_pipeline,
    validate_chimew_phase6_pipeline,
)
from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .multi_fpga_physical_flow import (
    run_multi_fpga_physical_flow,
    validate_multi_fpga_physical_report,
)
from .phase3 import validate_phase3
from .phase4 import validate_phase4
from .phase5 import validate_phase5
from .phase6 import run_phase6, validate_phase6
from .phase7c import run_phase7c
from .pin_planning import (
    SIGNAL_POSITION_HINTS_SCHEMA,
    build_pin_plan,
    validate_pin_plan,
)
from .platform import Platform
from .vpr import VTR_HARD_BLOCK_PROFILE


EXPERIMENT_LOOKAHEAD_SCHEMA = "emuflow.experiment-physical-lookahead/v1"
EXPERIMENT_PHASE6_SCHEMA = "emuflow.experiment-phase6-checkpoint/v1"
EXPERIMENT_PHASE7_SCHEMA = "emuflow.experiment-phase7-checkpoint/v1"
_PROVIDERS = {"baseline", "placement-aware", "chimew"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise ValidationError(f"experiment stage artifact is missing: {relative}")
    return path


def _shared_paths(root: Path) -> Dict[str, Path]:
    return {
        "ir": _require_file(root, "frontend/phase1/design.emuir.json"),
        "clusters": _require_file(root, "partition/clusters.json"),
        "assignment": _require_file(root, "partition/assignment.json"),
        "phase3_report": _require_file(root, "partition/phase3_report.json"),
        "routes": _require_file(root, "system-route/routes.json"),
        "phase4_report": _require_file(root, "system-route/phase4_report.json"),
        "schedule": _require_file(root, "tdm/schedule.json"),
        "phase5_report": _require_file(root, "tdm/phase5_report.json"),
    }


def _timing_paths(root: Path) -> Path | None:
    path = root / "timing/cut-timing-paths.json"
    return path if path.is_file() else None


def _sta_path_database(root: Path) -> Path | None:
    path = root / "timing/path-database.json"
    return path if path.is_file() else None


def _board_link_timing(root: Path) -> Path | None:
    path = root / "timing/board-link-timing.json"
    return path if path.is_file() else None


def _prepare_empty_output(output_dir: Path, label: str) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists() and (
        not output_dir.is_dir() or any(output_dir.iterdir())
    ):
        raise EmuFlowError(f"{label} output must be an empty directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _placement_aware_positions(
    ir_path: Path,
    schedule_path: Path,
    placement_source_path: Path,
    *,
    region_count: int,
) -> Dict[str, Any]:
    ir = read_json(ir_path)
    schedule = read_json(schedule_path)
    placement_source = read_json(placement_source_path)
    locations = {
        fpga["fpga"]: {
            instance["id"]: float(instance["normalised_y"])
            for instance in fpga.get("instances", [])
        }
        for fpga in placement_source.get("fpgas", [])
    }
    net_by_id = {net["id"]: net for net in ir.get("nets", [])}

    def centroid(fpga: str, instances: list[str]) -> tuple[float, bool]:
        values = [
            locations[fpga][instance]
            for instance in instances
            if instance in locations.get(fpga, {})
        ]
        return ((sum(values) / len(values), False) if values else (0.5, True))

    hints = []
    fallbacks = 0
    for entry in sorted(schedule.get("entries", []), key=lambda item: item["id"]):
        net = net_by_id.get(entry.get("net"))
        if net is None:
            raise ValidationError(
                f"placement-aware entry {entry.get('id')!r} references an unknown net"
            )
        drivers = [
            endpoint["instance"]
            for endpoint in net.get("drivers", [])
            if endpoint.get("instance") is not None
        ]
        sinks = [
            endpoint["instance"]
            for endpoint in net.get("sinks", [])
            if endpoint.get("instance") is not None
        ]
        source_y, source_fallback = centroid(entry["from"], drivers)
        sink_y, sink_fallback = centroid(entry["to"], sinks)
        fallbacks += int(source_fallback) + int(sink_fallback)
        hints.append(
            {
                "schedule_entry": entry["id"],
                "source_y": source_y,
                "sink_y": sink_y,
                "source_region": min(region_count - 1, int(source_y * region_count)),
                "sink_region": min(region_count - 1, int(sink_y * region_count)),
                "source_fallback": source_fallback,
                "sink_fallback": sink_fallback,
            }
        )
    return {
        "schema": SIGNAL_POSITION_HINTS_SCHEMA,
        "design": schedule["design"],
        "platform": schedule["platform"],
        "provider": "openparf-lookahead-centroid-v1",
        "region_count": region_count,
        "metrics": {
            "signals": len(hints),
            "endpoint_centroid_fallbacks": fallbacks,
        },
        "entries": hints,
    }


def validate_shared_phase1_5(root: Path, platform_path: Path) -> Dict[str, Any]:
    root = root.resolve()
    paths = _shared_paths(root)
    platform_path = platform_path.resolve()
    validate_phase3(paths["ir"], platform_path, paths["clusters"], paths["assignment"])
    validate_phase4(
        paths["assignment"],
        platform_path,
        paths["routes"],
        timing_paths_path=_timing_paths(root),
    )
    ratio_plan = root / "tdm/ratio_plan.json"
    validate_phase5(
        paths["routes"],
        platform_path,
        paths["schedule"],
        ratio_plan_path=ratio_plan if ratio_plan.is_file() else None,
    )
    ir = EmuIR.load(paths["ir"])
    platform = Platform.load(platform_path)
    return {
        "status": "pass",
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "phase1_5_sha256": {
            label: _sha256(paths[label])
            for label in ("ir", "assignment", "routes", "schedule")
        },
    }


def run_physical_lookahead(
    shared_root: Path,
    baseline_phase6_root: Path | None,
    platform_path: Path,
    output_dir: Path,
    *,
    seed: int,
    workers: int,
    region_count: int,
    architecture: Path | None = None,
    architecture_id: str = VTR_HARD_BLOCK_PROFILE,
    yosys: str | None = None,
    vpr: str | None = None,
    architecture_importer: str | None = None,
    packed_importer: str | None = None,
    route_checker: str | None = None,
    openparf_install: Path | None = None,
    openparf_python: Path | None = None,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    shared = validate_shared_phase1_5(shared_root, platform_path)
    paths = _shared_paths(shared_root)
    split_root = (
        baseline_phase6_root / "split"
        if baseline_phase6_root is not None
        else shared_root / "split"
    )
    if baseline_phase6_root is not None:
        baseline = validate_phase6_checkpoint(
            baseline_phase6_root, shared_root, None, platform_path
        )
        if baseline["provider"] != "baseline":
            raise ValidationError("physical lookahead requires baseline Phase 6")
    output_dir = _prepare_empty_output(output_dir, "physical-lookahead")
    physical = run_multi_fpga_physical_flow(
        split_root,
        platform_path,
        paths["schedule"],
        output_dir / "physical",
        backend="open",
        architecture=architecture,
        architecture_id=architecture_id,
        yosys=yosys,
        vpr=vpr,
        architecture_importer=architecture_importer,
        packed_importer=packed_importer,
        route_checker=route_checker,
        openparf_install=openparf_install,
        openparf_python=openparf_python,
        seed=seed,
        route_channel_width=route_channel_width,
        workers=workers,
        original_ir_path=(
            paths["ir"] if _sta_path_database(shared_root) else None
        ),
        assignment_path=(
            paths["assignment"] if _sta_path_database(shared_root) else None
        ),
        routes_path=(
            paths["routes"] if _sta_path_database(shared_root) else None
        ),
        path_database_path=_sta_path_database(shared_root),
    )
    return _finish_physical_lookahead(
        shared_root,
        baseline_phase6_root,
        platform_path,
        output_dir,
        physical,
        seed=seed,
        workers=workers,
        region_count=region_count,
        architecture=architecture,
        architecture_id=architecture_id,
        route_channel_width=route_channel_width,
    )


def resume_physical_lookahead(
    shared_root: Path,
    baseline_phase6_root: Path | None,
    platform_path: Path,
    output_dir: Path,
    *,
    seed: int,
    workers: int,
    region_count: int,
    architecture: Path | None = None,
    architecture_id: str = VTR_HARD_BLOCK_PROFILE,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    """Finish a lookahead checkpoint around an independently resumed physical run."""

    output_dir = output_dir.expanduser().resolve()
    if not output_dir.is_dir() or {path.name for path in output_dir.iterdir()} != {
        "physical"
    }:
        raise ValidationError(
            "resumed physical-lookahead root must contain only physical/"
        )
    physical_root = output_dir / "physical"
    if not physical_root.is_dir():
        raise ValidationError("resumed physical-lookahead physical/ is missing")
    physical = read_json(
        _require_file(physical_root, "multi-fpga-physical-flow-report.json")
    )
    return _finish_physical_lookahead(
        shared_root,
        baseline_phase6_root,
        platform_path,
        output_dir,
        physical,
        seed=seed,
        workers=workers,
        region_count=region_count,
        architecture=architecture,
        architecture_id=architecture_id,
        route_channel_width=route_channel_width,
    )


def _finish_physical_lookahead(
    shared_root: Path,
    baseline_phase6_root: Path | None,
    platform_path: Path,
    output_dir: Path,
    physical: Dict[str, Any],
    *,
    seed: int,
    workers: int,
    region_count: int,
    architecture: Path | None,
    architecture_id: str,
    route_channel_width: int,
) -> Dict[str, Any]:
    shared = validate_shared_phase1_5(shared_root, platform_path)
    paths = _shared_paths(shared_root)
    split_root = (
        baseline_phase6_root / "split"
        if baseline_phase6_root is not None
        else shared_root / "split"
    )
    if baseline_phase6_root is not None:
        baseline = validate_phase6_checkpoint(
            baseline_phase6_root, shared_root, None, platform_path
        )
        if baseline["provider"] != "baseline":
            raise ValidationError("physical lookahead requires baseline Phase 6")
    validate_multi_fpga_physical_report(physical)
    if physical.get("execution", {}).get("requested_workers") != workers:
        raise ValidationError("resumed physical-lookahead worker count disagrees")
    physical_architecture = physical.get("architecture", {})
    expected_architecture_sha256 = (
        _sha256(architecture.expanduser().resolve())
        if architecture is not None
        else None
    )
    if expected_architecture_sha256 is not None and physical_architecture.get(
        "sha256"
    ) != expected_architecture_sha256:
        raise ValidationError("resumed physical-lookahead architecture disagrees")
    if physical.get("split_manifest", {}).get("sha256") != _sha256(
        split_root / "manifest.json"
    ):
        raise ValidationError("resumed physical-lookahead Phase 6 seal disagrees")
    for fpga in physical.get("fpgas", []):
        stages = fpga.get("stages", {})
        if stages.get("vpr_pack_place", {}).get("configuration", {}).get(
            "seed"
        ) != seed:
            raise ValidationError("resumed physical-lookahead VPR seed disagrees")
        if stages.get("vpr_route", {}).get("configuration", {}).get(
            "route_channel_width"
        ) != route_channel_width:
            raise ValidationError(
                "resumed physical-lookahead VPR channel width disagrees"
            )
    lookahead = materialize_academic_chimew_inputs(
        ir_path=paths["ir"],
        schedule_path=paths["schedule"],
        routes_path=paths["routes"],
        platform_path=platform_path,
        physical_report=physical,
        output_dir=output_dir / "lookahead",
        timing_paths_path=(
            shared_root / "timing/cut-timing-paths.json"
            if (shared_root / "timing/cut-timing-paths.json").is_file()
            else None
        ),
        region_count=region_count,
    )
    report = {
        "schema": EXPERIMENT_LOOKAHEAD_SCHEMA,
        "status": "pass",
        "seed": seed,
        "workers": workers,
        "region_count": region_count,
        "architecture_sha256": expected_architecture_sha256,
        "architecture_id": architecture_id,
        "route_channel_width": route_channel_width,
        "shared": shared,
        "baseline_phase6_manifest_sha256": _sha256(split_root / "manifest.json"),
        "physical_summary_sha256": _sha256(
            output_dir / "physical/physical-summary.json"
        ),
        "lookahead_report_sha256": _sha256(
            output_dir / "lookahead/academic-chimew-lookahead-report.json"
        ),
        "metrics": lookahead["metrics"],
    }
    write_json(output_dir / "experiment-lookahead-report.json", report)
    validate_physical_lookahead(
        output_dir, shared_root, baseline_phase6_root, platform_path
    )
    return report


def validate_physical_lookahead(
    root: Path,
    shared_root: Path,
    baseline_phase6_root: Path | None,
    platform_path: Path,
    *,
    expected_seed: int | None = None,
    expected_workers: int | None = None,
    expected_region_count: int | None = None,
    expected_architecture: Path | None = None,
    expected_route_channel_width: int | None = None,
) -> Dict[str, Any]:
    validate_shared_phase1_5(shared_root, platform_path)
    split_root = (
        baseline_phase6_root / "split"
        if baseline_phase6_root is not None
        else shared_root / "split"
    )
    if baseline_phase6_root is not None:
        baseline = validate_phase6_checkpoint(
            baseline_phase6_root, shared_root, None, platform_path
        )
        if baseline["provider"] != "baseline":
            raise ValidationError("physical lookahead requires baseline Phase 6")
    report = read_json(_require_file(root, "experiment-lookahead-report.json"))
    if report.get("schema") != EXPERIMENT_LOOKAHEAD_SCHEMA or report.get("status") != "pass":
        raise ValidationError("experiment physical-lookahead report is invalid")
    physical_path = _require_file(root, "physical/multi-fpga-physical-flow-report.json")
    physical_report = read_json(physical_path)
    validate_multi_fpga_physical_report(physical_report)
    expected = {
        "seed": expected_seed,
        "workers": expected_workers,
        "region_count": expected_region_count,
        "route_channel_width": expected_route_channel_width,
    }
    for field, value in expected.items():
        if value is not None and report.get(field) != value:
            raise ValidationError(
                f"experiment physical-lookahead {field} contract disagrees"
            )
    if expected_architecture is not None and report.get(
        "architecture_sha256"
    ) != _sha256(expected_architecture.resolve()):
        raise ValidationError(
            "experiment physical-lookahead architecture contract disagrees"
        )
    if expected_workers is not None and physical_report.get("execution", {}).get(
        "requested_workers"
    ) != expected_workers:
        raise ValidationError(
            "experiment physical-lookahead physical worker count disagrees"
        )
    if expected_seed is not None or expected_route_channel_width is not None:
        for fpga in physical_report.get("fpgas", []):
            stages = fpga.get("stages", {})
            if expected_seed is not None and stages.get(
                "vpr_pack_place", {}
            ).get("configuration", {}).get("seed") != expected_seed:
                raise ValidationError(
                    "experiment physical-lookahead VPR seed disagrees"
                )
            if expected_route_channel_width is not None and stages.get(
                "vpr_route", {}
            ).get("configuration", {}).get(
                "route_channel_width"
            ) != expected_route_channel_width:
                raise ValidationError(
                    "experiment physical-lookahead VPR channel width disagrees"
                )
    if report.get("physical_summary_sha256") != _sha256(
        _require_file(root, "physical/physical-summary.json")
    ):
        raise ValidationError("experiment physical-lookahead summary seal is broken")
    baseline_digest = report.get("baseline_phase6_manifest_sha256")
    split_manifest = split_root / "manifest.json"
    if split_manifest.is_file():
        if baseline_digest != _sha256(split_manifest):
            raise ValidationError(
                "experiment physical-lookahead Phase 6 seal is broken"
            )
    elif not isinstance(baseline_digest, str) or len(baseline_digest) != 64 or any(
        character not in "0123456789abcdef" for character in baseline_digest
    ):
        raise ValidationError(
            "experiment physical-lookahead Phase 6 digest is invalid"
        )
    lookahead_report = _require_file(
        root, "lookahead/academic-chimew-lookahead-report.json"
    )
    if report.get("lookahead_report_sha256") != _sha256(lookahead_report):
        raise ValidationError("experiment Chimew lookahead seal is broken")
    lookahead = read_json(lookahead_report)
    if lookahead.get("status") != "pass":
        raise ValidationError("experiment Chimew lookahead did not pass")
    for label in (
        "schedule",
        "crossings",
        "positions",
        "rudy_input",
        "bank_channel_input",
        "electrical_map",
    ):
        path = _require_file(root, f"lookahead/inputs/{label}.json")
        if lookahead.get("artifacts", {}).get(label, {}).get("sha256") != _sha256(path):
            raise ValidationError(f"experiment Chimew lookahead {label} seal is broken")
    return {"status": "pass", "seed": report["seed"], "metrics": report["metrics"]}


def run_phase6_checkpoint(
    shared_root: Path,
    lookahead_root: Path | None,
    platform_path: Path,
    output_dir: Path,
    *,
    provider: str,
    equivalence_cycles: int = 16,
    equivalence_seed: int = 20260727,
    pin_planner: str | None = None,
    chimew_grouper: str | None = None,
    chimew_refiner: str | None = None,
    chimew_rudy: str | None = None,
    chimew_assigner: str | None = None,
) -> Dict[str, Any]:
    if provider not in _PROVIDERS:
        raise ValidationError("experiment Phase 6 provider is invalid")
    shared = validate_shared_phase1_5(shared_root, platform_path)
    if provider == "baseline":
        lookahead = None
    else:
        if lookahead_root is None:
            raise ValidationError(
                f"experiment Phase 6 provider {provider} requires physical lookahead"
            )
        lookahead = validate_physical_lookahead(
            lookahead_root, shared_root, None, platform_path
        )
    paths = _shared_paths(shared_root)
    output_dir = _prepare_empty_output(output_dir, "Phase 6 checkpoint")
    schedule_path = paths["schedule"]
    pin_plan_path = None
    position_hints_path = None
    electrical_binding_path = None
    if provider == "placement-aware":
        assert lookahead_root is not None
        region_count = int(
            read_json(lookahead_root / "experiment-lookahead-report.json")[
                "region_count"
            ]
        )
        positions = _placement_aware_positions(
            paths["ir"],
            schedule_path,
            lookahead_root / "lookahead/sources/placement.json",
            region_count=region_count,
        )
        position_hints_path = output_dir / "placement-aware-position-hints.json"
        write_json(position_hints_path, positions)
        plan = build_pin_plan(
            read_json(schedule_path),
            Platform.load(platform_path),
            positions,
            executable=pin_planner,
        )
        pin_plan_path = output_dir / "placement-aware-pin-plan.json"
        write_json(pin_plan_path, plan)
    elif provider == "chimew":
        assert lookahead_root is not None
        inputs = lookahead_root / "lookahead/inputs"
        sources = lookahead_root / "lookahead/sources"
        pipeline_root = output_dir / "chimew-pipeline"
        pipeline = run_chimew_phase6_pipeline(
            inputs / "schedule.json",
            platform_path,
            inputs / "crossings.json",
            inputs / "positions.json",
            inputs / "rudy_input.json",
            inputs / "bank_channel_input.json",
            inputs / "electrical_map.json",
            pipeline_root,
            source_paths={
                "routing": sources / "routing.json",
                "placement": sources / "placement.json",
                "netlist": paths["ir"],
                "architecture": sources / "architecture.json",
                "package_pins": sources / "package-pins.json",
            },
            grouper=chimew_grouper,
            refiner=chimew_refiner,
            rudy=chimew_rudy,
            assigner=chimew_assigner,
            region_count=int(
                read_json(lookahead_root / "experiment-lookahead-report.json")[
                    "region_count"
                ]
            ),
        )
        schedule_path = inputs / "schedule.json"
        adapter = pipeline_root / "phase6-adapter"
        pin_plan_path = adapter / "pin_plan.json"
        position_hints_path = adapter / "position_hints.json"
        electrical_binding_path = adapter / "electrical_binding.json"
        if pipeline.get("status") != "pass":
            raise ValidationError("experiment Chimew pipeline did not pass")
    shutil.copy2(schedule_path, output_dir / "schedule.json")
    phase6 = run_phase6(
        paths["ir"],
        paths["assignment"],
        output_dir / "schedule.json",
        platform_path,
        output_dir / "split",
        equivalence_cycles=equivalence_cycles,
        equivalence_seed=equivalence_seed,
        pin_plan_path=pin_plan_path,
        position_hints_path=position_hints_path,
        electrical_binding_path=electrical_binding_path,
    )
    report = {
        "schema": EXPERIMENT_PHASE6_SCHEMA,
        "status": "pass",
        "provider": provider,
        "shared": shared,
        "lookahead": lookahead,
        "schedule_sha256": _sha256(output_dir / "schedule.json"),
        "manifest_sha256": _sha256(output_dir / "split/manifest.json"),
        "equivalence": phase6["equivalence"],
    }
    write_json(output_dir / "experiment-phase6-report.json", report)
    validate_phase6_checkpoint(output_dir, shared_root, lookahead_root, platform_path)
    return report


def validate_phase6_checkpoint(
    root: Path,
    shared_root: Path,
    lookahead_root: Path | None,
    platform_path: Path,
    *,
    expected_provider: str | None = None,
) -> Dict[str, Any]:
    validate_shared_phase1_5(shared_root, platform_path)
    report = read_json(_require_file(root, "experiment-phase6-report.json"))
    provider = report.get("provider")
    if report.get("schema") != EXPERIMENT_PHASE6_SCHEMA or provider not in _PROVIDERS:
        raise ValidationError("experiment Phase 6 checkpoint report is invalid")
    if expected_provider is not None and provider != expected_provider:
        raise ValidationError("experiment Phase 6 provider contract disagrees")
    if provider == "baseline":
        if report.get("lookahead") is not None:
            raise ValidationError("baseline Phase 6 must not depend on lookahead")
    else:
        if lookahead_root is None:
            raise ValidationError(
                f"experiment Phase 6 provider {provider} requires physical lookahead"
            )
        validate_physical_lookahead(
            lookahead_root, shared_root, None, platform_path
        )
    paths = _shared_paths(shared_root)
    manifest = _require_file(root, "split/manifest.json")
    validate_phase6(
        paths["ir"], paths["assignment"], root / "schedule.json", platform_path, manifest
    )
    if provider == "placement-aware":
        validate_pin_plan(
            read_json(root / "schedule.json"),
            Platform.load(platform_path),
            read_json(root / "placement-aware-position-hints.json"),
            read_json(root / "placement-aware-pin-plan.json"),
        )
    elif provider == "chimew":
        validate_chimew_phase6_pipeline(root / "chimew-pipeline")
    if report.get("schedule_sha256") != _sha256(root / "schedule.json") or report.get(
        "manifest_sha256"
    ) != _sha256(manifest):
        raise ValidationError("experiment Phase 6 checkpoint seal is broken")
    return {"status": "pass", "provider": provider, "equivalence": report["equivalence"]}


def run_phase7_checkpoint(
    shared_root: Path,
    lookahead_root: Path,
    phase6_root: Path,
    platform_path: Path,
    output_dir: Path,
    *,
    seed: int,
    workers: int,
    yosys: str | None = None,
    vpr: str | None = None,
    architecture_importer: str | None = None,
    packed_importer: str | None = None,
    route_checker: str | None = None,
    openparf_install: Path | None = None,
    openparf_python: Path | None = None,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    phase6 = validate_phase6_checkpoint(
        phase6_root, shared_root, lookahead_root, platform_path
    )
    paths = _shared_paths(shared_root)
    output_dir = _prepare_empty_output(output_dir, "Phase 7 checkpoint")
    lookahead_report = read_json(lookahead_root / "experiment-lookahead-report.json")
    if phase6["provider"] == "baseline" and seed == lookahead_report["seed"]:
        shutil.copytree(lookahead_root / "physical", output_dir / "physical")
    else:
        run_multi_fpga_physical_flow(
            phase6_root / "split",
            platform_path,
            phase6_root / "schedule.json",
            output_dir / "physical",
            backend="open",
            architecture=lookahead_root / "physical/architecture/vtr-flagship.xml",
            yosys=yosys,
            vpr=vpr,
            architecture_importer=architecture_importer,
            packed_importer=packed_importer,
            route_checker=route_checker,
            openparf_install=openparf_install,
            openparf_python=openparf_python,
            seed=seed,
            route_channel_width=route_channel_width,
            workers=workers,
            original_ir_path=(
                paths["ir"] if _sta_path_database(shared_root) else None
            ),
            assignment_path=(
                paths["assignment"] if _sta_path_database(shared_root) else None
            ),
            routes_path=(
                paths["routes"] if _sta_path_database(shared_root) else None
            ),
            path_database_path=_sta_path_database(shared_root),
        )
    runtime = run_phase7c(
        phase6_root / "schedule.json",
        platform_path,
        paths["phase3_report"],
        paths["phase4_report"],
        paths["phase5_report"],
        phase6_root / "split/phase6_report.json",
        output_dir / "runtime",
        physical_summary_path=output_dir / "physical/physical-summary.json",
        routes_path=paths["routes"],
        board_link_timing_path=_board_link_timing(shared_root),
    )
    if runtime.get("status") != "pass":
        raise ValidationError("experiment Phase 7C did not reach physical closure")
    report = {
        "schema": EXPERIMENT_PHASE7_SCHEMA,
        "status": "pass",
        "provider": phase6["provider"],
        "physical_seed": seed,
        "workers": workers,
        "route_channel_width": route_channel_width,
        "phase6_manifest_sha256": _sha256(phase6_root / "split/manifest.json"),
        "frozen_upstream": {
            "emuir_sha256": _sha256(paths["ir"]),
            "assignment_sha256": _sha256(paths["assignment"]),
            "routes_sha256": _sha256(paths["routes"]),
            "schedule_sha256": _sha256(phase6_root / "schedule.json"),
        },
        "physical_summary_sha256": _sha256(output_dir / "physical/physical-summary.json"),
        "qor_sha256": _sha256(output_dir / "runtime/qor_report.json"),
        "qor": read_json(output_dir / "runtime/qor_report.json"),
    }
    write_json(output_dir / "experiment-phase7-report.json", report)
    validate_phase7_checkpoint(
        output_dir, shared_root, lookahead_root, phase6_root, platform_path
    )
    return report


def validate_phase7_checkpoint(
    root: Path,
    shared_root: Path,
    lookahead_root: Path,
    phase6_root: Path,
    platform_path: Path,
    *,
    expected_seed: int | None = None,
    expected_workers: int | None = None,
    expected_route_channel_width: int | None = None,
) -> Dict[str, Any]:
    phase6 = validate_phase6_checkpoint(
        phase6_root, shared_root, lookahead_root, platform_path
    )
    report = read_json(_require_file(root, "experiment-phase7-report.json"))
    if (
        report.get("schema") != EXPERIMENT_PHASE7_SCHEMA
        or report.get("status") != "pass"
        or report.get("provider") != phase6["provider"]
    ):
        raise ValidationError("experiment Phase 7 checkpoint report is invalid")
    if expected_seed is not None and report.get("physical_seed") != expected_seed:
        raise ValidationError("experiment Phase 7 seed contract disagrees")
    if expected_workers is not None and report.get("workers") != expected_workers:
        raise ValidationError("experiment Phase 7 worker contract disagrees")
    if expected_route_channel_width is not None and report.get(
        "route_channel_width"
    ) != expected_route_channel_width:
        raise ValidationError("experiment Phase 7 channel-width contract disagrees")
    paths = _shared_paths(shared_root)
    expected_upstream = {
        "emuir_sha256": _sha256(paths["ir"]),
        "assignment_sha256": _sha256(paths["assignment"]),
        "routes_sha256": _sha256(paths["routes"]),
        "schedule_sha256": _sha256(phase6_root / "schedule.json"),
    }
    if report.get("frozen_upstream") != expected_upstream:
        raise ValidationError("experiment Phase 7 frozen-upstream seal is broken")
    physical_report = read_json(
        _require_file(root, "physical/multi-fpga-physical-flow-report.json")
    )
    validate_multi_fpga_physical_report(physical_report)
    if expected_workers is not None and physical_report.get("execution", {}).get(
        "requested_workers"
    ) != expected_workers:
        raise ValidationError("experiment Phase 7 physical worker count disagrees")
    if expected_seed is not None or expected_route_channel_width is not None:
        for fpga in physical_report.get("fpgas", []):
            stages = fpga.get("stages", {})
            if expected_seed is not None and stages.get(
                "vpr_pack_place", {}
            ).get("configuration", {}).get("seed") != expected_seed:
                raise ValidationError("experiment Phase 7 VPR seed disagrees")
            if expected_route_channel_width is not None and stages.get(
                "vpr_route", {}
            ).get("configuration", {}).get(
                "route_channel_width"
            ) != expected_route_channel_width:
                raise ValidationError("experiment Phase 7 VPR channel width disagrees")
    if report.get("physical_summary_sha256") != _sha256(
        root / "physical/physical-summary.json"
    ) or report.get("qor_sha256") != _sha256(root / "runtime/qor_report.json"):
        raise ValidationError("experiment Phase 7 checkpoint seal is broken")
    with tempfile.TemporaryDirectory() as temporary:
        replay = run_phase7c(
            phase6_root / "schedule.json",
            platform_path,
            paths["phase3_report"],
            paths["phase4_report"],
            paths["phase5_report"],
            phase6_root / "split/phase6_report.json",
            Path(temporary),
            physical_summary_path=root / "physical/physical-summary.json",
            routes_path=paths["routes"],
            board_link_timing_path=_board_link_timing(shared_root),
        )
        if replay.get("status") != "pass" or read_json(
            Path(temporary) / "qor_report.json"
        ) != read_json(root / "runtime/qor_report.json"):
            raise ValidationError("experiment Phase 7 QoR replay disagrees")
    return {
        "status": "pass",
        "provider": report["provider"],
        "physical_seed": report["physical_seed"],
        "qor": report["qor"],
    }
