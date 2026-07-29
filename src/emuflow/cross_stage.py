"""Checked Phase 3--5 candidate evaluation and feedback orchestration."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .ir import EmuIR
from .partition import (
    PARTITION_ASSIGNMENT_SCHEMA,
    build_clusters,
    load_partition_constraints,
    validate_partition_artifacts,
)
from .partition_feedback import run_partition_feedback
from .phase3 import run_phase3
from .phase4 import run_phase4
from .phase5 import run_phase5
from .platform import Platform
from .routing import validate_system_routes
from .sta import (
    STA_PATH_DATABASE_SCHEMA,
    _normalized_slack,
    _validate_database_normalization,
    project_sta_path_database,
)
from .tdm import validate_tdm_schedule
from .tdm_ratio import TDM_RATIO_PROVIDER, validate_tdm_ratio_plan
from .timing_routing import ROUTE_TDM_PROVIDER


CROSS_STAGE_CANDIDATE_SCHEMA = "emuflow.cross-stage-candidate/v1"
CROSS_STAGE_REPORT_SCHEMA = "emuflow.cross-stage-report/v1"
CROSS_STAGE_OBJECTIVE = (
    "lexicographic(all-path worst normalized slack, all-path total "
    "negative normalized slack, negative path count, maximum TDM ratio, "
    "completion slot, link bit-hops, cut bits, replica LUTs)"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _link_delay_ns(
    platform: Platform,
    link_id: str,
    constraints: Mapping[str, Any],
) -> float:
    overrides = constraints.get("link_delay_ns", {})
    if not isinstance(overrides, dict):
        raise ValidationError("routes.constraints.link_delay_ns is invalid")
    if link_id in overrides:
        value = overrides[link_id]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise ValidationError(
                f"routes.constraints link delay {link_id!r} is invalid"
            )
        return float(value)
    link = next(
        (candidate for candidate in platform.links if candidate.id == link_id),
        None,
    )
    if link is None:
        raise ValidationError(f"route references unknown link {link_id!r}")
    return link.latency_cycles * 1000.0 / link.fabric_clock_mhz


def _scheduled_transport_delay_by_net(
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    platform: Platform,
) -> Dict[str, float]:
    link_by_id = {link.id: link for link in platform.links}
    entries = {}
    for index, entry in enumerate(schedule.get("entries", [])):
        if not isinstance(entry, dict):
            raise ValidationError(f"schedule.entries[{index}] is invalid")
        key = (
            entry.get("demand"),
            entry.get("link"),
            entry.get("from"),
            entry.get("to"),
        )
        if key in entries:
            raise ValidationError(f"schedule has duplicate routed hop {key}")
        entries[key] = entry

    constraints = routes.get("constraints")
    if not isinstance(constraints, dict):
        raise ValidationError("routes.constraints is invalid")
    delay_by_net = {}
    for route in routes.get("routes", []):
        graph = defaultdict(list)
        for edge in route["tree_edges"]:
            graph[edge["from"]].append(edge)
        arrival = {route["source"]: 0.0}
        queue = deque([route["source"]])
        while queue:
            node = queue.popleft()
            for edge in sorted(
                graph[node],
                key=lambda item: (
                    item["to"],
                    item["link"],
                ),
            ):
                key = (
                    route["id"],
                    edge["link"],
                    edge["from"],
                    edge["to"],
                )
                entry = entries.get(key)
                if entry is None:
                    raise ValidationError(
                        f"schedule is missing routed hop {key}"
                    )
                wait_slots = entry.get("slot", -1) - entry.get(
                    "ready_slot", 0
                )
                if wait_slots < 0:
                    raise ValidationError(
                        f"schedule routed hop {key} has negative wait"
                    )
                link = link_by_id[edge["link"]]
                edge_delay = _link_delay_ns(
                    platform, edge["link"], constraints
                )
                edge_delay += (
                    1000.0 / link.fabric_clock_mhz
                ) * wait_slots
                arrival[edge["to"]] = arrival[node] + edge_delay
                queue.append(edge["to"])
        delay_by_net[route["net"]] = max(
            arrival[sink] for sink in route["sinks"]
        )
    return delay_by_net


def _path_metrics(
    database: Mapping[str, Any],
    assignment: Mapping[str, Any],
    transport_delay_by_net: Mapping[str, float],
) -> Dict[str, Any]:
    normalization = _validate_database_normalization(
        database.get("normalization")
    )
    cut_nets = {
        cut["net"]
        for cut in assignment.get("cut_nets", [])
        if isinstance(cut, dict) and isinstance(cut.get("net"), str)
    }
    if cut_nets != set(transport_delay_by_net):
        missing = sorted(cut_nets - set(transport_delay_by_net))
        extra = sorted(set(transport_delay_by_net) - cut_nets)
        raise ValidationError(
            "candidate route/cut coverage mismatch: "
            f"missing={missing[:8]} extra={extra[:8]}"
        )

    raw_paths = database.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValidationError("STA path database paths are invalid")
    records = []
    path_ids = set()
    for index, path in enumerate(raw_paths):
        if not isinstance(path, dict):
            raise ValidationError(
                f"STA path database paths[{index}] is invalid"
            )
        path_id = path.get("id")
        period = path.get("clock_period_ns")
        slack = path.get("slack_ns")
        fixed_delay = path.get("fixed_delay_ns")
        normalized = path.get("normalized_slack")
        if (
            not isinstance(path_id, str)
            or not path_id
            or path_id in path_ids
        ):
            raise ValidationError(
                f"STA path database paths[{index}].id is invalid"
            )
        path_ids.add(path_id)
        for name, value in (
            ("clock_period_ns", period),
            ("slack_ns", slack),
            ("fixed_delay_ns", fixed_delay),
            ("normalized_slack", normalized),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValidationError(
                    f"STA path database paths[{index}].{name} is invalid"
                )
        if float(period) <= 0.0 or float(fixed_delay) < 0.0:
            raise ValidationError(
                f"STA path database paths[{index}] timing is invalid"
            )
        expected_normalized = _normalized_slack(
            float(period), float(slack), normalization
        )
        if abs(float(normalized) - expected_normalized) > 1.0e-12:
            raise ValidationError(
                f"STA path database paths[{index}].normalized_slack "
                "is inconsistent"
            )
        path_nets = path.get("path_nets")
        if (
            not isinstance(path_nets, list)
            or not path_nets
            or not all(isinstance(net, str) and net for net in path_nets)
            or len(path_nets) != len(set(path_nets))
        ):
            raise ValidationError(
                f"STA path database paths[{index}].path_nets is invalid"
            )
        crossed = [net for net in path_nets if net in cut_nets]
        delay = float(fixed_delay) + sum(
            transport_delay_by_net[net] for net in crossed
        )
        realized_slack = float(period) - delay
        realized_normalized = _normalized_slack(
            float(period),
            realized_slack,
            normalization,
        )
        records.append(
            {
                "path": path_id,
                "crossed_cut_nets": len(crossed),
                "delay_ns": delay,
                "slack_ns": realized_slack,
                "normalized_slack": realized_normalized,
            }
        )
    ordered = sorted(
        records,
        key=lambda item: (item["normalized_slack"], item["path"]),
    )
    normalized_values = sorted(
        record["normalized_slack"] for record in records
    )
    negative = [
        record for record in records if record["slack_ns"] < 0.0
    ]
    worst = ordered[0]
    return {
        "all_paths": len(records),
        "crossing_paths": sum(
            record["crossed_cut_nets"] > 0 for record in records
        ),
        "no_cut_paths": sum(
            record["crossed_cut_nets"] == 0 for record in records
        ),
        "negative_slack_paths": len(negative),
        "worst_path": worst["path"],
        "worst_delay_ns": worst["delay_ns"],
        "worst_slack_ns": worst["slack_ns"],
        "worst_normalized_slack": worst["normalized_slack"],
        "total_negative_normalized_slack": sum(
            record["normalized_slack"] for record in negative
        ),
        "p01_normalized_slack": normalized_values[
            len(normalized_values) // 100
        ],
        "median_normalized_slack": normalized_values[
            len(normalized_values) // 2
        ],
    }


def _objective_metrics(
    path_metrics: Mapping[str, Any],
    assignment: Mapping[str, Any],
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
) -> Dict[str, Any]:
    route_metrics = routes.get("metrics", {})
    schedule_metrics = schedule.get("metrics", {})
    ratios = [
        entry.get("tdm_ratio", 1)
        for entry in schedule.get("entries", [])
    ]
    replication = assignment.get("replication", {})
    replication_metrics = (
        replication.get("metrics", {})
        if isinstance(replication, dict)
        else {}
    )
    replica_luts = replication_metrics.get("replica_luts", 0)
    if (
        isinstance(replica_luts, bool)
        or not isinstance(replica_luts, int)
        or replica_luts < 0
    ):
        raise ValidationError("assignment replica LUT count is invalid")
    return {
        "worst_normalized_slack": path_metrics[
            "worst_normalized_slack"
        ],
        "total_negative_normalized_slack": path_metrics[
            "total_negative_normalized_slack"
        ],
        "negative_slack_paths": path_metrics["negative_slack_paths"],
        "max_tdm_ratio": max(ratios, default=1),
        "completion_slot": schedule_metrics["completion_slot"],
        "total_link_bit_hops": route_metrics["total_link_bit_hops"],
        "cut_bits": sum(
            int(route["width_bits"]) for route in routes["routes"]
        ),
        "replica_luts": replica_luts,
    }


def _objective_key(metrics: Mapping[str, Any]) -> Tuple[float, ...]:
    return (
        -float(metrics["worst_normalized_slack"]),
        -float(metrics["total_negative_normalized_slack"]),
        float(metrics["negative_slack_paths"]),
        float(metrics["max_tdm_ratio"]),
        float(metrics["completion_slot"]),
        float(metrics["total_link_bit_hops"]),
        float(metrics["cut_bits"]),
        float(metrics["replica_luts"]),
    )


def compare_candidate_objectives(
    candidate: Mapping[str, Any],
    incumbent: Mapping[str, Any],
    *,
    tolerance: float = 1.0e-12,
) -> Dict[str, Any]:
    candidate_key = _objective_key(candidate["objective_metrics"])
    incumbent_key = _objective_key(incumbent["objective_metrics"])
    for index, (new, old) in enumerate(zip(candidate_key, incumbent_key)):
        if new < old - tolerance:
            return {
                "accepted": True,
                "deciding_level": index,
                "candidate_key": list(candidate_key),
                "incumbent_key": list(incumbent_key),
            }
        if new > old + tolerance:
            return {
                "accepted": False,
                "deciding_level": index,
                "candidate_key": list(candidate_key),
                "incumbent_key": list(incumbent_key),
            }
    return {
        "accepted": False,
        "deciding_level": None,
        "candidate_key": list(candidate_key),
        "incumbent_key": list(incumbent_key),
    }


def build_cross_stage_candidate(
    database: Mapping[str, Any],
    assignment: Mapping[str, Any],
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    ratio_plan: Mapping[str, Any],
    platform: Platform,
    *,
    source_hashes: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    if database.get("schema") != STA_PATH_DATABASE_SCHEMA:
        raise ValidationError("cross-stage candidate STA database is invalid")
    if assignment.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError("cross-stage candidate assignment is invalid")
    design = database.get("design")
    if (
        design != assignment.get("design")
        or design != routes.get("design")
        or design != schedule.get("design")
    ):
        raise ValidationError("cross-stage candidate design mismatch")
    validate_system_routes(assignment, platform, routes)
    validate_tdm_ratio_plan(routes, platform, ratio_plan)
    validate_tdm_schedule(routes, platform, schedule, ratio_plan)
    transport_delay = _scheduled_transport_delay_by_net(
        routes, schedule, platform
    )
    paths = _path_metrics(database, assignment, transport_delay)
    objective_metrics = _objective_metrics(
        paths, assignment, routes, schedule
    )
    hashes = dict(source_hashes or {})
    candidate_id = hashlib.sha256(
        "\n".join(
            f"{key}:{hashes[key]}" for key in sorted(hashes)
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": CROSS_STAGE_CANDIDATE_SCHEMA,
        "status": "pass",
        "design": design,
        "platform": platform.name,
        "candidate_id": candidate_id,
        "source_sha256": hashes,
        "path_metrics": paths,
        "objective": CROSS_STAGE_OBJECTIVE,
        "objective_metrics": objective_metrics,
        "objective_key": list(_objective_key(objective_metrics)),
    }


def evaluate_cross_stage_candidate(
    database_path: Path,
    assignment_path: Path,
    routes_path: Path,
    schedule_path: Path,
    ratio_plan_path: Path,
    platform_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    paths = {
        "database": database_path,
        "assignment": assignment_path,
        "routes": routes_path,
        "schedule": schedule_path,
        "ratio_plan": ratio_plan_path,
        "platform": platform_path,
    }
    candidate = build_cross_stage_candidate(
        read_json(database_path),
        read_json(assignment_path),
        read_json(routes_path),
        read_json(schedule_path),
        read_json(ratio_plan_path),
        Platform.load(platform_path),
        source_hashes={key: _sha256(path) for key, path in paths.items()},
    )
    write_json(output_path, candidate)
    return candidate


def validate_cross_stage_candidate(
    candidate_path: Path,
    database_path: Path,
    assignment_path: Path,
    routes_path: Path,
    schedule_path: Path,
    ratio_plan_path: Path,
    platform_path: Path,
) -> Dict[str, Any]:
    paths = {
        "database": database_path,
        "assignment": assignment_path,
        "routes": routes_path,
        "schedule": schedule_path,
        "ratio_plan": ratio_plan_path,
        "platform": platform_path,
    }
    expected = build_cross_stage_candidate(
        read_json(database_path),
        read_json(assignment_path),
        read_json(routes_path),
        read_json(schedule_path),
        read_json(ratio_plan_path),
        Platform.load(platform_path),
        source_hashes={key: _sha256(path) for key, path in paths.items()},
    )
    actual = read_json(candidate_path)
    if actual != expected:
        raise ValidationError(
            "cross-stage candidate does not match independent reconstruction"
        )
    return {
        "status": "pass",
        "candidate_id": actual["candidate_id"],
        "objective_key": actual["objective_key"],
        "all_paths": actual["path_metrics"]["all_paths"],
    }


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def _run_candidate_flow(
    *,
    root: Path,
    iteration: int,
    assignment_path: Path,
    database_path: Path,
    platform_path: Path,
    route_constraints_path: Optional[Path],
    frame_slots: Optional[int],
    route_max_iterations: Optional[int],
    router: Optional[str],
    simulation_frames: int,
    ratio_optimizer: Optional[str],
    ratio_max_iterations: int,
    max_ratio: Optional[int],
    ratio_quantum: int,
    post_refinement_iterations: int,
    ratio_convergence: float,
) -> Dict[str, Any]:
    iteration_root = root / f"iteration_{iteration:03d}"
    timing_path = iteration_root / "timing_paths.json"
    phase4_root = iteration_root / "phase4"
    phase5_root = iteration_root / "phase5"
    score_path = iteration_root / "candidate.json"
    projection = project_sta_path_database(
        database_path, assignment_path, timing_path
    )
    phase4 = run_phase4(
        assignment_path,
        platform_path,
        phase4_root,
        constraints_path=route_constraints_path,
        frame_slots=frame_slots,
        max_iterations=route_max_iterations,
        provider=ROUTE_TDM_PROVIDER,
        timing_paths_path=timing_path,
        router=router,
    )
    phase5 = run_phase5(
        phase4_root / "routes.json",
        platform_path,
        phase5_root,
        simulation_frames=simulation_frames,
        provider=TDM_RATIO_PROVIDER,
        ratio_optimizer=ratio_optimizer,
        ratio_max_iterations=ratio_max_iterations,
        max_ratio=max_ratio,
        ratio_quantum=ratio_quantum,
        post_refinement_iterations=post_refinement_iterations,
        convergence=ratio_convergence,
    )
    score = evaluate_cross_stage_candidate(
        database_path,
        assignment_path,
        phase4_root / "routes.json",
        phase5_root / "schedule.json",
        phase5_root / "ratio_plan.json",
        platform_path,
        score_path,
    )
    return {
        "iteration": iteration,
        "status": "pass",
        "assignment": _relative(assignment_path, root),
        "timing_paths": _relative(timing_path, root),
        "routes": _relative(phase4_root / "routes.json", root),
        "schedule": _relative(phase5_root / "schedule.json", root),
        "ratio_plan": _relative(phase5_root / "ratio_plan.json", root),
        "score": _relative(score_path, root),
        "projection": projection,
        "phase4_validation": phase4["validation"],
        "phase5_validation": phase5["validation"],
        "objective_metrics": score["objective_metrics"],
        "objective_key": score["objective_key"],
        "candidate_id": score["candidate_id"],
    }


def run_cross_stage_optimization(
    *,
    ir_path: Path,
    platform_path: Path,
    database_path: Path,
    initial_assignment_path: Path,
    output_dir: Path,
    phase3_constraints_path: Optional[Path] = None,
    route_constraints_path: Optional[Path] = None,
    phase3_provider: str = "repart-replication",
    max_outer_iterations: int = 1,
    seed: int = 0,
    min_used_fpgas: Optional[int] = None,
    balance_tolerance: Optional[float] = None,
    openroad: Optional[str] = None,
    repart: Optional[str] = None,
    partition_timeout_seconds: int = 3600,
    router: Optional[str] = None,
    frame_slots: Optional[int] = None,
    route_max_iterations: Optional[int] = None,
    ratio_optimizer: Optional[str] = None,
    feedback_optimizer: Optional[str] = None,
    simulation_frames: int = 4,
    ratio_max_iterations: int = 500,
    max_ratio: Optional[int] = None,
    ratio_quantum: int = 8,
    post_refinement_iterations: int = 200,
    ratio_convergence: float = 1.0e-9,
    pair_pressure_weight: float = 1.0,
) -> Dict[str, Any]:
    if (
        isinstance(max_outer_iterations, bool)
        or not isinstance(max_outer_iterations, int)
        or max_outer_iterations < 0
    ):
        raise ValidationError(
            "cross-stage max outer iterations must be non-negative"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValidationError(
            f"cross-stage output directory is not empty: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    ir = EmuIR.load(ir_path)
    platform = Platform.load(platform_path)
    database = read_json(database_path)
    if database.get("schema") != STA_PATH_DATABASE_SCHEMA:
        raise ValidationError("cross-stage STA path database is invalid")
    if database.get("design") != ir.value["design"]["name"]:
        raise ValidationError(
            "cross-stage STA path database design does not match EmuIR"
        )
    partition_constraints = load_partition_constraints(
        phase3_constraints_path,
        ir,
        platform,
        min_used_fpgas=min_used_fpgas,
        balance_tolerance=balance_tolerance,
    )
    clusters = build_clusters(ir, partition_constraints)
    initial_assignment = read_json(initial_assignment_path)
    initial_validation = validate_partition_artifacts(
        ir, platform, clusters, initial_assignment
    )
    initial_root = output_dir / "iteration_000" / "phase3"
    initial_root.mkdir(parents=True, exist_ok=True)
    assignment_path = initial_root / "assignment.json"
    write_json(assignment_path, initial_assignment)
    write_json(initial_root / "clusters.json", clusters)
    write_json(
        initial_root / "constraints.normalized.json",
        partition_constraints,
    )

    candidates = []
    baseline = _run_candidate_flow(
        root=output_dir,
        iteration=0,
        assignment_path=assignment_path,
        database_path=database_path,
        platform_path=platform_path,
        route_constraints_path=route_constraints_path,
        frame_slots=frame_slots,
        route_max_iterations=route_max_iterations,
        router=router,
        simulation_frames=simulation_frames,
        ratio_optimizer=ratio_optimizer,
        ratio_max_iterations=ratio_max_iterations,
        max_ratio=max_ratio,
        ratio_quantum=ratio_quantum,
        post_refinement_iterations=post_refinement_iterations,
        ratio_convergence=ratio_convergence,
    )
    baseline["decision"] = {
        "accepted": True,
        "reason": "initial incumbent",
    }
    baseline["phase3_validation"] = initial_validation
    candidates.append(baseline)
    incumbent_index = 0
    termination = "iteration-limit"

    for iteration in range(1, max_outer_iterations + 1):
        incumbent = candidates[incumbent_index]
        incumbent_root = output_dir / f"iteration_{incumbent_index:03d}"
        iteration_root = output_dir / f"iteration_{iteration:03d}"
        iteration_root.mkdir(parents=True, exist_ok=True)
        feedback_path = iteration_root / "partition_feedback.json"
        try:
            feedback_validation = run_partition_feedback(
                incumbent_root / "phase4" / "routes.json",
                incumbent_root / "phase5" / "ratio_plan.json",
                platform_path,
                feedback_path,
                executable=feedback_optimizer,
                pair_pressure_weight=pair_pressure_weight,
            )
            phase3_root = iteration_root / "phase3"
            phase3_report = run_phase3(
                ir_path,
                platform_path,
                phase3_root,
                constraints_path=phase3_constraints_path,
                seed=seed,
                min_used_fpgas=min_used_fpgas,
                balance_tolerance=balance_tolerance,
                provider=phase3_provider,
                openroad=openroad,
                net_weights_path=feedback_path,
                tritonpart_timeout_seconds=partition_timeout_seconds,
                repart=repart,
                repart_timeout_seconds=partition_timeout_seconds,
            )
            candidate = _run_candidate_flow(
                root=output_dir,
                iteration=iteration,
                assignment_path=phase3_root / "assignment.json",
                database_path=database_path,
                platform_path=platform_path,
                route_constraints_path=route_constraints_path,
                frame_slots=frame_slots,
                route_max_iterations=route_max_iterations,
                router=router,
                simulation_frames=simulation_frames,
                ratio_optimizer=ratio_optimizer,
                ratio_max_iterations=ratio_max_iterations,
                max_ratio=max_ratio,
                ratio_quantum=ratio_quantum,
                post_refinement_iterations=post_refinement_iterations,
                ratio_convergence=ratio_convergence,
            )
            candidate["feedback"] = _relative(
                feedback_path, output_dir
            )
            candidate["feedback_validation"] = feedback_validation
            candidate["phase3_validation"] = phase3_report["validation"]
            decision = compare_candidate_objectives(
                read_json(output_dir / candidate["score"]),
                read_json(output_dir / incumbent["score"]),
            )
            candidate["decision"] = decision
            candidates.append(candidate)
            if decision["accepted"]:
                incumbent_index = iteration
                continue
            termination = (
                "candidate-regressed"
                if decision["deciding_level"] is not None
                else "fixed-point"
            )
            break
        except (EmuFlowError, ValidationError, ValueError) as error:
            candidates.append(
                {
                    "iteration": iteration,
                    "status": "rejected",
                    "decision": {
                        "accepted": False,
                        "reason": str(error),
                    },
                }
            )
            termination = "infeasible-candidate"
            break

    report = {
        "schema": CROSS_STAGE_REPORT_SCHEMA,
        "status": "pass",
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "provider": "tdm-feedback-iterative-partition-route-schedule-v1",
        "objective": CROSS_STAGE_OBJECTIVE,
        "configuration": {
            "phase3_provider": phase3_provider,
            "max_outer_iterations": max_outer_iterations,
            "seed": seed,
            "simulation_frames": simulation_frames,
            "pair_pressure_weight": pair_pressure_weight,
        },
        "source_sha256": {
            "ir": _sha256(ir_path),
            "platform": _sha256(platform_path),
            "database": _sha256(database_path),
            "initial_assignment": _sha256(initial_assignment_path),
            **(
                {
                    "phase3_constraints": _sha256(
                        phase3_constraints_path
                    )
                }
                if phase3_constraints_path is not None
                else {}
            ),
            **(
                {
                    "route_constraints": _sha256(
                        route_constraints_path
                    )
                }
                if route_constraints_path is not None
                else {}
            ),
        },
        "selected_iteration": incumbent_index,
        "selected_candidate_id": candidates[incumbent_index][
            "candidate_id"
        ],
        "termination": termination,
        "candidates": candidates,
    }
    write_json(output_dir / "cross_stage_report.json", report)
    validate_cross_stage_report(
        output_dir / "cross_stage_report.json",
        ir_path,
        database_path,
        platform_path,
    )
    return report


def validate_cross_stage_report(
    report_path: Path,
    ir_path: Path,
    database_path: Path,
    platform_path: Path,
) -> Dict[str, Any]:
    report = read_json(report_path)
    if report.get("schema") != CROSS_STAGE_REPORT_SCHEMA:
        raise ValidationError("cross-stage report schema is invalid")
    root = report_path.parent
    candidates = report.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValidationError("cross-stage report candidates are invalid")
    ir = EmuIR.load(ir_path)
    platform = Platform.load(platform_path)
    source_hashes = report.get("source_sha256")
    if not isinstance(source_hashes, dict):
        raise ValidationError("cross-stage report source hashes are invalid")
    for name, path in (
        ("ir", ir_path),
        ("database", database_path),
        ("platform", platform_path),
    ):
        if source_hashes.get(name) != _sha256(path):
            raise ValidationError(
                f"cross-stage report source hash {name!r} mismatch"
            )
    incumbent = None
    selected = 0
    validated = 0
    for index, candidate in enumerate(candidates):
        if candidate.get("iteration") != index:
            raise ValidationError(
                "cross-stage candidate iterations are not contiguous"
            )
        if candidate.get("status") != "pass":
            if candidate.get("decision", {}).get("accepted"):
                raise ValidationError(
                    "failed cross-stage candidate cannot be accepted"
                )
            break
        validate_cross_stage_candidate(
            root / candidate["score"],
            database_path,
            root / candidate["assignment"],
            root / candidate["routes"],
            root / candidate["schedule"],
            root / candidate["ratio_plan"],
            platform_path,
        )
        assignment_path = root / candidate["assignment"]
        clusters_path = assignment_path.parent / "clusters.json"
        phase3_validation = validate_partition_artifacts(
            ir,
            platform,
            read_json(clusters_path),
            read_json(assignment_path),
        )
        if candidate.get("phase3_validation") != phase3_validation:
            raise ValidationError(
                "cross-stage report Phase 3 validation mismatch"
            )
        score = read_json(root / candidate["score"])
        for key in (
            "candidate_id",
            "objective_metrics",
            "objective_key",
        ):
            if candidate.get(key) != score[key]:
                raise ValidationError(
                    f"cross-stage report candidate {key} mismatch"
                )
        decision = candidate.get("decision")
        if index == 0:
            if not isinstance(decision, dict) or not decision.get(
                "accepted"
            ):
                raise ValidationError(
                    "cross-stage initial candidate must be accepted"
                )
            incumbent = score
        else:
            expected = compare_candidate_objectives(score, incumbent)
            if decision != expected:
                raise ValidationError(
                    "cross-stage candidate decision mismatch"
                )
            if expected["accepted"]:
                incumbent = score
                selected = index
        validated += 1
    if report.get("selected_iteration") != selected:
        raise ValidationError(
            "cross-stage selected iteration does not match decisions"
        )
    if (
        report.get("selected_candidate_id")
        != candidates[selected]["candidate_id"]
    ):
        raise ValidationError(
            "cross-stage selected candidate identity mismatch"
        )
    return {
        "status": "pass",
        "validated_candidates": validated,
        "selected_iteration": selected,
        "selected_candidate_id": candidates[selected]["candidate_id"],
    }
