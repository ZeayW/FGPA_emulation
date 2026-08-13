"""Content-addressed checkpoint reuse for staged validation experiments."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .validation_farm import FARM_SPEC_SCHEMA


EXPERIMENT_SPEC_SCHEMA = "emuflow.experiment-dag-spec/v1"
EXPERIMENT_PLAN_SCHEMA = "emuflow.experiment-dag-plan/v1"
EXPERIMENT_CHECKPOINT_SCHEMA = "emuflow.experiment-checkpoint/v1"

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_STAGES = ("shared-phase1-5", "phase6", "phase7")
_PROVIDERS = ("baseline", "placement-aware", "chimew")
_TOKEN_RE = re.compile(
    r"\{(output_dir|artifact_root|dependency:([a-z0-9_.-]+))\}"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"experiment {label} must be a non-empty string")
    return value


def _identifier(value: Any, label: str) -> str:
    result = _string(value, label)
    if _ID_RE.fullmatch(result) is None:
        raise ValidationError(
            f"experiment {label} may contain lowercase letters, digits, '.', '_', '-'; "
            f"got {result!r}"
        )
    return result


def _safe_relative(value: Any, label: str) -> str:
    result = _string(value, label)
    path = Path(result)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValidationError(f"experiment {label} must be a safe relative path")
    return path.as_posix()


def _safe_artifact(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValidationError(
                f"experiment checkpoint artifact uses a symlink: {relative}"
            )
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValidationError(
            f"experiment checkpoint artifact escapes its root: {relative}"
        )
    if not resolved.exists():
        raise ValidationError(
            f"experiment checkpoint artifact is missing: {relative}"
        )
    if not resolved.is_file() and not resolved.is_dir():
        raise ValidationError(
            f"experiment checkpoint artifact is not a file/directory: {relative}"
        )
    return resolved


def _artifact_digest(path: Path) -> tuple[str, str, int]:
    if path.is_file():
        return "file", _sha256(path), path.stat().st_size
    records = []
    total = 0
    for child in sorted(path.rglob("*")):
        relative = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise ValidationError(
                f"experiment checkpoint directory contains symlink: {relative}"
            )
        if child.is_dir():
            records.append({"path": relative, "kind": "directory"})
        elif child.is_file():
            size = child.stat().st_size
            total += size
            records.append(
                {
                    "path": relative,
                    "kind": "file",
                    "bytes": size,
                    "sha256": _sha256(child),
                }
            )
        else:
            raise ValidationError(
                f"experiment checkpoint directory contains special file: {relative}"
            )
    return "directory", _canonical_sha256(records), total


def _validate_node(raw: Any, seen: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("experiment nodes must be objects")
    node_id = _identifier(raw.get("id"), "node id")
    stage = _string(raw.get("stage"), f"node {node_id} stage")
    if stage not in _STAGES:
        raise ValidationError(f"experiment node {node_id} stage is invalid")
    dependencies = raw.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and item for item in dependencies
    ):
        raise ValidationError(
            f"experiment node {node_id} dependencies must be a string list"
        )
    if len(dependencies) != len(set(dependencies)):
        raise ValidationError(f"experiment node {node_id} dependencies are duplicated")
    if any(dependency not in seen for dependency in dependencies):
        raise ValidationError(
            f"experiment node {node_id} dependencies must precede the node"
        )
    if stage == "shared-phase1-5" and dependencies:
        raise ValidationError("shared Phase 1-5 checkpoint cannot have dependencies")
    if stage == "phase6" and (
        len(dependencies) != 1 or seen[dependencies[0]]["stage"] != "shared-phase1-5"
    ):
        raise ValidationError(
            f"experiment Phase 6 node {node_id} must depend on one shared Phase 1-5 node"
        )
    if stage == "phase7" and (
        len(dependencies) != 1 or seen[dependencies[0]]["stage"] != "phase6"
    ):
        raise ValidationError(
            f"experiment Phase 7 node {node_id} must depend on one Phase 6 node"
        )

    provider = raw.get("provider")
    if stage == "shared-phase1-5":
        if provider is not None or raw.get("physical_seed") is not None:
            raise ValidationError("shared Phase 1-5 node cannot select provider/seed")
    else:
        if provider not in _PROVIDERS:
            raise ValidationError(f"experiment node {node_id} provider is invalid")
        if stage == "phase6" and raw.get("physical_seed") is not None:
            raise ValidationError("Phase 6 checkpoint cannot select physical seed")
        if stage == "phase7":
            seed = raw.get("physical_seed")
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise ValidationError(
                    f"experiment Phase 7 node {node_id} physical seed is invalid"
                )
            dependency_provider = seen[dependencies[0]].get("provider")
            if dependency_provider != provider:
                raise ValidationError(
                    f"experiment Phase 7 node {node_id} provider disagrees with Phase 6"
                )

    inputs = raw.get("inputs", {})
    if not isinstance(inputs, dict) or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and _DIGEST_RE.fullmatch(value) is not None
        for key, value in inputs.items()
    ):
        raise ValidationError(
            f"experiment node {node_id} inputs must map labels to SHA-256 values"
        )
    if stage == "shared-phase1-5" and not inputs:
        raise ValidationError(
            "shared Phase 1-5 checkpoint requires explicit source/platform input hashes"
        )
    configuration = raw.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ValidationError(
            f"experiment node {node_id} configuration must be an object"
        )
    if stage == "phase7":
        backend = configuration.get("physical_backend")
        workers = configuration.get("physical_workers")
        if not isinstance(backend, str) or not backend:
            raise ValidationError(
                f"experiment Phase 7 node {node_id} must seal physical_backend"
            )
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValidationError(
                f"experiment Phase 7 node {node_id} must seal physical_workers"
            )
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(item, str) and item for item in command
    ):
        raise ValidationError(
            f"experiment node {node_id} command must be a non-empty argv list"
        )
    if not any("{output_dir}" in item for item in command):
        raise ValidationError(
            f"experiment node {node_id} command must reference {{output_dir}}"
        )
    for argument in command:
        stripped = _TOKEN_RE.sub("", argument)
        if "{" in stripped or "}" in stripped:
            raise ValidationError(
                f"experiment node {node_id} command contains unknown placeholder"
            )
        for _, dependency in _TOKEN_RE.findall(argument):
            if dependency and dependency not in dependencies:
                raise ValidationError(
                    f"experiment node {node_id} command references undeclared dependency"
                )
        if "{artifact_root}" in argument:
            raise ValidationError(
                f"experiment node {node_id} command cannot use {{artifact_root}}"
            )
    validator = raw.get("validator")
    if not isinstance(validator, list) or not validator or not all(
        isinstance(item, str) and item for item in validator
    ):
        raise ValidationError(
            f"experiment node {node_id} validator must be a non-empty argv list"
        )
    if not any("{artifact_root}" in item for item in validator):
        raise ValidationError(
            f"experiment node {node_id} validator must reference {{artifact_root}}"
        )
    for argument in validator:
        stripped = _TOKEN_RE.sub("", argument)
        if "{" in stripped or "}" in stripped or "{output_dir}" in argument:
            raise ValidationError(
                f"experiment node {node_id} validator contains an invalid placeholder"
            )
        for _, dependency in _TOKEN_RE.findall(argument):
            if dependency and dependency not in dependencies:
                raise ValidationError(
                    f"experiment node {node_id} validator references undeclared dependency"
                )
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValidationError(f"experiment node {node_id} requires artifacts")
    artifact_paths = [
        _safe_relative(item, f"node {node_id} artifact") for item in artifacts
    ]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ValidationError(f"experiment node {node_id} artifacts are duplicated")
    environment = raw.get("environment", {})
    if not isinstance(environment, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValidationError(
            f"experiment node {node_id} environment must map strings to strings"
        )
    result: Dict[str, Any] = {
        "id": node_id,
        "stage": stage,
        "dependencies": list(dependencies),
        "inputs": dict(sorted(inputs.items())),
        "configuration": configuration,
        "command": list(command),
        "validator": list(validator),
        "environment": dict(sorted(environment.items())),
        "artifacts": artifact_paths,
    }
    if provider is not None:
        result["provider"] = provider
    if raw.get("physical_seed") is not None:
        result["physical_seed"] = raw["physical_seed"]
    return result


def validate_experiment_spec(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != EXPERIMENT_SPEC_SCHEMA:
        raise ValidationError("experiment DAG spec schema is invalid")
    experiment_id = _identifier(value.get("experiment_id"), "experiment_id")
    source_commit = _string(value.get("source_commit"), "source_commit").lower()
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise ValidationError("experiment source_commit must be full 40-hex")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValidationError("experiment DAG spec requires nodes")
    normalized: Dict[str, Dict[str, Any]] = {}
    ordered = []
    for raw in nodes:
        node = _validate_node(raw, normalized)
        if node["id"] in normalized:
            raise ValidationError("experiment node IDs must be unique")
        normalized[node["id"]] = node
        ordered.append(node)
    phase6 = [node for node in ordered if node["stage"] == "phase6"]
    phase7 = [node for node in ordered if node["stage"] == "phase7"]
    if len({node["provider"] for node in phase6}) != len(phase6):
        raise ValidationError("experiment has duplicate Phase 6 provider checkpoints")
    phase7_arms = {(node["provider"], node["physical_seed"]) for node in phase7}
    if len(phase7_arms) != len(phase7):
        raise ValidationError("experiment has duplicate Phase 7 provider/seed arms")
    return {
        "schema": EXPERIMENT_SPEC_SCHEMA,
        "experiment_id": experiment_id,
        "source_commit": source_commit,
        "nodes": ordered,
    }


def _node_key(
    source_commit: str,
    node: Mapping[str, Any],
    dependency_keys: Mapping[str, str],
) -> str:
    identity = {
        "schema": "emuflow.experiment-node-identity/v1",
        "source_commit": source_commit,
        "node": node,
        "dependency_keys": dict(sorted(dependency_keys.items())),
    }
    return _canonical_sha256(identity)


def _checkpoint_manifest(cache_root: Path, key: str) -> Path:
    return cache_root / "objects" / key / "checkpoint.json"


def validate_experiment_checkpoint(
    manifest_path: Path,
    *,
    expected_key: Optional[str] = None,
) -> Dict[str, Any]:
    value = read_json(manifest_path)
    if value.get("schema") != EXPERIMENT_CHECKPOINT_SCHEMA:
        raise ValidationError("experiment checkpoint schema is invalid")
    key = _string(value.get("key"), "checkpoint key")
    if _DIGEST_RE.fullmatch(key) is None or (
        expected_key is not None and key != expected_key
    ):
        raise ValidationError("experiment checkpoint key is invalid")
    output_dir = Path(_string(value.get("output_dir"), "checkpoint output_dir"))
    if not output_dir.is_absolute() or output_dir.is_symlink() or not output_dir.is_dir():
        raise ValidationError("experiment checkpoint output directory is invalid")
    artifacts = value.get("artifacts")
    expected = value.get("expected_artifacts")
    if not isinstance(artifacts, dict) or not isinstance(expected, list):
        raise ValidationError("experiment checkpoint artifact table is invalid")
    if sorted(artifacts) != sorted(expected):
        raise ValidationError("experiment checkpoint artifact coverage is incomplete")
    for relative in sorted(expected):
        record = artifacts.get(relative)
        if not isinstance(record, dict):
            raise ValidationError("experiment checkpoint artifact record is invalid")
        path = _safe_artifact(output_dir, relative)
        kind, digest, size = _artifact_digest(path)
        if record != {"kind": kind, "sha256": digest, "bytes": size}:
            raise ValidationError(
                f"experiment checkpoint artifact seal is broken: {relative}"
            )
    if value.get("status") != "pass":
        raise ValidationError("experiment checkpoint did not pass")
    return value


def _cached_checkpoint(cache_root: Path, key: str) -> Optional[Dict[str, Any]]:
    manifest = _checkpoint_manifest(cache_root, key)
    if not manifest.is_file():
        return None
    return validate_experiment_checkpoint(manifest, expected_key=key)


def plan_experiment(
    spec_path: Path, cache_root: Path, output_path: Path
) -> Dict[str, Any]:
    spec_raw = read_json(spec_path)
    spec = validate_experiment_spec(spec_raw)
    cache_root = cache_root.expanduser().resolve()
    keys: Dict[str, str] = {}
    states: Dict[str, str] = {}
    records = []
    for node in spec["nodes"]:
        dependency_keys = {dependency: keys[dependency] for dependency in node["dependencies"]}
        key = _node_key(spec["source_commit"], node, dependency_keys)
        keys[node["id"]] = key
        cached = _cached_checkpoint(cache_root, key)
        if cached is not None:
            state = "reuse"
            output_dir = cached["output_dir"]
        elif all(states[dependency] == "reuse" for dependency in node["dependencies"]):
            state = "ready"
            output_dir = str((cache_root / "objects" / key / "output").resolve())
        else:
            state = "waiting"
            output_dir = str((cache_root / "objects" / key / "output").resolve())
        states[node["id"]] = state
        records.append(
            {
                **node,
                "key": key,
                "dependency_keys": dependency_keys,
                "state": state,
                "output_dir": output_dir,
            }
        )
    plan = {
        "schema": EXPERIMENT_PLAN_SCHEMA,
        "experiment_id": spec["experiment_id"],
        "source_commit": spec["source_commit"],
        "spec_sha256": _canonical_sha256(spec_raw),
        "cache_root": str(cache_root),
        "nodes": records,
        "counts": {
            state: sum(item["state"] == state for item in records)
            for state in ("reuse", "ready", "waiting")
        },
    }
    write_json(output_path, plan)
    return plan


def _load_plan(path: Path, expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    if expected_sha256 is not None:
        if _DIGEST_RE.fullmatch(expected_sha256) is None or _sha256(path) != expected_sha256:
            raise ValidationError("experiment plan seal is broken")
    value = read_json(path)
    if value.get("schema") != EXPERIMENT_PLAN_SCHEMA:
        raise ValidationError("experiment plan schema is invalid")
    cache_root = Path(_string(value.get("cache_root"), "plan cache_root"))
    if not cache_root.is_absolute():
        raise ValidationError("experiment plan cache_root must be absolute")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValidationError("experiment plan requires nodes")
    return value


def _seal_checkpoint(
    cache_root: Path,
    node: Mapping[str, Any],
    output_dir: Path,
    *,
    storage: str,
) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    for relative in node["artifacts"]:
        kind, digest, size = _artifact_digest(_safe_artifact(output_dir, relative))
        artifacts[relative] = {"kind": kind, "sha256": digest, "bytes": size}
    manifest = {
        "schema": EXPERIMENT_CHECKPOINT_SCHEMA,
        "key": node["key"],
        "node_id": node["id"],
        "stage": node["stage"],
        "provider": node.get("provider"),
        "physical_seed": node.get("physical_seed"),
        "dependency_keys": node["dependency_keys"],
        "storage": storage,
        "output_dir": str(output_dir.resolve()),
        "expected_artifacts": node["artifacts"],
        "artifacts": artifacts,
        "status": "pass",
    }
    object_root = cache_root / "objects" / node["key"]
    object_root.mkdir(parents=True, exist_ok=True)
    write_json(object_root / "checkpoint.json", manifest)
    return validate_experiment_checkpoint(
        object_root / "checkpoint.json", expected_key=node["key"]
    )


def _dependency_outputs(
    node: Mapping[str, Any], cache_root: Path
) -> Dict[str, str]:
    result = {}
    for dependency, key in node["dependency_keys"].items():
        checkpoint = _cached_checkpoint(cache_root, key)
        if checkpoint is None:
            raise ValidationError(
                f"experiment node {node['id']} dependency {dependency} is not cached"
            )
        result[dependency] = checkpoint["output_dir"]
    return result


def _expand_argv(
    template: list[str],
    dependency_outputs: Mapping[str, str],
    *,
    output_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
) -> list[str]:
    def replace(argument: str) -> str:
        def token(match: re.Match[str]) -> str:
            label = match.group(1)
            if label == "output_dir":
                if output_dir is None:
                    raise ValidationError("experiment argv lacks output_dir binding")
                return str(output_dir)
            if label == "artifact_root":
                if artifact_root is None:
                    raise ValidationError("experiment argv lacks artifact_root binding")
                return str(artifact_root)
            return dependency_outputs[match.group(2)]

        return _TOKEN_RE.sub(token, argument)

    return [replace(argument) for argument in template]


def _run_validator(
    node: Mapping[str, Any], cache_root: Path, artifact_root: Path
) -> subprocess.CompletedProcess[bytes]:
    dependencies = _dependency_outputs(node, cache_root)
    command = _expand_argv(
        node["validator"], dependencies, artifact_root=artifact_root
    )
    return subprocess.run(
        command,
        cwd=artifact_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def import_experiment_checkpoint(
    plan_path: Path,
    node_id: str,
    artifact_root: Path,
    *,
    expected_plan_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    plan = _load_plan(plan_path, expected_plan_sha256)
    nodes = {item["id"]: item for item in plan["nodes"]}
    if node_id not in nodes:
        raise ValidationError(f"experiment plan has no node {node_id!r}")
    node = nodes[node_id]
    cache_root = Path(plan["cache_root"])
    existing = _cached_checkpoint(cache_root, node["key"])
    if existing is not None:
        return {"status": "reused", "checkpoint": existing}
    for dependency, key in node["dependency_keys"].items():
        if _cached_checkpoint(cache_root, key) is None:
            raise ValidationError(
                f"experiment node {node_id} dependency {dependency} is not cached"
            )
    artifact_root = artifact_root.expanduser().resolve()
    validation = _run_validator(node, cache_root, artifact_root)
    if validation.returncode != 0:
        raise ValidationError(
            f"experiment node {node_id} independent validator failed: "
            f"{validation.stderr.decode('utf-8', errors='replace')[-2048:]}"
        )
    checkpoint = _seal_checkpoint(
        cache_root, node, artifact_root, storage="external-validated"
    )
    return {"status": "imported", "checkpoint": checkpoint}


def _expand_command(
    node: Mapping[str, Any], cache_root: Path, output_dir: Path
) -> list[str]:
    return _expand_argv(
        node["command"],
        _dependency_outputs(node, cache_root),
        output_dir=output_dir,
    )


def run_experiment_node(
    plan_path: Path,
    node_id: str,
    run_dir: Path,
    *,
    expected_plan_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    plan = _load_plan(plan_path, expected_plan_sha256)
    nodes = {item["id"]: item for item in plan["nodes"]}
    if node_id not in nodes:
        raise ValidationError(f"experiment plan has no node {node_id!r}")
    node = nodes[node_id]
    cache_root = Path(plan["cache_root"])
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / "locks").mkdir(exist_ok=True)
    lock = (cache_root / "locks" / f"{node['key']}.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        existing = _cached_checkpoint(cache_root, node["key"])
        if existing is not None:
            report = {"status": "reused", "node_id": node_id, "checkpoint": existing}
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(run_dir / "experiment-node-report.json", report)
            return report
        object_root = cache_root / "objects" / node["key"]
        if object_root.exists():
            raise EmuFlowError(
                f"experiment cache object is incomplete; preserve and inspect: {object_root}"
            )
        staging = cache_root / "staging" / f"{node['key']}.{os.getpid()}"
        staging.mkdir(parents=True, exist_ok=False)
        output_dir = staging / "output"
        output_dir.mkdir()
        run_dir.mkdir(parents=True, exist_ok=True)
        command = _expand_command(node, cache_root, output_dir)
        environment = os.environ.copy()
        environment.update(node["environment"])
        started = time.monotonic()
        with (run_dir / "command.stdout.log").open("wb") as stdout, (
            run_dir / "command.stderr.log"
        ).open("wb") as stderr:
            completed = subprocess.run(
                command,
                cwd=output_dir,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
        if completed.returncode != 0:
            failure_root = cache_root / "failures" / staging.name
            failure_root.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(failure_root)
            report = {
                "status": "failed",
                "node_id": node_id,
                "exit_code": completed.returncode,
                "elapsed_seconds": time.monotonic() - started,
                "failure_root": str(failure_root),
            }
            write_json(run_dir / "experiment-node-report.json", report)
            return report
        validation = _run_validator(node, cache_root, output_dir)
        if validation.returncode != 0:
            failure_root = cache_root / "failures" / staging.name
            failure_root.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(failure_root)
            (run_dir / "validator.stdout.log").write_bytes(validation.stdout)
            (run_dir / "validator.stderr.log").write_bytes(validation.stderr)
            report = {
                "status": "failed",
                "node_id": node_id,
                "exit_code": validation.returncode,
                "failure_stage": "independent-validator",
                "elapsed_seconds": time.monotonic() - started,
                "failure_root": str(failure_root),
            }
            write_json(run_dir / "experiment-node-report.json", report)
            return report
        final_root = cache_root / "objects" / node["key"]
        final_root.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(final_root)
        checkpoint = _seal_checkpoint(
            cache_root, node, final_root / "output", storage="managed"
        )
        report = {
            "status": "pass",
            "node_id": node_id,
            "elapsed_seconds": time.monotonic() - started,
            "checkpoint": checkpoint,
        }
        write_json(run_dir / "experiment-node-report.json", report)
        return report
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def build_experiment_farm_spec(
    plan_path: Path,
    install_dir: Path,
    nodes: list[str],
    farm_id: str,
    output_path: Path,
) -> Dict[str, Any]:
    plan = _load_plan(plan_path)
    ready = [item for item in plan["nodes"] if item["state"] == "ready"]
    if not ready:
        raise EmuFlowError("experiment plan has no ready nodes; replan or finish dependencies")
    if not nodes:
        raise ValidationError("experiment farm requires at least one HPC node")
    for node in nodes:
        _identifier(node, "HPC node")
    if len(nodes) != len(set(nodes)):
        raise ValidationError("experiment farm HPC nodes must be unique")
    plan_path = plan_path.resolve()
    plan_sha256 = _sha256(plan_path)
    spec = {
        "schema": FARM_SPEC_SCHEMA,
        "farm_id": _identifier(farm_id, "farm_id"),
        "source_commit": plan["source_commit"],
        "install_dir": str(install_dir.expanduser().resolve()),
        "nodes": nodes,
        "slots_per_node": 1,
        "tasks": [
            {
                "id": item["id"],
                "command": [
                    "{install}/bin/emuflow",
                    "experiment-cache",
                    "run-node",
                    "--plan",
                    str(plan_path),
                    "--expected-plan-sha256",
                    plan_sha256,
                    "--node",
                    item["id"],
                    "--run-dir",
                    "{run_dir}",
                ],
            }
            for item in ready
        ],
    }
    write_json(output_path, spec)
    return {
        "status": "pass",
        "ready_tasks": len(ready),
        "reused_tasks": plan["counts"]["reuse"],
        "waiting_tasks": plan["counts"]["waiting"],
        "plan_sha256": plan_sha256,
        "farm_spec": str(output_path),
    }
