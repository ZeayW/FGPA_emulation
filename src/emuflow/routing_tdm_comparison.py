"""Source-bound complete-Phase-7 comparison for routing/TDM providers."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Dict, Iterable

from .errors import ValidationError
from .io import read_json, write_json
from .multi_fpga_flow import (
    _phase6_physical_metrics,
    validate_multi_fpga_flow_report,
)
from .phase4 import validate_phase4
from .phase5 import validate_phase5
from .tdm import TDM_ACADEMIC_SCHEDULE_PROVIDER, TDM_BASELINE_PROVIDER
from .timing_routing import GLOBAL_CANDIDATE_PROVIDER, ROUTE_TDM_PROVIDER


SYSTEM_ROUTE_TDM_AB_SCHEMA = "emuflow.system-route-tdm-ab/v1"
SYSTEM_ROUTE_TDM_SCALE_SCHEMA = "emuflow.system-route-tdm-scale-ab/v1"
_FROZEN_ARTIFACTS = (
    "emuir",
    "assignment",
    "timing_path_database",
    "partition_net_weights",
)
_PHYSICAL_METRICS = (
    "total_wirelength",
    "worst_critical_path_ns",
    "worst_wns_ns",
    "total_tns_ns",
    "failing_endpoints",
    "failing_endpoint_constraints",
    "unrouted_nets",
    "drc_violations",
)
_BASELINE_PHASE6_PROVIDER = "deterministic-cut-shadow-split-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_json_sha256(path: Path, root: Path) -> str:
    """Hash deterministic bytes while normalizing only their flow root."""

    root_texts = {root.absolute().as_posix(), root.resolve().as_posix()}
    root_texts.update(
        text[len("/private") :] for text in tuple(root_texts)
        if text.startswith("/private/")
    )

    encoded = path.read_bytes()
    for root_text in sorted(root_texts, key=len, reverse=True):
        encoded = encoded.replace(root_text.encode("utf-8"), b"$FLOW_ROOT")
    return hashlib.sha256(encoded).hexdigest()


def _checked_artifact(root: Path, report: Dict[str, Any], key: str) -> str:
    artifact = report.get("artifacts", {}).get(key)
    if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
        raise ValidationError(f"routing/TDM A/B artifact {key!r} is missing")
    raw_path = artifact.get("path")
    expected = artifact.get("sha256")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or Path(raw_path).is_absolute()
        or ".." in Path(raw_path).parts
        or not isinstance(expected, str)
        or len(expected) != 64
    ):
        raise ValidationError(f"routing/TDM A/B artifact {key!r} seal is invalid")
    root = root.resolve()
    path = root / raw_path
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"routing/TDM A/B artifact {key!r} is not a regular file")
    resolved = path.resolve()
    if resolved.parent != root and root not in resolved.parents:
        raise ValidationError(f"routing/TDM A/B artifact {key!r} escapes its flow")
    actual = _sha256(resolved)
    if actual != expected:
        raise ValidationError(f"routing/TDM A/B artifact {key!r} hash disagrees")
    return actual


def _finite_metrics(value: Dict[str, Any], fields: Iterable[str], label: str) -> None:
    for field in fields:
        item = value.get(field)
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            raise ValidationError(f"routing/TDM A/B {label} metric {field!r} is invalid")


def _route_metrics(stage: Dict[str, Any]) -> Dict[str, Any]:
    validation = stage["validation"]
    return {
        field: validation[field]
        for field in (
            "demands",
            "routed_sinks",
            "tree_edges",
            "total_link_bit_hops",
            "max_link_utilization",
            "overloaded_links",
            "estimated_worst_tdm_slack_ns",
        )
        if field in validation
    }


def _tdm_metrics(stage: Dict[str, Any]) -> Dict[str, Any]:
    validation = stage["validation"]
    return {
        field: validation[field]
        for field in (
            "frame_slots",
            "completion_slot",
            "max_domain_utilization",
            "scheduled_bit_hops",
            "collisions",
        )
        if field in validation
    }


def validate_system_route_tdm_ab_comparison(report: Dict[str, Any]) -> Dict[str, Any]:
    if (
        report.get("schema") != SYSTEM_ROUTE_TDM_AB_SCHEMA
        or report.get("status") != "pass"
        or report.get("qualification") != "complete-phase7-source-bound-ab"
    ):
        raise ValidationError("routing/TDM A/B comparison identity is invalid")
    frozen = report.get("frozen_upstream")
    if not isinstance(frozen, dict) or not {"emuir", "assignment"}.issubset(frozen):
        raise ValidationError("routing/TDM A/B frozen upstream is incomplete")
    for digest in frozen.values():
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValidationError("routing/TDM A/B frozen digest is invalid")
    configuration = report.get("configuration")
    if configuration != {
        "baseline_route_provider": ROUTE_TDM_PROVIDER,
        "upgrade_route_provider": GLOBAL_CANDIDATE_PROVIDER,
        "baseline_tdm_provider": TDM_BASELINE_PROVIDER,
        "upgrade_tdm_provider": TDM_ACADEMIC_SCHEDULE_PROVIDER,
        "shared_phase6_provider": _BASELINE_PHASE6_PROVIDER,
    }:
        raise ValidationError("routing/TDM A/B provider configuration is invalid")
    arms = report.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"baseline", "upgrade"}:
        raise ValidationError("routing/TDM A/B arms are incomplete")
    for label, arm in arms.items():
        if not isinstance(arm, dict):
            raise ValidationError(f"routing/TDM A/B {label} arm is invalid")
        physical = arm.get("physical")
        if not isinstance(physical, dict) or set(physical) != set(_PHYSICAL_METRICS):
            raise ValidationError(f"routing/TDM A/B {label} physical metrics are incomplete")
        _finite_metrics(physical, _PHYSICAL_METRICS, f"{label} physical")
        if physical["unrouted_nets"] != 0 or physical["drc_violations"] != 0:
            raise ValidationError(f"routing/TDM A/B {label} physical arm did not close")
        source = arm.get("flow_report")
        if (
            not isinstance(source, dict)
            or set(source) != {"sha256"}
            or not isinstance(source["sha256"], str)
            or len(source["sha256"]) != 64
        ):
            raise ValidationError(f"routing/TDM A/B {label} source seal is invalid")
    baseline = arms["baseline"]["physical"]
    upgrade = arms["upgrade"]["physical"]
    expected = {
        field: upgrade[field] - baseline[field]
        for field in _PHYSICAL_METRICS
        if field not in {"unrouted_nets", "drc_violations"}
    }
    delta = report.get("physical_delta_upgrade_minus_baseline")
    if delta != expected:
        raise ValidationError("routing/TDM A/B physical delta disagrees")
    return {
        "status": "pass",
        "wns_improvement_ns": expected["worst_wns_ns"],
        "tns_improvement_ns": expected["total_tns_ns"],
        "critical_path_change_ns": expected["worst_critical_path_ns"],
        "wirelength_change": expected["total_wirelength"],
        "failing_endpoint_change": expected["failing_endpoints"],
    }


def build_system_route_tdm_ab_comparison(
    baseline_root: Path,
    upgrade_root: Path,
    output_path: Path,
) -> Dict[str, Any]:
    baseline_root = baseline_root.resolve()
    upgrade_root = upgrade_root.resolve()
    if baseline_root == upgrade_root:
        raise ValidationError("routing/TDM A/B flow roots must be different")
    reports: Dict[str, Dict[str, Any]] = {}
    roots = {"baseline": baseline_root, "upgrade": upgrade_root}
    for label, root in roots.items():
        path = root / "multi-fpga-flow-report.json"
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"routing/TDM A/B {label} flow report is missing")
        report = read_json(path)
        validate_multi_fpga_flow_report(report)
        if report.get("physical") is None:
            raise ValidationError(f"routing/TDM A/B {label} did not complete Phase 7")
        reports[label] = report

    baseline = reports["baseline"]
    upgrade = reports["upgrade"]
    if (
        baseline["stages"]["frontend"].get("design")
        != upgrade["stages"]["frontend"].get("design")
        or baseline["stages"]["frontend"].get("platform")
        != upgrade["stages"]["frontend"].get("platform")
        or baseline["stages"]["partition"].get("provider")
        != upgrade["stages"]["partition"].get("provider")
    ):
        raise ValidationError("routing/TDM A/B common design/platform/partition differs")
    if (
        baseline["stages"]["system_route"].get("provider") != ROUTE_TDM_PROVIDER
        or upgrade["stages"]["system_route"].get("provider")
        != GLOBAL_CANDIDATE_PROVIDER
        or baseline["stages"]["tdm"].get("provider") != TDM_BASELINE_PROVIDER
        or upgrade["stages"]["tdm"].get("provider")
        != TDM_ACADEMIC_SCHEDULE_PROVIDER
    ):
        raise ValidationError("routing/TDM A/B arm providers are not the frozen pair")
    shared_phase6 = baseline["stages"]["split"].get("provider")
    if (
        shared_phase6 != _BASELINE_PHASE6_PROVIDER
        or upgrade["stages"]["split"].get("provider") != shared_phase6
    ):
        raise ValidationError("routing/TDM A/B Phase 6 provider is not shared baseline")

    frozen: Dict[str, str] = {}
    for key in _FROZEN_ARTIFACTS:
        baseline_has = key in baseline.get("artifacts", {})
        upgrade_has = key in upgrade.get("artifacts", {})
        if baseline_has != upgrade_has:
            raise ValidationError(f"routing/TDM A/B artifact {key!r} coverage differs")
        if baseline_has:
            _checked_artifact(baseline_root, baseline, key)
            _checked_artifact(upgrade_root, upgrade, key)
            baseline_path = baseline_root / baseline["artifacts"][key]["path"]
            upgrade_path = upgrade_root / upgrade["artifacts"][key]["path"]
            baseline_digest = _normalized_json_sha256(
                baseline_path, baseline_root
            )
            upgrade_digest = _normalized_json_sha256(upgrade_path, upgrade_root)
            if baseline_digest != upgrade_digest:
                raise ValidationError(f"routing/TDM A/B frozen artifact {key!r} differs")
            frozen[key] = baseline_digest

    arms: Dict[str, Any] = {}
    for label, report in reports.items():
        root = roots[label]
        source_path = root / "multi-fpga-flow-report.json"
        physical = _phase6_physical_metrics(report["physical"])
        arms[label] = {
            "flow_report": {"sha256": _sha256(source_path)},
            "route_provider": report["stages"]["system_route"]["provider"],
            "tdm_provider": report["stages"]["tdm"]["provider"],
            "route": _route_metrics(report["stages"]["system_route"]),
            "tdm": _tdm_metrics(report["stages"]["tdm"]),
            "physical": physical,
        }
    baseline_physical = arms["baseline"]["physical"]
    upgrade_physical = arms["upgrade"]["physical"]
    result = {
        "schema": SYSTEM_ROUTE_TDM_AB_SCHEMA,
        "status": "pass",
        "qualification": "complete-phase7-source-bound-ab",
        "design": baseline["stages"]["frontend"]["design"],
        "platform": baseline["stages"]["frontend"]["platform"],
        "frozen_upstream": frozen,
        "configuration": {
            "baseline_route_provider": ROUTE_TDM_PROVIDER,
            "upgrade_route_provider": GLOBAL_CANDIDATE_PROVIDER,
            "baseline_tdm_provider": TDM_BASELINE_PROVIDER,
            "upgrade_tdm_provider": TDM_ACADEMIC_SCHEDULE_PROVIDER,
            "shared_phase6_provider": shared_phase6,
        },
        "arms": arms,
        "physical_delta_upgrade_minus_baseline": {
            field: upgrade_physical[field] - baseline_physical[field]
            for field in _PHYSICAL_METRICS
            if field not in {"unrouted_nets", "drc_violations"}
        },
    }
    result["validation"] = validate_system_route_tdm_ab_comparison(result)
    write_json(output_path, result)
    return result


def validate_system_route_tdm_scale_comparison(
    report: Dict[str, Any],
) -> Dict[str, Any]:
    if (
        report.get("schema") != SYSTEM_ROUTE_TDM_SCALE_SCHEMA
        or report.get("status") != "pass"
        or report.get("qualification") != "independent-phase4-phase5-scale-ab"
    ):
        raise ValidationError("routing/TDM scale comparison identity is invalid")
    sources = report.get("frozen_upstream")
    if not isinstance(sources, dict) or set(sources) != {
        "assignment", "platform", "timing_paths"
    }:
        raise ValidationError("routing/TDM scale frozen upstream is incomplete")
    for digest in sources.values():
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValidationError("routing/TDM scale source digest is invalid")
    arms = report.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"baseline", "upgrade"}:
        raise ValidationError("routing/TDM scale arms are incomplete")
    expected_providers = {
        "baseline": (ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER),
        "upgrade": (GLOBAL_CANDIDATE_PROVIDER, TDM_ACADEMIC_SCHEDULE_PROVIDER),
    }
    for label, (route_provider, tdm_provider) in expected_providers.items():
        arm = arms.get(label)
        if (
            not isinstance(arm, dict)
            or arm.get("route_provider") != route_provider
            or arm.get("tdm_provider") != tdm_provider
            or arm.get("route_validation", {}).get("status") != "pass"
            or arm.get("tdm_validation", {}).get("status") != "pass"
        ):
            raise ValidationError(f"routing/TDM scale {label} arm is invalid")
        seals = arm.get("artifacts")
        if not isinstance(seals, dict) or set(seals) != {"routes", "schedule"}:
            raise ValidationError(f"routing/TDM scale {label} seals are invalid")
        if any(not isinstance(value, str) or len(value) != 64 for value in seals.values()):
            raise ValidationError(f"routing/TDM scale {label} digest is invalid")
    return {
        "status": "pass",
        "baseline_routed_sinks": arms["baseline"]["route_validation"].get(
            "routed_sinks"
        ),
        "upgrade_routed_sinks": arms["upgrade"]["route_validation"].get(
            "routed_sinks"
        ),
        "baseline_scheduled_bit_hops": arms["baseline"]["tdm_validation"].get(
            "scheduled_bit_hops"
        ),
        "upgrade_scheduled_bit_hops": arms["upgrade"]["tdm_validation"].get(
            "scheduled_bit_hops"
        ),
    }


def build_system_route_tdm_scale_comparison(
    assignment_path: Path,
    platform_path: Path,
    timing_paths_path: Path,
    baseline_route_root: Path,
    baseline_tdm_root: Path,
    upgrade_route_root: Path,
    upgrade_tdm_root: Path,
    output_path: Path,
) -> Dict[str, Any]:
    """Independently replay and seal Phase 4/5 on a frozen large instance."""

    source_paths = {
        "assignment": assignment_path.resolve(),
        "platform": platform_path.resolve(),
        "timing_paths": timing_paths_path.resolve(),
    }
    for label, path in source_paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"routing/TDM scale {label} input is missing")
    roots = {
        "baseline": (baseline_route_root.resolve(), baseline_tdm_root.resolve()),
        "upgrade": (upgrade_route_root.resolve(), upgrade_tdm_root.resolve()),
    }
    arms: Dict[str, Any] = {}
    expected = {
        "baseline": (ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER),
        "upgrade": (GLOBAL_CANDIDATE_PROVIDER, TDM_ACADEMIC_SCHEDULE_PROVIDER),
    }
    for label, (route_root, tdm_root) in roots.items():
        routes_path = route_root / "routes.json"
        schedule_path = tdm_root / "schedule.json"
        ratio_path = tdm_root / "ratio_plan.json"
        for artifact_label, path in (("routes", routes_path), ("schedule", schedule_path)):
            if path.is_symlink() or not path.is_file():
                raise ValidationError(
                    f"routing/TDM scale {label} {artifact_label} is missing"
                )
        routes = read_json(routes_path)
        schedule = read_json(schedule_path)
        route_provider, tdm_provider = expected[label]
        if routes.get("provider") != route_provider or schedule.get("provider") != tdm_provider:
            raise ValidationError(f"routing/TDM scale {label} provider differs")
        route_validation = validate_phase4(
            source_paths["assignment"],
            source_paths["platform"],
            routes_path,
            source_paths["timing_paths"],
        )
        tdm_validation = validate_phase5(
            routes_path,
            source_paths["platform"],
            schedule_path,
            ratio_path if ratio_path.is_file() and not ratio_path.is_symlink() else None,
        )
        arms[label] = {
            "route_provider": route_provider,
            "tdm_provider": tdm_provider,
            "route_validation": route_validation,
            "tdm_validation": tdm_validation,
            "artifacts": {
                "routes": _sha256(routes_path),
                "schedule": _sha256(schedule_path),
            },
        }
    report = {
        "schema": SYSTEM_ROUTE_TDM_SCALE_SCHEMA,
        "status": "pass",
        "qualification": "independent-phase4-phase5-scale-ab",
        "frozen_upstream": {
            label: _sha256(path) for label, path in source_paths.items()
        },
        "arms": arms,
    }
    report["validation"] = validate_system_route_tdm_scale_comparison(report)
    write_json(output_path, report)
    return report
