"""Version-pinned, collision-safe scheduling for shared-filesystem validation farms."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shlex
import socket
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .errors import EmuFlowError, ValidationError
from .experiment_storage import (
    DEFAULT_STORAGE_RESERVE_BYTES,
    preflight_experiment_storage,
    prepare_experiment_scratch,
    validate_experiment_write_path,
    validation_storage_required,
)
from .io import read_json, write_json


FARM_SPEC_SCHEMA = "emuflow.validation-farm-spec/v1"
FARM_MANIFEST_SCHEMA = "emuflow.validation-farm-manifest/v1"
FARM_TASK_SCHEMA = "emuflow.validation-farm-task/v1"
FARM_STATE_SCHEMA = "emuflow.validation-farm-state/v1"
FARM_RETIREMENT_MARKER = "RETIREMENT_PENDING.json"
FARM_RETIREMENT_MARKER_SCHEMA = "emuflow.validation-farm-retirement-pending/v1"

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PLACEHOLDERS = {"install", "run_dir", "task_dir", "task_id", "node"}
_TERMINAL_STATES = {"pass", "failed", "submit_failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _refuse_retiring_farm(farm_dir: Path) -> None:
    """Reject a farm once retirement has been committed under its launch lock."""

    marker = farm_dir / FARM_RETIREMENT_MARKER
    if os.path.lexists(marker):
        raise ValidationError("validation farm is pending retirement")


def _open_farm_launch_lock(farm_dir: Path) -> Any:
    """Open/create the launch lock without following a substituted symlink."""

    path = farm_dir / "launch.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o644)
    except OSError as error:
        raise ValidationError("validation farm launch lock is unsafe") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValidationError("validation farm launch lock is unsafe")
    return os.fdopen(descriptor, "r+", encoding="utf-8")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"validation farm {label} must be a non-empty string")
    return value


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"validation farm {label} must be a non-empty list")
    result = []
    for item in value:
        result.append(_require_string(item, label))
    return result


def _validate_id(value: Any, label: str) -> str:
    result = _require_string(value, label)
    if _ID_RE.fullmatch(result) is None:
        raise ValidationError(
            f"validation farm {label} may contain only letters, digits, "
            "'.', '_', and '-'"
        )
    return result


def _validate_commit(value: Any) -> str:
    commit = _require_string(value, "source_commit").lower()
    if _COMMIT_RE.fullmatch(commit) is None:
        raise ValidationError(
            "validation farm source_commit must be a full 40-hex revision"
        )
    return commit


def _known_hosts_binding(path: Path) -> Dict[str, str]:
    if not path.is_absolute():
        raise ValidationError(
            "validation farm known_hosts path must be absolute"
        )
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            "validation farm known_hosts must be a regular non-symlink file"
        )
    path = path.resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def _validate_known_hosts_binding(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError(
            "validation farm known_hosts binding must be an object"
        )
    path = Path(_require_string(value.get("path"), "known_hosts path"))
    expected = _require_string(value.get("sha256"), "known_hosts SHA-256")
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValidationError("validation farm known_hosts SHA-256 is invalid")
    actual = _known_hosts_binding(path)
    if actual["sha256"] != expected:
        raise ValidationError("validation farm known_hosts seal is broken")
    return actual


def _validate_worker_launcher_binding(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError(
            "validation farm worker launcher binding must be an object"
        )
    path = Path(_require_string(value.get("path"), "worker launcher path"))
    expected = _require_string(
        value.get("sha256"), "worker launcher SHA-256"
    )
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValidationError(
            "validation farm worker launcher SHA-256 is invalid"
        )
    if not path.is_absolute():
        raise ValidationError(
            "validation farm worker launcher path must be absolute"
        )
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            "validation farm worker launcher must be a regular "
            "non-symlink file"
        )
    resolved = path.resolve()
    if _sha256(resolved) != expected:
        raise ValidationError(
            "validation farm worker launcher seal is broken"
        )
    return {"path": str(resolved), "sha256": expected}


def _format_value(value: str, replacements: Mapping[str, str], label: str) -> str:
    placeholder = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    fields = set(placeholder.findall(value))
    unknown = fields - _PLACEHOLDERS
    if unknown:
        raise ValidationError(
            f"validation farm {label} uses unknown placeholders: {sorted(unknown)}"
        )
    return placeholder.sub(lambda match: replacements[match.group(1)], value)


def _validate_install(raw_path: Any, commit: str) -> Path:
    path = Path(_require_string(raw_path, "install_dir")).expanduser()
    if not path.is_absolute():
        raise ValidationError("validation farm install_dir must be absolute")
    if path.is_symlink():
        raise ValidationError(
            "validation farm install_dir must be versioned, not a symlink"
        )
    resolved = path.resolve()
    if resolved.name != commit:
        raise ValidationError(
            "validation farm install_dir basename must equal source_commit; "
            "mutable aliases such as install/current are forbidden"
        )
    if not resolved.is_dir():
        raise ValidationError(f"validation farm install_dir is missing: {resolved}")
    return resolved


def _balanced_nodes(
    tasks: Sequence[Mapping[str, Any]], nodes: Sequence[str]
) -> list[str]:
    counts = {node: 0 for node in nodes}
    result = []
    for raw_task in tasks:
        requested = raw_task.get("node")
        if requested is not None:
            node = _require_string(requested, "task node")
            if node not in counts:
                raise ValidationError(
                    f"validation farm task requests unknown node: {node}"
                )
        else:
            node = min(
                nodes,
                key=lambda candidate: (counts[candidate], nodes.index(candidate)),
            )
        counts[node] += 1
        result.append(node)
    return result


def prepare_validation_farm(
    spec_path: Path,
    output_dir: Path,
    *,
    ssh_known_hosts_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate a farm spec and atomically reserve isolated task/run directories."""

    spec = read_json(spec_path)
    if spec.get("schema") != FARM_SPEC_SCHEMA:
        raise ValidationError("validation farm spec schema is invalid")
    farm_id = _validate_id(spec.get("farm_id"), "farm_id")
    commit = _validate_commit(spec.get("source_commit"))
    install = _validate_install(spec.get("install_dir"), commit)
    nodes = _require_string_list(spec.get("nodes"), "nodes")
    if len(set(nodes)) != len(nodes):
        raise ValidationError("validation farm nodes must be unique")
    for node in nodes:
        _validate_id(node, "node")
    slots = spec.get("slots_per_node", 1)
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
        raise ValidationError(
            "validation farm slots_per_node must be a positive integer"
        )
    lease_seconds = spec.get("lease_seconds", 900)
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or lease_seconds < 30
    ):
        raise ValidationError("validation farm lease_seconds must be at least 30")
    ssh = spec.get("ssh", {})
    if not isinstance(ssh, dict):
        raise ValidationError("validation farm ssh configuration must be an object")
    ssh_executable = _require_string(ssh.get("executable", "ssh"), "ssh executable")
    ssh_arguments = ssh.get("arguments", ["-o", "BatchMode=yes"])
    if not isinstance(ssh_arguments, list) or not all(
        isinstance(item, str) for item in ssh_arguments
    ):
        raise ValidationError("validation farm ssh arguments must be a string list")
    known_hosts = None
    if ssh_known_hosts_file is not None:
        if ssh.get("known_hosts") is not None:
            raise ValidationError(
                "validation farm known_hosts is specified twice"
            )
        if any("UserKnownHostsFile=" in item for item in ssh_arguments):
            raise ValidationError(
                "validation farm SSH arguments already set UserKnownHostsFile"
            )
        known_hosts = _known_hosts_binding(ssh_known_hosts_file)
        ssh_arguments = [
            *ssh_arguments,
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts['path']}",
        ]
    elif ssh.get("known_hosts") is not None:
        known_hosts = _validate_known_hosts_binding(ssh["known_hosts"])
        expected_argument = f"UserKnownHostsFile={known_hosts['path']}"
        if expected_argument not in ssh_arguments:
            raise ValidationError(
                "validation farm known_hosts binding is not used by SSH arguments"
            )
    worker_argv = spec.get("worker_argv", ["{install}/bin/emuflow"])
    worker_argv = _require_string_list(worker_argv, "worker_argv")
    worker_launcher = None
    if spec.get("worker_launcher") is not None:
        worker_launcher = _validate_worker_launcher_binding(
            spec["worker_launcher"]
        )
        if worker_argv[0] != worker_launcher["path"]:
            raise ValidationError(
                "validation farm worker_argv does not use its sealed launcher"
            )

    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValidationError("validation farm tasks must be a non-empty list")
    if not all(isinstance(task, dict) for task in tasks):
        raise ValidationError("validation farm task records must be objects")
    task_ids = [_validate_id(task.get("id"), "task id") for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise ValidationError("validation farm task IDs must be unique")
    assigned_nodes = _balanced_nodes(tasks, nodes)

    output_dir = validate_experiment_write_path(output_dir)
    storage_reserve_bytes = spec.get(
        "storage_reserve_bytes",
        DEFAULT_STORAGE_RESERVE_BYTES if validation_storage_required() else 0,
    )
    if (
        isinstance(storage_reserve_bytes, bool)
        or not isinstance(storage_reserve_bytes, int)
        or storage_reserve_bytes < 0
    ):
        raise ValidationError("validation farm storage reserve is invalid")
    task_peak_bytes = []
    for raw_task, task_id in zip(tasks, task_ids):
        peak = raw_task.get("estimated_peak_bytes")
        if peak is None and not validation_storage_required():
            # Local legacy fixtures remain readable. Validation-server farms
            # never receive this compatibility default.
            peak = 0
        if isinstance(peak, bool) or not isinstance(peak, int) or peak < 0:
            raise ValidationError(
                f"validation farm task {task_id!r} requires estimated_peak_bytes"
            )
        task_peak_bytes.append(peak)
    storage = preflight_experiment_storage(
        output_dir.parent,
        sum(task_peak_bytes),
        reserve_bytes=storage_reserve_bytes,
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise EmuFlowError(
            f"validation farm output already exists: {output_dir}"
        ) from error

    task_records = []
    try:
        (output_dir / "tasks").mkdir()
        (output_dir / "runs").mkdir()
        (output_dir / "locks").mkdir()
        for node in nodes:
            (output_dir / "locks" / node).mkdir()

        for raw_task, task_id, node, peak_bytes in zip(
            tasks, task_ids, assigned_nodes, task_peak_bytes
        ):
            task_dir = output_dir / "tasks" / task_id
            run_dir = output_dir / "runs" / task_id
            task_dir.mkdir()
            run_dir.mkdir()
            replacements = {
                "install": str(install),
                "run_dir": str(run_dir),
                "task_dir": str(task_dir),
                "task_id": task_id,
                "node": node,
            }
            raw_command = _require_string_list(raw_task.get("command"), "task command")
            if not any("{run_dir}" in argument for argument in raw_command):
                raise ValidationError(
                    f"validation farm task {task_id!r} command must use {{run_dir}}"
                )
            command = [
                _format_value(argument, replacements, f"task {task_id} command")
                for argument in raw_command
            ]
            raw_environment = raw_task.get("environment", {})
            if not isinstance(raw_environment, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_environment.items()
            ):
                raise ValidationError(
                    f"validation farm task {task_id!r} environment must map "
                    "strings to strings"
                )
            if not any("{install}" in argument for argument in raw_command) and not any(
                "{install}" in value for value in raw_environment.values()
            ):
                raise ValidationError(
                    f"validation farm task {task_id!r} must reference the "
                    "pinned {install}"
                )
            environment = {
                key: _format_value(value, replacements, f"task {task_id} environment")
                for key, value in sorted(raw_environment.items())
            }
            raw_cwd = raw_task.get("cwd", "{run_dir}")
            cwd = Path(
                _format_value(
                    _require_string(raw_cwd, "task cwd"),
                    replacements,
                    f"task {task_id} cwd",
                )
            )
            if not cwd.is_absolute():
                raise ValidationError(
                    f"validation farm task {task_id!r} cwd must be absolute"
                )
            task_path = task_dir / "task.json"
            task_record = {
                "schema": FARM_TASK_SCHEMA,
                "farm_id": farm_id,
                "task_id": task_id,
                "source_commit": commit,
                "install_dir": str(install),
                "node": node,
                "slots_per_node": slots,
                "lease_seconds": lease_seconds,
                "slot_lock_dir": str(output_dir / "locks" / node),
                "run_dir": str(run_dir),
                "task_dir": str(task_dir),
                "cwd": str(cwd),
                "command": command,
                "environment": environment,
                "command_template": raw_command,
                "environment_template": dict(sorted(raw_environment.items())),
                "cwd_template": raw_cwd,
                "estimated_peak_bytes": peak_bytes,
                "created_at": _now(),
            }
            write_json(task_path, task_record)
            write_json(
                task_dir / "state.json",
                {
                    "schema": FARM_STATE_SCHEMA,
                    "farm_id": farm_id,
                    "task_id": task_id,
                    "node": node,
                    "status": (
                        "prepared"
                        if storage["status"] == "pass"
                        else "blocked_storage"
                    ),
                    "updated_at": _now(),
                },
            )
            task_records.append(
                {
                    "id": task_id,
                    "node": node,
                    "task": str(task_path),
                    "task_sha256": _sha256(task_path),
                    "run_dir": str(run_dir),
                    "state": str(task_dir / "state.json"),
                }
            )

        base_replacements = {
            "install": str(install),
            "run_dir": "",
            "task_dir": "",
            "task_id": "",
            "node": "",
        }
        worker = [
            _format_value(value, base_replacements, "worker_argv")
            for value in worker_argv
        ]
        ssh_manifest = {"executable": ssh_executable, "arguments": ssh_arguments}
        if known_hosts is not None:
            ssh_manifest["known_hosts"] = known_hosts
        manifest = {
            "schema": FARM_MANIFEST_SCHEMA,
            "farm_id": farm_id,
            "source_commit": commit,
            "install_dir": str(install),
            "nodes": nodes,
            "slots_per_node": slots,
            "lease_seconds": lease_seconds,
            "ssh": ssh_manifest,
            "worker_argv": worker,
            "storage_reserve_bytes": storage_reserve_bytes,
            "storage_preflight": storage,
            "tasks": task_records,
            "created_at": _now(),
        }
        if worker_launcher is not None:
            manifest["worker_launcher"] = worker_launcher
        write_json(output_dir / "farm-manifest.json", manifest)
        return validate_validation_farm(output_dir)
    except BaseException:
        # A partially prepared farm is unsafe to launch. Keep it explicit instead
        # of silently deleting evidence that may explain a shared-filesystem race.
        write_json(
            output_dir / "PREPARE_FAILED.json",
            {
                "schema": FARM_STATE_SCHEMA,
                "status": "prepare_failed",
                "updated_at": _now(),
            },
        )
        raise


def validate_validation_farm(farm_dir: Path) -> Dict[str, Any]:
    farm_dir = farm_dir.resolve()
    _refuse_retiring_farm(farm_dir)
    manifest = read_json(farm_dir / "farm-manifest.json")
    if manifest.get("schema") != FARM_MANIFEST_SCHEMA:
        raise ValidationError("validation farm manifest schema is invalid")
    commit = _validate_commit(manifest.get("source_commit"))
    install = _validate_install(manifest.get("install_dir"), commit)
    worker_launcher = None
    if manifest.get("worker_launcher") is not None:
        worker_launcher = _validate_worker_launcher_binding(
            manifest["worker_launcher"]
        )
        worker_argv = manifest.get("worker_argv")
        if (
            not isinstance(worker_argv, list)
            or not worker_argv
            or worker_argv[0] != worker_launcher["path"]
        ):
            raise ValidationError(
                "validation farm manifest does not use its sealed worker launcher"
            )
    ssh = manifest.get("ssh")
    if not isinstance(ssh, dict):
        raise ValidationError("validation farm manifest SSH configuration is invalid")
    if ssh.get("known_hosts") is not None:
        binding = _validate_known_hosts_binding(ssh["known_hosts"])
        arguments = ssh.get("arguments")
        if (
            not isinstance(arguments, list)
            or f"UserKnownHostsFile={binding['path']}" not in arguments
        ):
            raise ValidationError(
                "validation farm manifest does not use its known_hosts binding"
            )
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValidationError("validation farm manifest has no tasks")
    seen = set()
    for record in tasks:
        if not isinstance(record, dict):
            raise ValidationError("validation farm manifest task record is invalid")
        task_id = _validate_id(record.get("id"), "task id")
        if task_id in seen:
            raise ValidationError("validation farm manifest has duplicate task IDs")
        seen.add(task_id)
        expected_task = (farm_dir / "tasks" / task_id / "task.json").resolve()
        if (
            Path(_require_string(record.get("task"), "task path")).resolve()
            != expected_task
        ):
            raise ValidationError(
                "validation farm task path escapes its reserved directory"
            )
        expected_sha256 = _require_string(record.get("task_sha256"), "task SHA-256")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValidationError("validation farm task SHA-256 is invalid")
        if _sha256(expected_task) != expected_sha256:
            raise ValidationError(f"validation farm task {task_id!r} seal is broken")
        task = read_json(expected_task)
        if (
            task.get("schema") != FARM_TASK_SCHEMA
            or task.get("source_commit") != commit
        ):
            raise ValidationError(
                f"validation farm task {task_id!r} is not version-pinned"
            )
        task_install = Path(
            _require_string(task.get("install_dir"), "task install_dir")
        ).resolve()
        if task_install != install:
            raise ValidationError(
                f"validation farm task {task_id!r} install differs from farm"
            )
        peak = task.get("estimated_peak_bytes")
        if isinstance(peak, bool) or not isinstance(peak, int) or peak < 0:
            raise ValidationError(
                f"validation farm task {task_id!r} storage estimate is invalid"
            )
        lease_seconds = task.get("lease_seconds")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds < 30
        ):
            raise ValidationError(
                f"validation farm task {task_id!r} lease is invalid"
            )
        expected_run = (farm_dir / "runs" / task_id).resolve()
        task_run = Path(
            _require_string(task.get("run_dir"), "task run_dir")
        ).resolve()
        if task_run != expected_run:
            raise ValidationError(
                f"validation farm task {task_id!r} run directory is not isolated"
            )
    return {
        "farm_id": manifest["farm_id"],
        "source_commit": commit,
        "install_dir": str(install),
        "nodes": list(manifest["nodes"]),
        "tasks": len(tasks),
        "status": "valid",
    }


def _claim_submission(task_dir: Path) -> None:
    claim = task_dir / "submission.claim"
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as error:
        raise EmuFlowError(
            f"validation farm task is already submitted: {task_dir.name}"
        ) from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(f"{_now()} pid={os.getpid()}\n")


def launch_validation_farm(farm_dir: Path, submit_workers: int = 8) -> Dict[str, Any]:
    """Submit every prepared task through SSH; workers detach on their target nodes.

    Detachment deliberately happens in the remote host shell, outside any
    runtime wrapper present in ``worker_argv``.  Detaching from inside a
    Singularity/Apptainer payload lets the wrapper's main process exit and can
    tear down the container runtime view while the orphaned Python worker is
    still running.  Native children started later by that worker then lose the
    container libraries they were sealed against.
    """

    if submit_workers < 1:
        raise ValidationError("validation farm submit workers must be positive")
    farm_dir = farm_dir.resolve()
    _refuse_retiring_farm(farm_dir)
    launch_lock = _open_farm_launch_lock(farm_dir)
    try:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        launch_lock.close()
        raise EmuFlowError("validation farm launch is already in progress") from error
    try:
        # Retirement writes its marker while holding this same lock and closes
        # the descriptor before removing the tree.  Rechecking after lock
        # acquisition closes the validate-before-lock race.
        _refuse_retiring_farm(farm_dir)
        return _launch_validation_farm_locked(farm_dir, submit_workers)
    finally:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_UN)
        launch_lock.close()


