"""Compile the canonical real-RTL/contest-BoardDB Phase 1-7 experiment DAG."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .errors import ValidationError
from .benchmark import BenchmarkRun
from .contest_public import PUBLIC_CONTEST_BOARDDB_REPORT_SCHEMA
from .end_to_end_validation_matrix import load_end_to_end_validation_matrix
from .experiment_dag import (
    EXPERIMENT_SPEC_V2_SCHEMA,
    validate_experiment_spec,
)
from .experiment_identity import build_implementation_closure
from .io import read_json, write_json


CANONICAL_EXPERIMENT_CONFIG_SCHEMA = "emuflow.canonical-experiment-config/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"canonical experiment {label} must be a file path")
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"canonical experiment {label} is not a regular file")
    return path


def _directory(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"canonical experiment {label} must be a directory path")
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_dir():
        raise ValidationError(f"canonical experiment {label} is not a directory")
    return path


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"canonical experiment {label} must be positive")
    return value


def _append_option(command: list[str], option: str, value: Any) -> None:
    if value is not None:
        command.extend((option, str(value)))


def _canonical_case_contract(
    repository_root: Path,
    case_id: str,
    rtl: Path,
    platform: Path,
    boarddb_report_path: Path,
    top: str,
    clocks: Sequence[str],
) -> Dict[str, Any]:
    matrix_path = repository_root / "benchmarks/end_to_end_validation_matrix.json"
    matrix, matrix_validation = load_end_to_end_validation_matrix(matrix_path)
    records = [record for record in matrix["cases"] if record["id"] == case_id]
    if len(records) != 1:
        raise ValidationError(
            "canonical experiment case_id is absent from the end-to-end matrix"
        )
    record = records[0]
    run_spec_path = repository_root / record["workload"]["run_spec"]
    run_spec = BenchmarkRun.load(run_spec_path).value
    if top != run_spec["top"] or list(clocks) != run_spec["clocks"]:
        raise ValidationError(
            "canonical experiment top/clocks do not match the matrix run spec"
        )
    source_names = {Path(value).name for value in run_spec["sources"]}
    if rtl.name not in source_names:
        raise ValidationError(
            "canonical experiment RTL does not match a matrix run-spec source"
        )

    contest_case_id = record["platform"]["contest_case_id"]
    boarddb_report = read_json(boarddb_report_path)
    if (
        boarddb_report.get("schema") != PUBLIC_CONTEST_BOARDDB_REPORT_SCHEMA
        or boarddb_report.get("status") != "pass"
        or boarddb_report.get("case_id") != contest_case_id
        or boarddb_report.get("gate") != "materialize-boarddb"
        or boarddb_report.get("qualification")
        != "academic-architecture-projection"
    ):
        raise ValidationError(
            "canonical experiment BoardDB report is not the matrix contest case"
        )
    contest_matrix_path = repository_root / record["platform"]["contest_matrix"]
    from .contest_validation_matrix import load_contest_validation_matrix

    _, contest_validation = load_contest_validation_matrix(contest_matrix_path)
    if boarddb_report.get("matrix_sha256") != contest_validation["matrix_sha256"]:
        raise ValidationError(
            "canonical experiment BoardDB report contest matrix is stale"
        )
    artifacts = boarddb_report.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValidationError("canonical experiment BoardDB artifact seal is missing")
    boarddb_artifacts = [
        item
        for item in artifacts
        if isinstance(item, dict) and item.get("path") == "boarddb.json"
    ]
    if (
        len(boarddb_artifacts) != 1
        or boarddb_artifacts[0].get("sha256") != _sha256(platform)
        or boarddb_artifacts[0].get("bytes") != platform.stat().st_size
    ):
        raise ValidationError(
            "canonical experiment platform bytes do not match the BoardDB report"
        )
    platform_document = read_json(platform)
    if (
        platform_document.get("schema") != "emuflow.boarddb/v1"
        or platform_document.get("platform", {}).get("name")
        != contest_case_id.replace(".", "-") + "-rtl"
    ):
        raise ValidationError(
            "canonical experiment platform is not the named contest projection"
        )
    return {
        "matrix_path": matrix_path,
        "matrix_sha256": matrix_validation["matrix_sha256"],
        "run_spec_path": run_spec_path,
        "contest_case_id": contest_case_id,
        "boarddb_report": boarddb_report,
    }


_COMPONENTS: Dict[str, Sequence[str]] = {
    "frontend": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/phase1.py",
        "src/emuflow/ir.py",
        "src/emuflow/platform.py",
        "src/emuflow/resources.py",
        "src/emuflow/yosys.py",
        "src/emuflow/synthesis.py",
        "src/emuflow/vpr.py",
        "src/emuflow/vtr_netlist.py",
        "scripts/yosys",
    ),
    "timing": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/opensta.py",
        "src/emuflow/sta.py",
        "src/emuflow/ir.py",
        "scripts/opensta",
    ),
    "partition": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/phase3.py",
        "src/emuflow/partition.py",
        "src/emuflow/partition_hops.py",
        "src/emuflow/tritonpart.py",
        "src/emuflow/routing.py",
        "src/native/hop_partition_refiner.cpp",
    ),
    "cut-timing": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/opensta.py",
        "src/emuflow/sta.py",
        "scripts/opensta",
    ),
    "route": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/phase4.py",
        "src/emuflow/routing.py",
        "src/emuflow/timing_routing.py",
        "src/native/tlr_router.cpp",
    ),
    "tdm": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/phase5.py",
        "src/emuflow/tdm.py",
        "src/emuflow/tdm_ratio.py",
        "src/emuflow/tdm_timing_dag.py",
        "src/emuflow/tdm_slot.py",
        "src/native/tdm_ratio_optimizer.cpp",
        "src/native/tdm_timing_dag_optimizer.cpp",
        "src/native/tdm_slot_optimizer.cpp",
    ),
    "shared": (
        "src/emuflow/experiment_upstream.py",
        "src/emuflow/experiment_stages.py",
        "src/emuflow/phase3.py",
        "src/emuflow/phase4.py",
        "src/emuflow/phase5.py",
    ),
    "phase6": (
        "src/emuflow/experiment_stages.py",
        "src/emuflow/phase6.py",
        "src/emuflow/netlist.py",
        "src/emuflow/runtime.py",
        "src/emuflow/equivalence.py",
        "src/emuflow/pin_planning.py",
        "src/emuflow/chimew_pipeline.py",
        "src/emuflow/chimew_phase6.py",
        "src/emuflow/chimew_qualification.py",
        "rtl/transport",
        "src/native/placement_aware_pin_planner.cpp",
        "src/native/chimew_bank_channel_assigner.cpp",
        "src/native/chimew_position_refiner.cpp",
        "src/native/chimew_rudy.cpp",
        "src/native/chimew_signal_grouper.cpp",
    ),
    "lookahead": (
        "src/emuflow/experiment_stages.py",
        "src/emuflow/academic_chimew.py",
        "src/emuflow/multi_fpga_physical_flow.py",
        "src/emuflow/physical_backend.py",
        "src/emuflow/openparf.py",
        "src/emuflow/packed_placement.py",
        "src/emuflow/vpr.py",
        "scripts/openparf",
        "src/native/vtr_architecture_importer.cpp",
        "src/native/vpr_packed_netlist_importer.cpp",
        "src/native/vpr_route_checker.cpp",
    ),
    "phase7": (
        "src/emuflow/experiment_stages.py",
        "src/emuflow/multi_fpga_physical_flow.py",
        "src/emuflow/physical_backend.py",
        "src/emuflow/openparf.py",
        "src/emuflow/packed_placement.py",
        "src/emuflow/phase7c.py",
        "src/emuflow/system_timing.py",
        "src/emuflow/vpr.py",
        "scripts/openparf",
        "src/native/vtr_architecture_importer.cpp",
        "src/native/vpr_packed_netlist_importer.cpp",
        "src/native/vpr_route_checker.cpp",
    ),
}


def _closure(repository_root: Path, stage: str) -> Dict[str, Any]:
    return build_implementation_closure(repository_root, _COMPONENTS[stage])


def _artifact(path: str, role: str) -> Dict[str, str]:
    retention = {
        "consumer-checkpoint": "required",
        "evidence-critical": "required",
        "diagnostic": "optional",
    }[role]
    return {"path": path, "role": role, "retention": retention}


def compile_canonical_experiment_spec(
    config_path: Path, repository_root: Path, output_path: Path
) -> Dict[str, Any]:
    config = read_json(config_path)
    if config.get("schema") != CANONICAL_EXPERIMENT_CONFIG_SCHEMA:
        raise ValidationError("canonical experiment config schema is invalid")
    repository_root = _directory(str(repository_root), "repository_root")
    case_id = config.get("case_id")
    source_commit = config.get("source_commit")
    if not isinstance(case_id, str) or not case_id:
        raise ValidationError("canonical experiment case_id is invalid")
    if not isinstance(source_commit, str) or len(source_commit) != 40:
        raise ValidationError("canonical experiment source_commit is invalid")
    rtl = _file(config.get("rtl_source"), "rtl_source")
    platform = _file(config.get("platform"), "platform")
    boarddb_report_path = _file(
        config.get("boarddb_report"), "boarddb_report"
    )
    timing_model = _file(config.get("timing_model"), "timing_model")
    architecture_timing = _file(
        config.get("architecture_timing_db"), "architecture_timing_db"
    )
    physical_architecture = _file(
        config.get("physical_architecture"), "physical_architecture"
    )
    tools_raw = config.get("tools")
    if not isinstance(tools_raw, dict):
        raise ValidationError("canonical experiment tools must be an object")
    required_tools = {
        "emuflow",
        "yosys",
        "opensta",
        "openroad",
        "router",
        "ratio_optimizer",
        "timing_dag_optimizer",
        "slot_optimizer",
        "vpr",
        "architecture_importer",
        "packed_importer",
        "route_checker",
        "openparf_python",
    }
    if set(tools_raw) != required_tools:
        raise ValidationError(
            "canonical experiment tools must exactly cover " + ", ".join(sorted(required_tools))
        )
    tools = {label: _file(value, f"tool {label}") for label, value in tools_raw.items()}
    openparf_install = _directory(config.get("openparf_install"), "openparf_install")
    openparf_manifest = _file(config.get("openparf_manifest"), "openparf_manifest")
    top = config.get("top")
    if not isinstance(top, str) or not top:
        raise ValidationError("canonical experiment top is invalid")
    clocks = config.get("clocks")
    periods = config.get("clock_periods")
    if (
        not isinstance(clocks, list)
        or not clocks
        or not all(isinstance(item, str) and item for item in clocks)
        or len(clocks) != len(set(clocks))
        or not isinstance(periods, dict)
        or set(periods) != set(clocks)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in periods.values()
        )
    ):
        raise ValidationError("canonical experiment clocks/periods are invalid")
    contract = _canonical_case_contract(
        repository_root,
        case_id,
        rtl,
        platform,
        boarddb_report_path,
        top,
        clocks,
    )
    workers = _positive_integer(config.get("physical_workers", 8), "physical_workers")
    channel_width = _positive_integer(
        config.get("physical_route_channel_width", 300),
        "physical_route_channel_width",
    )
    region_count = _positive_integer(config.get("region_count", 4), "region_count")
    partition_seed = config.get("partition_seed", 0)
    if isinstance(partition_seed, bool) or not isinstance(partition_seed, int) or partition_seed < 0:
        raise ValidationError("canonical experiment partition_seed is invalid")
    executable = str(tools["emuflow"])
    base_inputs = {
        "rtl": _sha256(rtl),
        "platform": _sha256(platform),
        "boarddb_report": _sha256(boarddb_report_path),
        "end_to_end_matrix": contract["matrix_sha256"],
        "benchmark_run_spec": _sha256(contract["run_spec_path"]),
        "timing_model": _sha256(timing_model),
        "architecture_timing_db": _sha256(architecture_timing),
        "physical_architecture": _sha256(physical_architecture),
        "openparf_manifest": _sha256(openparf_manifest),
        **{f"tool.{label}": _sha256(path) for label, path in sorted(tools.items())},
    }
    closures = {stage: _closure(repository_root, stage) for stage in _COMPONENTS}

    nodes: list[Dict[str, Any]] = []

    def node(
        node_id: str,
        stage: str,
        dependencies: Sequence[str],
        command: list[str],
        validator: list[str],
        artifacts: list[Dict[str, str]],
        *,
        inputs: Sequence[str] = (),
        configuration: Mapping[str, Any] | None = None,
        peak_gib: int,
        retained_gib: int,
        provider: str | None = None,
        physical_seed: int | None = None,
    ) -> None:
        record: Dict[str, Any] = {
            "id": node_id,
            "stage": stage,
            "dependencies": list(dependencies),
            "inputs": {label: base_inputs[label] for label in sorted(inputs)},
            "configuration": dict(configuration or {}),
            "implementation": closures[stage],
            "command": command,
            "validator_implementation": closures[stage],
            "validator": validator,
            "environment": {"EMUFLOW_EXPERIMENT_POLICY": "canonical-real-rtl-v1"},
            "storage_estimate": {
                "peak_bytes": peak_gib * 1024**3,
                "retained_bytes": retained_gib * 1024**3,
            },
            "artifacts": artifacts,
        }
        if provider is not None:
            record["provider"] = provider
        if physical_seed is not None:
            record["physical_seed"] = physical_seed
        nodes.append(record)

    frontend_command = [
        executable, "experiment-stage", "frontend-run",
        "--platform", str(platform), "--source", str(rtl), "--top", top,
        "--mapping-profile", "vtr-hard-blocks", "--yosys", str(tools["yosys"]),
    ]
    for clock in clocks:
        frontend_command.extend(("--clock", clock))
    frontend_command.extend(("--out", "{output_dir}"))
    node(
        "frontend", "frontend", [], frontend_command,
        [executable, "experiment-stage", "frontend-validate", "{artifact_root}", "--platform", str(platform)],
        [_artifact("phase1", "consumer-checkpoint"), _artifact("synthesized.json", "consumer-checkpoint"), _artifact("experiment-frontend-report.json", "evidence-critical")],
        inputs=("rtl", "platform", "boarddb_report", "end_to_end_matrix", "benchmark_run_spec", "tool.emuflow", "tool.yosys"),
        configuration={"case_id": case_id, "contest_case_id": contract["contest_case_id"], "top": top, "clocks": clocks, "mapping_profile": "vtr-hard-blocks", "require_no_fabric_clock": True},
        peak_gib=16, retained_gib=4,
    )
    period_args = [f"{clock}={float(periods[clock]):.12g}" for clock in clocks]
    timing_command = [
        executable, "experiment-stage", "timing-run", "--frontend", "{dependency:frontend}",
        "--timing-model", str(timing_model), "--architecture-timing-db", str(architecture_timing),
        "--opensta", str(tools["opensta"]),
    ]
    for value in period_args:
        timing_command.extend(("--clock-period", value))
    timing_command.extend(("--out", "{output_dir}"))
    node(
        "timing", "timing", ["frontend"], timing_command,
        [executable, "experiment-stage", "timing-validate", "{artifact_root}", "--frontend", "{dependency:frontend}"],
        [_artifact("path-database.json", "consumer-checkpoint"), _artifact("partition-net-weights.json", "consumer-checkpoint"), _artifact("experiment-timing-report.json", "evidence-critical")],
        inputs=("timing_model", "architecture_timing_db", "tool.emuflow", "tool.opensta"),
        configuration={"clock_periods": periods, "max_paths": 200000, "criticality_scale": 9.0, "criticality_exponent": 2.0},
        peak_gib=16, retained_gib=4,
    )
    partition_command = [
        executable, "experiment-stage", "partition-run", "--frontend", "{dependency:frontend}",
        "--timing", "{dependency:timing}", "--platform", str(platform),
        "--provider", "tritonpart", "--seed", str(partition_seed), "--openroad", str(tools["openroad"]),
        "--out", "{output_dir}",
    ]
    node(
        "partition", "partition", ["frontend", "timing"], partition_command,
        [executable, "experiment-stage", "partition-validate", "{artifact_root}", "--frontend", "{dependency:frontend}", "--timing", "{dependency:timing}", "--platform", str(platform)],
        [_artifact("clusters.json", "consumer-checkpoint"), _artifact("constraints.normalized.json", "consumer-checkpoint"), _artifact("assignment.json", "consumer-checkpoint"), _artifact("phase3_report.json", "consumer-checkpoint"), _artifact("experiment-partition-report.json", "evidence-critical")],
        inputs=("platform", "tool.emuflow", "tool.openroad"),
        configuration={"provider": "tritonpart", "seed": partition_seed, "timeout_seconds": 3600, "num_initial_solutions": 50, "num_best_initial_solutions": 10},
        peak_gib=24, retained_gib=6,
    )
    cut_command = [
        executable, "experiment-stage", "cut-timing-run", "--frontend", "{dependency:frontend}",
        "--timing", "{dependency:timing}", "--partition", "{dependency:partition}",
        "--timing-model", str(timing_model), "--architecture-timing-db", str(architecture_timing),
        "--opensta", str(tools["opensta"]),
    ]
    for value in period_args:
        cut_command.extend(("--clock-period", value))
    cut_command.extend(("--out", "{output_dir}"))
    node(
        "cut-timing", "cut-timing", ["frontend", "timing", "partition"], cut_command,
        [executable, "experiment-stage", "cut-timing-validate", "{artifact_root}", "--frontend", "{dependency:frontend}", "--partition", "{dependency:partition}"],
        [_artifact("cut-path-database.json", "consumer-checkpoint"), _artifact("cut-timing-paths.json", "consumer-checkpoint"), _artifact("experiment-cut-timing-report.json", "evidence-critical")],
        inputs=("timing_model", "architecture_timing_db", "tool.emuflow", "tool.opensta"),
        configuration={"clock_periods": periods, "max_paths": 200000},
        peak_gib=16, retained_gib=4,
    )
    node(
        "route", "route", ["partition", "cut-timing"],
        [executable, "experiment-stage", "route-run", "--partition", "{dependency:partition}", "--cut-timing", "{dependency:cut-timing}", "--platform", str(platform), "--router", str(tools["router"]), "--out", "{output_dir}"],
        [executable, "experiment-stage", "route-validate", "{artifact_root}", "--partition", "{dependency:partition}", "--cut-timing", "{dependency:cut-timing}", "--platform", str(platform)],
        [_artifact("routes.json", "consumer-checkpoint"), _artifact("phase4_report.json", "consumer-checkpoint"), _artifact("experiment-route-report.json", "evidence-critical")],
        inputs=("platform", "tool.emuflow", "tool.router"),
        configuration={"provider": "route-tdm-timing-cooptimization-v1"},
        peak_gib=12, retained_gib=3,
    )
    node(
        "tdm", "tdm", ["route"],
        [executable, "experiment-stage", "tdm-run", "--route", "{dependency:route}", "--platform", str(platform), "--ratio-optimizer", str(tools["ratio_optimizer"]), "--timing-dag-optimizer", str(tools["timing_dag_optimizer"]), "--slot-optimizer", str(tools["slot_optimizer"]), "--out", "{output_dir}"],
        [executable, "experiment-stage", "tdm-validate", "{artifact_root}", "--route", "{dependency:route}", "--platform", str(platform)],
        [_artifact("schedule.json", "consumer-checkpoint"), _artifact("ratio_plan.json", "consumer-checkpoint"), _artifact("phase5_report.json", "consumer-checkpoint"), _artifact("experiment-tdm-report.json", "evidence-critical")],
        inputs=("platform", "tool.emuflow", "tool.ratio_optimizer", "tool.timing_dag_optimizer", "tool.slot_optimizer"),
        configuration={"simulation_frames": 16, "ratio_max_iterations": 500, "ratio_quantum": 8, "post_refinement_iterations": 200},
        peak_gib=12, retained_gib=3,
    )
    shared_dependencies = ["frontend", "timing", "partition", "cut-timing", "route", "tdm"]
    node(
        "shared-phase1-5", "shared", shared_dependencies,
        [executable, "experiment-stage", "shared-materialize", "--frontend", "{dependency:frontend}", "--timing", "{dependency:timing}", "--partition", "{dependency:partition}", "--cut-timing", "{dependency:cut-timing}", "--route", "{dependency:route}", "--tdm", "{dependency:tdm}", "--platform", str(platform), "--out", "{output_dir}"],
        [executable, "experiment-stage", "shared-validate", "--shared", "{artifact_root}", "--platform", str(platform)],
        [_artifact("frontend", "consumer-checkpoint"), _artifact("timing", "consumer-checkpoint"), _artifact("partition", "consumer-checkpoint"), _artifact("system-route", "consumer-checkpoint"), _artifact("tdm", "consumer-checkpoint"), _artifact("experiment-shared-report.json", "evidence-critical")],
        inputs=("platform", "tool.emuflow"), configuration={"materialization": "same-filesystem-hardlink-or-copy"}, peak_gib=2, retained_gib=1,
    )

    baseline_command = [executable, "experiment-stage", "phase6-run", "--shared", "{dependency:shared-phase1-5}", "--platform", str(platform), "--provider", "baseline", "--out", "{output_dir}"]
    node(
        "phase6-baseline", "phase6", ["shared-phase1-5"], baseline_command,
        [executable, "experiment-stage", "phase6-validate", "{artifact_root}", "--shared", "{dependency:shared-phase1-5}", "--platform", str(platform)],
        [_artifact("split", "consumer-checkpoint"), _artifact("schedule.json", "consumer-checkpoint"), _artifact("experiment-phase6-report.json", "evidence-critical")],
        inputs=("platform", "tool.emuflow"), configuration={"provider": "baseline", "equivalence_cycles": 16}, peak_gib=12, retained_gib=4, provider="baseline",
    )
    lookahead_command = [
        executable, "experiment-stage", "lookahead-run", "--shared", "{dependency:shared-phase1-5}",
        "--baseline-phase6", "{dependency:phase6-baseline}", "--platform", str(platform),
        "--seed", "1", "--workers", str(workers), "--region-count", str(region_count),
        "--architecture", str(physical_architecture), "--yosys", str(tools["yosys"]), "--vpr", str(tools["vpr"]),
        "--architecture-importer", str(tools["architecture_importer"]), "--packed-importer", str(tools["packed_importer"]),
        "--route-checker", str(tools["route_checker"]), "--openparf-install", str(openparf_install),
        "--openparf-python", str(tools["openparf_python"]), "--route-channel-width", str(channel_width), "--out", "{output_dir}",
    ]
    node(
        "physical-lookahead", "lookahead", ["shared-phase1-5", "phase6-baseline"], lookahead_command,
        [executable, "experiment-stage", "lookahead-validate", "{artifact_root}", "--shared", "{dependency:shared-phase1-5}", "--baseline-phase6", "{dependency:phase6-baseline}", "--platform", str(platform)],
        [_artifact("physical", "consumer-checkpoint"), _artifact("lookahead", "consumer-checkpoint"), _artifact("experiment-lookahead-report.json", "evidence-critical")],
        inputs=("platform", "physical_architecture", "openparf_manifest", "tool.emuflow", "tool.yosys", "tool.vpr", "tool.architecture_importer", "tool.packed_importer", "tool.route_checker", "tool.openparf_python"),
        configuration={"physical_seed": 1, "physical_workers": workers, "region_count": region_count, "route_channel_width": channel_width}, peak_gib=48, retained_gib=10,
    )
    for provider in ("placement-aware", "chimew"):
        phase6_id = f"phase6-{provider}"
        extra_artifacts = (
            [_artifact("placement-aware-position-hints.json", "consumer-checkpoint"), _artifact("placement-aware-pin-plan.json", "consumer-checkpoint")]
            if provider == "placement-aware"
            else [_artifact("chimew-pipeline", "consumer-checkpoint")]
        )
        node(
            phase6_id, "phase6", ["shared-phase1-5", "physical-lookahead"],
            [executable, "experiment-stage", "phase6-run", "--shared", "{dependency:shared-phase1-5}", "--lookahead", "{dependency:physical-lookahead}", "--platform", str(platform), "--provider", provider, "--out", "{output_dir}"],
            [executable, "experiment-stage", "phase6-validate", "{artifact_root}", "--shared", "{dependency:shared-phase1-5}", "--lookahead", "{dependency:physical-lookahead}", "--platform", str(platform)],
            [_artifact("split", "consumer-checkpoint"), _artifact("schedule.json", "consumer-checkpoint"), _artifact("experiment-phase6-report.json", "evidence-critical"), *extra_artifacts],
            inputs=("platform", "tool.emuflow"), configuration={"provider": provider, "equivalence_cycles": 16}, peak_gib=12, retained_gib=4, provider=provider,
        )
    for provider in ("baseline", "placement-aware", "chimew"):
        for seed in (1, 2, 3):
            phase6_id = f"phase6-{provider}"
            phase7_id = f"phase7-{provider}-seed{seed}"
            node(
                phase7_id, "phase7", ["shared-phase1-5", "physical-lookahead", phase6_id],
                [executable, "experiment-stage", "phase7-run", "--shared", "{dependency:shared-phase1-5}", "--lookahead", "{dependency:physical-lookahead}", "--phase6", f"{{dependency:{phase6_id}}}", "--platform", str(platform), "--seed", str(seed), "--workers", str(workers), "--yosys", str(tools["yosys"]), "--vpr", str(tools["vpr"]), "--architecture-importer", str(tools["architecture_importer"]), "--packed-importer", str(tools["packed_importer"]), "--route-checker", str(tools["route_checker"]), "--openparf-install", str(openparf_install), "--openparf-python", str(tools["openparf_python"]), "--route-channel-width", str(channel_width), "--out", "{output_dir}"],
                [executable, "experiment-stage", "phase7-validate", "{artifact_root}", "--shared", "{dependency:shared-phase1-5}", "--lookahead", "{dependency:physical-lookahead}", "--phase6", f"{{dependency:{phase6_id}}}", "--platform", str(platform)],
                [_artifact("runtime", "evidence-critical"), _artifact("experiment-phase7-report.json", "evidence-critical"), _artifact("physical", "diagnostic")],
                inputs=("platform", "openparf_manifest", "tool.emuflow", "tool.yosys", "tool.vpr", "tool.architecture_importer", "tool.packed_importer", "tool.route_checker", "tool.openparf_python"),
                configuration={"physical_backend": "open", "physical_workers": workers, "physical_seed": seed, "route_channel_width": channel_width},
                peak_gib=48, retained_gib=8, provider=provider, physical_seed=seed,
            )
    spec = {
        "schema": EXPERIMENT_SPEC_V2_SCHEMA,
        "experiment_id": case_id,
        "source_commit": source_commit,
        "nodes": nodes,
    }
    validated = validate_experiment_spec(spec)
    write_json(output_path, spec)
    return {
        "status": "pass",
        "experiment_id": case_id,
        "nodes": len(validated["nodes"]),
        "terminal_nodes": 9,
        "output": str(output_path.resolve()),
    }
