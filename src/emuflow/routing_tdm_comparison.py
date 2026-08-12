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
from .tdm import TDM_BASELINE_PROVIDER
from .tdm_ratio import TDM_RATIO_PROVIDER
from .timing_routing import GLOBAL_CANDIDATE_PROVIDER, ROUTE_TDM_PROVIDER


SYSTEM_ROUTE_TDM_AB_SCHEMA = "emuflow.system-route-tdm-ab/v1"
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
        "upgrade_tdm_provider": TDM_RATIO_PROVIDER,
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
        or upgrade["stages"]["tdm"].get("provider") != TDM_RATIO_PROVIDER
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
            baseline_digest = _checked_artifact(baseline_root, baseline, key)
            upgrade_digest = _checked_artifact(upgrade_root, upgrade, key)
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
            "upgrade_tdm_provider": TDM_RATIO_PROVIDER,
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
