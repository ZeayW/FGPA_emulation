"""Inventory and self-contained evidence bundles for experiment checkpoints."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import EmuFlowError, ValidationError
from .experiment_dag import (
    EXPERIMENT_PLAN_V2_SCHEMA,
    _artifact_digest,
    _cached_checkpoint,
    _load_plan,
    _portable_argv,
    _safe_artifact,
    _validated_certificate,
    _v2_execution_key,
    _v2_validation_key,
)
from .experiment_storage import validate_experiment_write_path
from .io import read_json, write_json


EXPERIMENT_INVENTORY_SCHEMA = "emuflow.experiment-store-inventory/v1"
EXPERIMENT_EVIDENCE_SCHEMA = "emuflow.experiment-evidence-bundle/v1"
EXPERIMENT_EVIDENCE_SEAL_SCHEMA = "emuflow.experiment-evidence-seal/v1"
EXPERIMENT_GC_PLAN_SCHEMA = "emuflow.experiment-gc-plan/v1"
EXPERIMENT_GC_RECEIPT_SCHEMA = "emuflow.experiment-gc-receipt/v1"
EXPERIMENT_MIGRATION_PLAN_SCHEMA = "emuflow.experiment-migration-plan/v1"
EXPERIMENT_RETIREMENT_PLAN_SCHEMA = "emuflow.experiment-retirement-plan/v1"
EXPERIMENT_RETIREMENT_RECEIPT_SCHEMA = "emuflow.experiment-retirement-receipt/v1"
_MIGRATION_MARKERS = {
    "multi-fpga-flow-report.json",
    "experiment-phase6-report.json",
    "experiment-phase7-report.json",
    "experiment-lookahead-report.json",
    "checkpoint.json",
    "farm-manifest.json",
    "archive-manifest.json",
    "cleanup-seal.json",
    "evidence-manifest.json",
    "evidence-seal.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def inventory_experiment_store(cache_root: Path) -> dict[str, Any]:
    cache_root = validate_experiment_write_path(cache_root)
    objects = []
    by_role: dict[str, int] = {}
    object_root = cache_root / "objects"
    for candidate in sorted(object_root.iterdir()) if object_root.is_dir() else []:
        manifest_path = candidate / "checkpoint.json"
        record: dict[str, Any] = {
            "execution_key": candidate.name,
            "path": str(candidate),
            "bytes": _tree_bytes(candidate),
        }
        try:
            checkpoint = _cached_checkpoint(cache_root, candidate.name)
            if checkpoint is None:
                raise ValidationError("checkpoint manifest is missing")
            record.update(
                {
                    "status": "valid",
                    "storage": checkpoint["storage"],
                    "stage": checkpoint["stage"],
                }
            )
            expected = checkpoint.get("expected_artifacts", [])
            if expected and isinstance(expected[0], dict):
                for artifact in expected:
                    size = checkpoint["artifacts"][artifact["path"]]["bytes"]
                    by_role[artifact["role"]] = by_role.get(artifact["role"], 0) + size
        except (OSError, ValidationError, KeyError) as error:
            record.update(
                {
                    "status": "invalid",
                    "error": f"{type(error).__name__}: {error}",
                    "manifest_present": manifest_path.is_file(),
                }
            )
        objects.append(record)
    areas = {
        label: _tree_bytes(cache_root / label)
        for label in ("objects", "staging", "failures", "scratch", "attempts", "evidence")
    }
    return {
        "schema": EXPERIMENT_INVENTORY_SCHEMA,
        "status": "pass",
        "cache_root": str(cache_root),
        "objects": objects,
        "counts": {
            "valid": sum(item["status"] == "valid" for item in objects),
            "invalid": sum(item["status"] == "invalid" for item in objects),
        },
        "bytes_by_area": areas,
        "bytes_by_artifact_role": dict(sorted(by_role.items())),
        "total_bytes": sum(areas.values()),
    }


def _hardlink_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_mode & 0o222:
        shutil.copy2(source, destination)
        destination.chmod(0o444)
        return
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        shutil.copy2(source, destination)
        destination.chmod(0o444)


def _materialize_artifact(source: Path, destination: Path) -> None:
    if source.is_file():
        _hardlink_or_copy(source, destination)
        return
    destination.mkdir(parents=True)
    for child in sorted(source.rglob("*")):
        relative = child.relative_to(source)
        target = destination / relative
        if child.is_symlink():
            raise ValidationError("evidence source contains a symbolic link")
        if child.is_dir():
            target.mkdir()
        elif child.is_file():
            _hardlink_or_copy(child, target)
        else:
            raise ValidationError("evidence source contains a special file")


def _make_bundle_immutable(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        elif path.is_file() and path.stat().st_mode & 0o222:
            path.chmod(0o444)
    root.chmod(0o555)


def _ancestor_ids(nodes: Mapping[str, Mapping[str, Any]], terminals: Iterable[str]) -> list[str]:
    selected: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in selected:
            return
        if node_id not in nodes:
            raise ValidationError(f"experiment plan has no node {node_id!r}")
        for dependency in nodes[node_id]["dependencies"]:
            visit(dependency)
        selected.add(node_id)

    for terminal in terminals:
        visit(terminal)
    return [node_id for node_id in nodes if node_id in selected]


def create_experiment_evidence_bundle(
    plan_path: Path, terminal_nodes: list[str], output_dir: Path
) -> dict[str, Any]:
    if not terminal_nodes:
        raise ValidationError("experiment evidence requires terminal nodes")
    plan = _load_plan(plan_path)
    if plan["schema"] != EXPERIMENT_PLAN_V2_SCHEMA:
        raise ValidationError("self-contained evidence requires a v2 experiment plan")
    cache_root = Path(plan["cache_root"])
    nodes = {item["id"]: item for item in plan["nodes"]}
    selected = _ancestor_ids(nodes, terminal_nodes)
    output_dir = validate_experiment_write_path(output_dir)
    if output_dir.exists():
        raise EmuFlowError(f"experiment evidence output already exists: {output_dir}")
    staging = output_dir.with_name(f".{output_dir.name}.creating-{os.getpid()}")
    if staging.exists():
        raise EmuFlowError(f"experiment evidence staging already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        records = []
        for node_id in selected:
            node = nodes[node_id]
            checkpoint = _cached_checkpoint(cache_root, node["execution_key"])
            certificate = _validated_certificate(
                cache_root, node["execution_key"], node["validation_key"]
            )
            if checkpoint is None or certificate is None:
                raise ValidationError(
                    f"experiment evidence node {node_id!r} is not reusable"
                )
            artifact_records = []
            expected_by_path = {
                item["path"]: item for item in checkpoint["expected_artifacts"]
            }
            for relative, declaration in sorted(expected_by_path.items()):
                retain = declaration["retention"] == "required"
                source = _safe_artifact(Path(checkpoint["output_dir"]), relative)
                bundle_relative = (
                    Path("checkpoints") / node["execution_key"] / relative
                )
                if retain:
                    _materialize_artifact(source, staging / bundle_relative)
                content = checkpoint["artifacts"][relative]
                artifact_records.append(
                    {
                        **declaration,
                        "bundle_path": bundle_relative.as_posix() if retain else None,
                        "content": content,
                    }
                )
            records.append(
                {
                    "node_id": node_id,
                    "stage": node["stage"],
                    "execution_key": node["execution_key"],
                    "validation_key": node["validation_key"],
                    "dependency_keys": node["dependency_keys"],
                    "contract": {
                        key: node[key]
                        for key in (
                            "stage",
                            "inputs",
                            "configuration",
                            "implementation",
                            "implementation_sha256",
                            "command",
                            "validator_implementation",
                            "validator_sha256",
                            "validator",
                            "environment",
                            "storage_estimate",
                            "artifacts",
                        )
                    }
                    | {
                        key: node[key]
                        for key in (
                            "execution_bindings",
                            "command_identity",
                            "validator_identity",
                        )
                        if key in node
                    }
                    | {
                        key: node[key]
                        for key in ("provider", "physical_seed")
                        if key in node
                    },
                    "validation": certificate,
                    "artifacts": artifact_records,
                }
            )
        manifest = {
            "schema": EXPERIMENT_EVIDENCE_SCHEMA,
            "status": "pass",
            "experiment_id": plan["experiment_id"],
            "source_commit": plan["source_commit"],
            "terminal_nodes": terminal_nodes,
            "nodes": records,
        }
        write_json(staging / "evidence-manifest.json", manifest)
        write_json(
            staging / "evidence-seal.json",
            {
                "schema": EXPERIMENT_EVIDENCE_SEAL_SCHEMA,
                "status": "sealed",
                "manifest_sha256": _sha256(staging / "evidence-manifest.json"),
            },
        )
        _make_bundle_immutable(staging)
        staging.rename(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return validate_experiment_evidence_bundle(output_dir)


def validate_experiment_evidence_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    manifest = read_json(manifest_path)
    seal = read_json(root / "evidence-seal.json")
    if (
        manifest.get("schema") != EXPERIMENT_EVIDENCE_SCHEMA
        or manifest.get("status") != "pass"
    ):
        raise ValidationError("experiment evidence manifest is invalid")
    if (
        seal.get("schema") != EXPERIMENT_EVIDENCE_SEAL_SCHEMA
        or seal.get("status") != "sealed"
        or seal.get("manifest_sha256") != _sha256(manifest_path)
    ):
        raise ValidationError("experiment evidence seal is broken")
    seen = set()
    retained = 0
    for node in manifest.get("nodes", []):
        if not isinstance(node, dict) or node.get("execution_key") in seen:
            raise ValidationError("experiment evidence node table is invalid")
        seen.add(node["execution_key"])
        contract = node.get("contract")
        if not isinstance(contract, dict):
            raise ValidationError("experiment evidence node contract is invalid")
        execution_bindings = contract.get("execution_bindings", {})
        command_identity = contract.get("command_identity")
        validator_identity = contract.get("validator_identity")
        identity_declared = (
            bool(execution_bindings)
            or command_identity is not None
            or validator_identity is not None
        )
        if identity_declared:
            inputs = contract.get("inputs", {})
            command = contract.get("command")
            validator = contract.get("validator")
            if (
                not isinstance(inputs, dict)
                or not isinstance(command, list)
                or not all(isinstance(value, str) for value in command)
                or not isinstance(validator, list)
                or not all(isinstance(value, str) for value in validator)
                or not isinstance(execution_bindings, dict)
                or not execution_bindings
                or not all(
                    isinstance(label, str)
                    and label in inputs
                    and isinstance(value, str)
                    and value
                    for label, value in execution_bindings.items()
                )
                or not isinstance(command_identity, list)
                or not all(isinstance(value, str) for value in command_identity)
                or not isinstance(validator_identity, list)
                or not all(isinstance(value, str) for value in validator_identity)
                or command_identity
                != _portable_argv(command, execution_bindings)
                or validator_identity
                != _portable_argv(validator, execution_bindings)
            ):
                raise ValidationError(
                    "experiment evidence portable execution identity is broken"
                )
        if _v2_execution_key(contract, node.get("dependency_keys", {})) != node[
            "execution_key"
        ]:
            raise ValidationError("experiment evidence execution identity is broken")
        if _v2_validation_key(contract, node["execution_key"]) != node.get(
            "validation_key"
        ):
            raise ValidationError("experiment evidence validation identity is broken")
        if node.get("validation") != {
            "schema": "emuflow.experiment-validation/v1",
            "execution_key": node["execution_key"],
            "validation_key": node["validation_key"],
            "status": "pass",
        }:
            raise ValidationError("experiment evidence validation certificate is invalid")
        for artifact in node.get("artifacts", []):
            if not isinstance(artifact, dict):
                raise ValidationError("experiment evidence artifact is invalid")
            content = artifact.get("content")
            if not isinstance(content, dict):
                raise ValidationError("experiment evidence content seal is invalid")
            bundle_path = artifact.get("bundle_path")
            if artifact.get("retention") == "required":
                if not isinstance(bundle_path, str):
                    raise ValidationError("required evidence artifact is missing")
                path = _safe_artifact(root, bundle_path)
                kind, digest, size = _artifact_digest(path)
                if content != {"kind": kind, "sha256": digest, "bytes": size}:
                    raise ValidationError(
                        f"experiment evidence artifact seal is broken: {bundle_path}"
                    )
                retained += 1
            elif bundle_path is not None:
                raise ValidationError("non-required evidence artifact was copied")
    return {
        "schema": EXPERIMENT_EVIDENCE_SCHEMA,
        "status": "pass",
        "experiment_id": manifest["experiment_id"],
        "terminal_nodes": manifest["terminal_nodes"],
        "nodes": len(seen),
        "retained_artifacts": retained,
    }


def _root_execution_keys(plan_paths: Iterable[Path]) -> tuple[set[str], list[dict[str, Any]]]:
    keys: set[str] = set()
    sources = []
    for path in plan_paths:
        plan_path = path.resolve()
        plan = _load_plan(plan_path)
        if plan["schema"] != EXPERIMENT_PLAN_V2_SCHEMA:
            raise ValidationError("experiment GC roots require v2 plans")
        plan_keys = sorted(node["execution_key"] for node in plan["nodes"])
        keys.update(plan_keys)
        sources.append(
            {
                "path": str(plan_path),
                "sha256": _sha256(plan_path),
                "execution_keys": plan_keys,
            }
        )
    return keys, sources


def plan_experiment_gc(
    cache_root: Path,
    root_plans: list[Path],
    output_path: Path,
    *,
    minimum_age_seconds: int = 7 * 24 * 3600,
) -> dict[str, Any]:
    if (
        isinstance(minimum_age_seconds, bool)
        or not isinstance(minimum_age_seconds, int)
        or minimum_age_seconds < 0
    ):
        raise ValidationError("experiment GC minimum age is invalid")
    cache_root = validate_experiment_write_path(cache_root)
    output_path = validate_experiment_write_path(output_path)
    roots, root_sources = _root_execution_keys(root_plans)
    now = datetime.now(timezone.utc).timestamp()
    candidates = []
    for area in ("objects", "staging", "failures", "scratch"):
        area_root = cache_root / area
        if not area_root.is_dir():
            continue
        for path in sorted(area_root.iterdir()):
            if path.is_symlink():
                raise ValidationError(f"experiment GC refuses symlink: {path}")
            if area == "objects" and path.name in roots:
                continue
            age = max(0.0, now - path.stat().st_mtime)
            if age < minimum_age_seconds:
                continue
            kind, digest, size = _artifact_digest(path)
            candidates.append(
                {
                    "path": path.relative_to(cache_root).as_posix(),
                    "area": area,
                    "kind": kind,
                    "sha256": digest,
                    "bytes": size,
                    "age_seconds": age,
                    "reason": (
                        "unreferenced-checkpoint"
                        if area == "objects"
                        else f"expired-{area}"
                    ),
                }
            )
    plan = {
        "schema": EXPERIMENT_GC_PLAN_SCHEMA,
        "status": "planned",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cache_root": str(cache_root),
        "minimum_age_seconds": minimum_age_seconds,
        "roots": root_sources,
        "candidates": candidates,
        "candidate_bytes": sum(item["bytes"] for item in candidates),
    }
    write_json(output_path, plan)
    return plan


def _make_writable(root: Path) -> None:
    if root.is_dir():
        root.chmod(0o755)
        for path in root.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
    elif root.exists():
        root.chmod(0o644)


def apply_experiment_gc(plan_path: Path, expected_sha256: str) -> dict[str, Any]:
    plan_path = plan_path.resolve()
    if len(expected_sha256) != 64 or _sha256(plan_path) != expected_sha256:
        raise ValidationError("experiment GC plan approval seal is broken")
    plan = read_json(plan_path)
    if plan.get("schema") != EXPERIMENT_GC_PLAN_SCHEMA or plan.get("status") != "planned":
        raise ValidationError("experiment GC plan is invalid")
    cache_root = validate_experiment_write_path(Path(plan["cache_root"]))
    roots, _ = _root_execution_keys([Path(item["path"]) for item in plan["roots"]])
    removed = []
    for candidate in plan.get("candidates", []):
        relative = Path(candidate["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValidationError("experiment GC candidate path is unsafe")
        path = (cache_root / relative).resolve()
        if path != cache_root and cache_root not in path.parents:
            raise ValidationError("experiment GC candidate escapes cache root")
        if candidate["area"] == "objects" and path.name in roots:
            raise ValidationError("experiment GC candidate became referenced")
        kind, digest, size = _artifact_digest(path)
        if (kind, digest, size) != (
            candidate["kind"],
            candidate["sha256"],
            candidate["bytes"],
        ):
            raise ValidationError(
                f"experiment GC candidate changed after approval: {candidate['path']}"
            )
        _make_writable(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(candidate)
    receipt = {
        "schema": EXPERIMENT_GC_RECEIPT_SCHEMA,
        "status": "pass",
        "plan_path": str(plan_path),
        "plan_sha256": expected_sha256,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "removed": removed,
        "removed_bytes": sum(item["bytes"] for item in removed),
    }
    receipt_path = cache_root / "gc-receipts" / f"{expected_sha256}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(receipt_path, receipt)
    return receipt


def plan_legacy_run_migration(root: Path, output_path: Path) -> dict[str, Any]:
    """Inventory top-level legacy run trees without mutating or trusting them."""

    root = root.expanduser().resolve()
    output_path = validate_experiment_write_path(output_path)
    if not root.is_dir() or root.is_symlink():
        raise ValidationError("legacy migration root must be a directory")
    records = []
    global_inodes: set[tuple[int, int]] = set()
    unique_allocated = 0
    for entry in sorted(root.iterdir()):
        if entry.is_symlink():
            records.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "classification": "unsafe-symlink",
                    "recommended_action": "manual-inspection",
                }
            )
            continue
        if not entry.is_dir():
            continue
        files = 0
        logical = 0
        allocated = 0
        markers = []
        newest = entry.stat().st_mtime
        local_inodes: set[tuple[int, int]] = set()
        for current, directory_names, file_names in os.walk(entry, followlinks=False):
            current_path = Path(current)
            directory_names[:] = [
                name
                for name in directory_names
                if not (current_path / name).is_symlink()
            ]
            for name in file_names:
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                inode = (stat.st_dev, stat.st_ino)
                files += 1
                logical += stat.st_size
                newest = max(newest, stat.st_mtime)
                if inode not in local_inodes:
                    allocated += stat.st_blocks * 512
                    local_inodes.add(inode)
                if inode not in global_inodes:
                    unique_allocated += stat.st_blocks * 512
                    global_inodes.add(inode)
                if name in _MIGRATION_MARKERS:
                    markers.append(
                        {
                            "path": path.relative_to(entry).as_posix(),
                            "sha256": _sha256(path),
                            "bytes": stat.st_size,
                        }
                    )
        marker_names = {Path(item["path"]).name for item in markers}
        if {"evidence-manifest.json", "evidence-seal.json"} <= marker_names:
            classification = "evidence-bundle-candidate"
            action = "independently-validate-and-retain"
        elif {"archive-manifest.json", "cleanup-seal.json"} <= marker_names:
            classification = "validation-archive-candidate"
            action = "independently-validate-and-retain"
        elif "farm-manifest.json" in marker_names:
            classification = "validation-farm"
            action = "reconcile-attempts-and-import-valid-checkpoints"
        elif "multi-fpga-flow-report.json" in marker_names:
            classification = "full-flow-candidate"
            action = "independently-validate-and-import"
        elif marker_names & {
            "checkpoint.json",
            "experiment-phase6-report.json",
            "experiment-phase7-report.json",
            "experiment-lookahead-report.json",
        }:
            classification = "checkpoint-candidate"
            action = "independently-validate-and-import"
        else:
            classification = "partial-or-diagnostic"
            action = "manual-inspection-before-retention-decision"
        records.append(
            {
                "name": entry.name,
                "path": str(entry),
                "classification": classification,
                "recommended_action": action,
                "files": files,
                "logical_bytes": logical,
                "allocated_bytes": allocated,
                "newest_mtime": newest,
                "markers": sorted(markers, key=lambda item: item["path"]),
            }
        )
    plan = {
        "schema": EXPERIMENT_MIGRATION_PLAN_SCHEMA,
        "status": "planned",
        "root": str(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": records,
        "totals": {
            "entries": len(records),
            "logical_bytes": sum(item.get("logical_bytes", 0) for item in records),
            "allocated_bytes_before_hardlink_dedup": sum(
                item.get("allocated_bytes", 0) for item in records
            ),
            "unique_allocated_bytes": unique_allocated,
        },
        "safety": {
            "mutated": False,
            "deletion_authorized": False,
            "marker_status_is_not_trusted_without_independent_validation": True,
        },
    }
    write_json(output_path, plan)
    return plan


def plan_legacy_run_retirement(
    migration_plan_path: Path,
    names: list[str],
    output_path: Path,
    *,
    reason: str,
) -> dict[str, Any]:
    """Content-seal explicitly selected noncanonical legacy run trees.

    This is intentionally separate from migration inventory: a marker or a
    directory name never authorizes deletion.  The resulting plan still needs
    its exact SHA-256 at apply time.
    """

    migration_plan_path = migration_plan_path.expanduser().resolve()
    migration = read_json(migration_plan_path)
    if (
        migration.get("schema") != EXPERIMENT_MIGRATION_PLAN_SCHEMA
        or migration.get("status") != "planned"
    ):
        raise ValidationError("legacy migration plan is invalid")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("legacy retirement reason is required")
    if not names or len(names) != len(set(names)):
        raise ValidationError("legacy retirement names must be nonempty and unique")
    for name in names:
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValidationError("legacy retirement name is unsafe")
    root = Path(migration["root"]).resolve()
    entries = {entry["name"]: entry for entry in migration.get("entries", [])}
    candidates = []
    for name in sorted(names):
        if name not in entries:
            raise ValidationError(f"legacy retirement name is not inventoried: {name}")
        entry = entries[name]
        if entry.get("classification") in {
            "evidence-bundle-candidate",
            "validation-archive-candidate",
            "unsafe-symlink",
        }:
            raise ValidationError(
                f"legacy retirement refuses protected candidate: {name}"
            )
        lexical_path = root / name
        if lexical_path.is_symlink():
            raise ValidationError(f"legacy retirement candidate is unsafe: {name}")
        path = lexical_path.resolve()
        if path.parent != root or not path.is_dir():
            raise ValidationError(f"legacy retirement candidate is unsafe: {name}")
        kind, digest, size = _artifact_digest(path)
        if kind != "directory":
            raise ValidationError("legacy retirement candidate must be a directory")
        candidates.append(
            {
                "name": name,
                "classification": entry.get("classification"),
                "kind": kind,
                "sha256": digest,
                "bytes": size,
                "markers": entry.get("markers", []),
            }
        )
    output_path = validate_experiment_write_path(output_path)
    plan = {
        "schema": EXPERIMENT_RETIREMENT_PLAN_SCHEMA,
        "status": "planned",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason.strip(),
        "root": str(root),
        "migration_plan": {
            "path": str(migration_plan_path),
            "sha256": _sha256(migration_plan_path),
        },
        "candidates": candidates,
        "candidate_bytes": sum(item["bytes"] for item in candidates),
        "safety": {
            "exact_plan_sha256_required": True,
            "all_candidates_prevalidated_before_mutation": True,
            "marker_tombstones_retained": True,
            "canonical_evidence_or_archive_candidates_refused": True,
        },
    }
    write_json(output_path, plan)
    return plan


def apply_legacy_run_retirement(
    plan_path: Path,
    expected_sha256: str,
    receipt_root: Path,
) -> dict[str, Any]:
    """Apply an unchanged retirement plan and retain a non-evidence receipt."""

    plan_path = plan_path.expanduser().resolve()
    if len(expected_sha256) != 64 or _sha256(plan_path) != expected_sha256:
        raise ValidationError("legacy retirement plan approval seal is broken")
    plan = read_json(plan_path)
    if (
        plan.get("schema") != EXPERIMENT_RETIREMENT_PLAN_SCHEMA
        or plan.get("status") != "planned"
    ):
        raise ValidationError("legacy retirement plan is invalid")
    migration_source = plan.get("migration_plan", {})
    migration_path = Path(migration_source.get("path", "")).resolve()
    if (
        not migration_path.is_file()
        or _sha256(migration_path) != migration_source.get("sha256")
    ):
        raise ValidationError("legacy migration inventory changed after retirement planning")
    root = Path(plan["root"]).resolve()
    receipt_root = validate_experiment_write_path(receipt_root)
    if receipt_root == root or root in receipt_root.parents or receipt_root in root.parents:
        raise ValidationError("legacy retirement receipt must be outside the run tree")
    if receipt_root.exists():
        raise ValidationError("legacy retirement receipt already exists")

    # Validate the complete set before deleting the first byte.
    validated: list[tuple[dict[str, Any], Path]] = []
    for candidate in plan.get("candidates", []):
        name = candidate.get("name")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValidationError("legacy retirement candidate path is unsafe")
        lexical_path = root / name
        if lexical_path.is_symlink():
            raise ValidationError(f"legacy retirement candidate changed: {name}")
        path = lexical_path.resolve()
        if path.parent != root or not path.is_dir():
            raise ValidationError(f"legacy retirement candidate changed: {name}")
        kind, digest, size = _artifact_digest(path)
        if (kind, digest, size) != (
            candidate.get("kind"),
            candidate.get("sha256"),
            candidate.get("bytes"),
        ):
            raise ValidationError(f"legacy retirement candidate changed: {name}")
        for marker in candidate.get("markers", []):
            marker_path = _safe_artifact(path, marker["path"])
            if not marker_path.is_file() or _sha256(marker_path) != marker["sha256"]:
                raise ValidationError(f"legacy retirement marker changed: {name}")
        validated.append((candidate, path))

    receipt_root.mkdir(parents=True)
    shutil.copy2(plan_path, receipt_root / "retirement-plan.json")
    marker_root = receipt_root / "marker-tombstones"
    for candidate, path in validated:
        for marker in candidate.get("markers", []):
            source = _safe_artifact(path, marker["path"])
            destination = marker_root / candidate["name"] / marker["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    receipt_path = receipt_root / "retirement-receipt.json"
    receipt = {
        "schema": EXPERIMENT_RETIREMENT_RECEIPT_SCHEMA,
        "status": "in-progress",
        "plan_sha256": expected_sha256,
        "reason": plan["reason"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "removed": [],
        "removed_bytes": 0,
        "claim_boundary": "retired noncanonical material; not validation evidence",
    }
    write_json(receipt_path, receipt)
    for candidate, path in validated:
        _make_writable(path)
        shutil.rmtree(path)
        receipt["removed"].append(candidate)
        receipt["removed_bytes"] += candidate["bytes"]
        write_json(receipt_path, receipt)
    receipt["status"] = "pass"
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(receipt_path, receipt)
    return receipt