def _launch_validation_farm_locked(
    farm_dir: Path, submit_workers: int
) -> Dict[str, Any]:
    """Launch a farm while its caller holds the exclusive launch lock."""

    validate_validation_farm(farm_dir)
    manifest = read_json(farm_dir / "farm-manifest.json")
    ssh = manifest["ssh"]
    storage = preflight_experiment_storage(
        farm_dir,
        sum(
            read_json(Path(record["task"]))["estimated_peak_bytes"]
            for record in manifest["tasks"]
        ),
        reserve_bytes=manifest.get("storage_reserve_bytes", 0),
    )
    if storage["status"] != "pass":
        for record in manifest["tasks"]:
            state_path = Path(record["state"])
            state = read_json(state_path)
            if state.get("status") in {"prepared", "submit_failed", "blocked_storage"}:
                write_json(
                    state_path,
                    {
                        "schema": FARM_STATE_SCHEMA,
                        "farm_id": manifest["farm_id"],
                        "task_id": record["id"],
                        "node": record["node"],
                        "status": "blocked_storage",
                        "storage": storage,
                        "updated_at": _now(),
                    },
                )
        return {
            "schema": "emuflow.validation-farm-submission/v1",
            "farm_id": manifest["farm_id"],
            "source_commit": manifest["source_commit"],
            "submitted": 0,
            "skipped": 0,
            "submit_failed": 0,
            "tasks": [],
            "storage": storage,
            "status": "blocked_storage",
        }
    for record in manifest["tasks"]:
        state_path = Path(record["state"])
        state = read_json(state_path)
        if state.get("status") == "blocked_storage":
            write_json(
                state_path,
                {
                    "schema": FARM_STATE_SCHEMA,
                    "farm_id": manifest["farm_id"],
                    "task_id": record["id"],
                    "node": record["node"],
                    "status": "prepared",
                    "updated_at": _now(),
                },
            )

    def submit(record: Mapping[str, Any]) -> Dict[str, Any]:
        if ssh.get("known_hosts") is not None:
            _validate_known_hosts_binding(ssh["known_hosts"])
        task_path = Path(record["task"])
        task_dir = task_path.parent
        previous = read_json(task_dir / "state.json")
        previous_status = previous.get("status")
        if previous_status not in {"prepared", "submit_failed", "retryable"}:
            return {
                "task_id": record["id"],
                "node": record["node"],
                "status": "skipped",
                "task_status": previous_status,
            }
        if previous_status in {"submit_failed", "retryable"}:
            (task_dir / "submission.claim").unlink(missing_ok=True)
        _claim_submission(task_dir)
        write_json(
            task_dir / "state.json",
            {
                "schema": FARM_STATE_SCHEMA,
                "farm_id": manifest["farm_id"],
                "task_id": record["id"],
                "node": record["node"],
                "status": "submitting",
                **(
                    {"attempt": previous["attempt"]}
                    if isinstance(previous.get("attempt"), int)
                    and not isinstance(previous.get("attempt"), bool)
                    else {}
                ),
                "updated_at": _now(),
            },
        )
        remote_argv = list(manifest["worker_argv"]) + [
            "validation-farm",
            "worker",
            "--task",
            str(task_path),
        ]
        bootstrap_log = task_dir / "worker-bootstrap.log"
        remote_command = (
            f"nohup setsid {shlex.join(remote_argv)} </dev/null "
            f">>{shlex.quote(str(bootstrap_log))} 2>&1 & "
            "printf '%s\\n' \"$!\""
        )
        command = [ssh["executable"], *ssh["arguments"], record["node"], remote_command]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            state = {
                "schema": FARM_STATE_SCHEMA,
                "farm_id": manifest["farm_id"],
                "task_id": record["id"],
                "node": record["node"],
                "status": "submit_failed",
                "exit_code": completed.returncode,
                "stderr": completed.stderr[-4096:],
                "updated_at": _now(),
            }
            write_json(task_dir / "state.json", state)
            (task_dir / "submission.claim").unlink(missing_ok=True)
            return state
        return {
            "task_id": record["id"],
            "node": record["node"],
            "status": "submitted",
            "remote_response": completed.stdout.strip(),
        }

    workers = min(submit_workers, len(manifest["tasks"]))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        submissions = list(executor.map(submit, manifest["tasks"]))
    failures = sum(item["status"] == "submit_failed" for item in submissions)
    skipped = sum(item["status"] == "skipped" for item in submissions)
    return {
        "schema": "emuflow.validation-farm-submission/v1",
        "farm_id": manifest["farm_id"],
        "source_commit": manifest["source_commit"],
        "submitted": len(submissions) - failures - skipped,
        "skipped": skipped,
        "submit_failed": failures,
        "tasks": submissions,
        "status": "pass" if failures == 0 else "failed",
    }


def _acquire_slot(task: Mapping[str, Any]) -> tuple[Any, int]:
    lock_dir = Path(task["slot_lock_dir"])
    slots = task["slots_per_node"]
    while True:
        for slot in range(slots):
            stream = (lock_dir / f"slot-{slot}.lock").open("a+", encoding="utf-8")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                continue
            return stream, slot
        time.sleep(1.0)


def run_validation_farm_task(task_path: Path) -> Dict[str, Any]:
    """Run one task on its assigned node while holding a per-node slot lock."""

    task_path = task_path.resolve()
    validate_validation_farm(task_path.parents[2])
    task = read_json(task_path)
    if task.get("schema") != FARM_TASK_SCHEMA:
        raise ValidationError("validation farm task schema is invalid")
    task_id = _validate_id(task.get("task_id"), "task id")
    commit = _validate_commit(task.get("source_commit"))
    install = _validate_install(task.get("install_dir"), commit)
    expected_node = _require_string(task.get("node"), "task node")
    actual_node = socket.gethostname().split(".", 1)[0]
    if actual_node != expected_node:
        raise ValidationError(
            f"validation farm task {task_id!r} assigned to {expected_node}, "
            f"running on {actual_node}"
        )
    task_dir = task_path.parent
    state_path = task_dir / "state.json"
    previous = read_json(state_path)
    if previous.get("status") not in {"prepared", "submitting", "retryable"}:
        raise EmuFlowError(
            f"validation farm task {task_id!r} cannot run from state "
            f"{previous.get('status')!r}"
        )
    previous_attempt = previous.get("attempt", 0)
    if (
        isinstance(previous_attempt, bool)
        or not isinstance(previous_attempt, int)
        or previous_attempt < 0
    ):
        raise ValidationError("validation farm previous attempt is invalid")
    attempt = previous_attempt + 1
    run_root = Path(task["run_dir"])
    attempt_dir = run_root / "attempts" / f"attempt-{attempt:04d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    replacements = {
        "install": str(install),
        "run_dir": str(attempt_dir),
        "task_dir": str(task_dir),
        "task_id": task_id,
        "node": expected_node,
    }
    command = [
        _format_value(argument, replacements, f"task {task_id} command")
        for argument in task["command_template"]
    ]
    task_environment = {
        key: _format_value(value, replacements, f"task {task_id} environment")
        for key, value in task["environment_template"].items()
    }
    cwd = Path(
        _format_value(task["cwd_template"], replacements, f"task {task_id} cwd")
    )
    cwd.mkdir(parents=True, exist_ok=True)
    lease_seconds = task["lease_seconds"]
    write_json(
        state_path,
        {
            "schema": FARM_STATE_SCHEMA,
            "farm_id": task["farm_id"],
            "task_id": task_id,
            "node": expected_node,
            "status": "waiting_for_slot",
            "attempt": attempt,
            "attempt_dir": str(attempt_dir),
            "pid": os.getpid(),
            "session_id": os.getsid(0),
            "lease_expires_at": _future(lease_seconds),
            "updated_at": _now(),
        },
    )
    slot_stream, slot = _acquire_slot(task)
    started = time.monotonic()
    started_at = _now()
    write_json(
        state_path,
        {
            "schema": FARM_STATE_SCHEMA,
            "farm_id": task["farm_id"],
            "task_id": task_id,
            "node": expected_node,
            "status": "running",
            "attempt": attempt,
            "attempt_dir": str(attempt_dir),
            "slot": slot,
            "pid": os.getpid(),
            "session_id": os.getsid(0),
            "source_commit": commit,
            "install_dir": str(install),
            "started_at": started_at,
            "heartbeat_at": _now(),
            "lease_expires_at": _future(lease_seconds),
            "updated_at": _now(),
        },
    )
    environment = os.environ.copy()
    environment.update(task_environment)
    _, scratch_environment = prepare_experiment_scratch(attempt_dir)
    environment.update(scratch_environment)
    environment["EMUFLOW_FARM_ID"] = task["farm_id"]
    environment["EMUFLOW_FARM_TASK_ID"] = task_id
    environment["EMUFLOW_FARM_RUN_DIR"] = task["run_dir"]
    environment["EMUFLOW_FARM_ATTEMPT_DIR"] = str(attempt_dir)
    environment["EMUFLOW_INSTALL"] = str(install)
    environment["EMUFLOW_SOURCE_COMMIT"] = commit
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        interval = max(10, min(60, lease_seconds // 3))
        while not stop_heartbeat.wait(interval):
            write_json(
                state_path,
                {
                    "schema": FARM_STATE_SCHEMA,
                    "farm_id": task["farm_id"],
                    "task_id": task_id,
                    "node": expected_node,
                    "status": "running",
                    "attempt": attempt,
                    "attempt_dir": str(attempt_dir),
                    "slot": slot,
                    "pid": os.getpid(),
                    "session_id": os.getsid(0),
                    "source_commit": commit,
                    "install_dir": str(install),
                    "started_at": started_at,
                    "heartbeat_at": _now(),
                    "lease_expires_at": _future(lease_seconds),
                    "updated_at": _now(),
                },
            )

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        with (attempt_dir / "stdout.log").open("wb") as stdout, (
            attempt_dir / "stderr.log"
        ).open("wb") as stderr:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        status = "pass" if completed.returncode == 0 else "failed"
        result = {
            "schema": FARM_STATE_SCHEMA,
            "farm_id": task["farm_id"],
            "task_id": task_id,
            "node": expected_node,
            "status": status,
            "attempt": attempt,
            "attempt_dir": str(attempt_dir),
            "slot": slot,
            "pid": os.getpid(),
            "session_id": os.getsid(0),
            "source_commit": commit,
            "install_dir": str(install),
            "exit_code": completed.returncode,
            "started_at": started_at,
            "finished_at": _now(),
            "elapsed_seconds": time.monotonic() - started,
            "updated_at": _now(),
        }
        write_json(state_path, result)
        return result
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)
        fcntl.flock(slot_stream.fileno(), fcntl.LOCK_UN)
        slot_stream.close()


def detach_validation_farm_task(task_path: Path) -> Dict[str, Any]:
    """Fork a terminal-independent worker and return after its new session exists."""

    task_path = task_path.resolve()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid:
        os.close(write_fd)
        with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
            raw_pid = stream.read().strip()
        if not raw_pid:
            raise EmuFlowError(
                "validation farm detached worker failed before session creation"
            )
        return {"status": "detached", "pid": int(raw_pid), "task": str(task_path)}

    os.close(read_fd)
    try:
        os.setsid()
        worker_pid = os.getpid()
        task_dir = task_path.parent
        stdin_fd = os.open(os.devnull, os.O_RDONLY)
        log_fd = os.open(
            task_dir / "worker.log",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        os.dup2(stdin_fd, 0)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(stdin_fd)
        os.close(log_fd)
        os.write(write_fd, f"{worker_pid}\n".encode("ascii"))
        os.close(write_fd)
        result = run_validation_farm_task(task_path)
        os._exit(0 if result["status"] == "pass" else 1)
    except BaseException as error:
        try:
            task = read_json(task_path)
            write_json(
                task_path.parent / "state.json",
                {
                    "schema": FARM_STATE_SCHEMA,
                    "farm_id": task.get("farm_id"),
                    "task_id": task.get("task_id"),
                    "node": task.get("node"),
                    "status": "failed",
                    "pid": os.getpid(),
                    "session_id": os.getsid(0),
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "finished_at": _now(),
                    "updated_at": _now(),
                },
            )
        except BaseException:
            pass
        try:
            os.write(write_fd, b"\n")
            os.close(write_fd)
        except OSError:
            pass
        os._exit(1)


def validation_farm_status(farm_dir: Path) -> Dict[str, Any]:
    farm_dir = farm_dir.resolve()
    validate_validation_farm(farm_dir)
    manifest = read_json(farm_dir / "farm-manifest.json")
    states = []
    counts: Dict[str, int] = {}
    for record in manifest["tasks"]:
        state = read_json(Path(record["state"]))
        status = _require_string(state.get("status"), "task status")
        counts[status] = counts.get(status, 0) + 1
        states.append(state)
    terminal = sum(counts.get(status, 0) for status in _TERMINAL_STATES)
    failed = counts.get("failed", 0) or counts.get("submit_failed", 0)
    complete = terminal == len(states)
    return {
        "schema": "emuflow.validation-farm-status/v1",
        "farm_id": manifest["farm_id"],
        "source_commit": manifest["source_commit"],
        "install_dir": manifest["install_dir"],
        "counts": dict(sorted(counts.items())),
        "complete": complete,
        "tasks": states,
        "status": "failed" if failed else ("pass" if complete else "active"),
    }


def reconcile_validation_farm(
    farm_dir: Path, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Probe expired workers and make only confirmed-dead attempts retryable."""

    farm_dir = farm_dir.resolve()
    validate_validation_farm(farm_dir)
    manifest = read_json(farm_dir / "farm-manifest.json")
    ssh = manifest["ssh"]
    current = now or datetime.now(timezone.utc)
    records = []
    for record in manifest["tasks"]:
        state_path = Path(record["state"])
        state = read_json(state_path)
        if state.get("status") not in {"running", "waiting_for_slot"}:
            records.append(
                {
                    "task_id": record["id"],
                    "status": "unchanged",
                    "task_status": state.get("status"),
                }
            )
            continue
        try:
            expiry = datetime.fromisoformat(state.get("lease_expires_at"))
        except (TypeError, ValueError) as error:
            raise ValidationError(
                f"validation farm task {record['id']!r} lease is invalid"
            ) from error
        if expiry.tzinfo is None:
            raise ValidationError("validation farm lease must include a timezone")
        if expiry > current:
            records.append({"task_id": record["id"], "status": "lease-valid"})
            continue
        pid = state.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            raise ValidationError(
                f"validation farm task {record['id']!r} PID is invalid"
            )
        completed = subprocess.run(
            [
                ssh["executable"],
                *ssh["arguments"],
                record["node"],
                f"kill -0 {pid}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode == 0:
            records.append(
                {
                    "task_id": record["id"],
                    "status": "expired-but-alive",
                    "pid": pid,
                }
            )
            continue
        write_json(
            state_path,
            {
                **state,
                "status": "retryable",
                "reconciled_at": _now(),
                "reconcile_reason": "lease-expired-and-worker-absent",
                "probe_exit_code": completed.returncode,
                "updated_at": _now(),
            },
        )
        records.append(
            {"task_id": record["id"], "status": "retryable", "pid": pid}
        )
    return {
        "schema": "emuflow.validation-farm-reconcile/v1",
        "farm_id": manifest["farm_id"],
        "status": "pass",
        "tasks": records,
        "retryable": sum(item["status"] == "retryable" for item in records),
        "alive": sum(
            item["status"] == "expired-but-alive" for item in records
        ),
    }
