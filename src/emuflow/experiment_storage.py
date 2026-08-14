"""Mandatory validation-server storage boundary and quota preflight."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .errors import ValidationError


VALIDATION_STORAGE_ROOT = Path("/research/d4/gds/ziyiwang21")
DEFAULT_STORAGE_RESERVE_BYTES = 10 * 1024**3
_VALIDATION_HOST_RE = re.compile(r"(?:linux10(?:-\d+)?|hpc[1-8])$")


def validation_storage_required(hostname: str | None = None) -> bool:
    override = os.environ.get("EMUFLOW_REQUIRE_RESEARCH_STORAGE")
    if override is not None:
        if override not in {"0", "1"}:
            raise ValidationError(
                "EMUFLOW_REQUIRE_RESEARCH_STORAGE must be exactly 0 or 1"
            )
        return override == "1"
    host = (hostname or socket.gethostname()).split(".", 1)[0]
    return _VALIDATION_HOST_RE.fullmatch(host) is not None


def validate_experiment_write_path(
    path: Path,
    *,
    hostname: str | None = None,
    require_research: bool | None = None,
) -> Path:
    """Reject every server write outside the designated shared quota root."""

    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = expanded.resolve()
    required = (
        validation_storage_required(hostname)
        if require_research is None
        else require_research
    )
    if not required:
        return expanded.resolve()
    root = VALIDATION_STORAGE_ROOT.resolve()
    lexical = Path(os.path.abspath(expanded))
    if lexical != root and root not in lexical.parents:
        raise ValidationError(
            "EmuFlow validation-server writes are restricted to "
            f"{VALIDATION_STORAGE_ROOT}; got {lexical}"
        )
    current = root
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise ValidationError("experiment write path escapes storage root") from error
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValidationError(
                f"experiment write path contains a symlink: {current}"
            )
    resolved = lexical.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValidationError("experiment write path escapes storage root")
    return resolved


def _quota_available_bytes(output: str) -> int | None:
    """Parse POSIX quota -v output; numeric block values are KiB."""

    available: list[int] = []
    for raw_line in output.splitlines():
        fields = raw_line.split()
        if len(fields) < 4 or not (
            fields[0].startswith("/") or fields[0].startswith("server")
        ):
            continue
        numeric = []
        for field in fields[1:4]:
            cleaned = field.rstrip("*")
            if not cleaned.isdigit():
                numeric = []
                break
            numeric.append(int(cleaned))
        if len(numeric) != 3:
            continue
        used_kib, soft_kib, hard_kib = numeric
        limits = [value for value in (soft_kib, hard_kib) if value > 0]
        if limits:
            available.append(max(0, min(limits) - used_kib) * 1024)
    return min(available) if available else None


def storage_budget(root: Path, *, query_quota: bool = True) -> dict[str, Any]:
    root = root.expanduser().resolve()
    usage = shutil.disk_usage(root)
    quota_available = None
    quota_error = None
    if query_quota:
        try:
            completed = subprocess.run(
                ["quota", "-v", "-w"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            if completed.returncode == 0:
                quota_available = _quota_available_bytes(completed.stdout)
            else:
                quota_error = completed.stderr.strip()[-1024:]
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            quota_error = type(error).__name__
    available = min(
        [
            value
            for value in (usage.free, quota_available)
            if value is not None
        ]
    )
    return {
        "root": str(root),
        "filesystem_free_bytes": usage.free,
        "quota_available_bytes": quota_available,
        "available_bytes": available,
        "quota_error": quota_error,
    }


def preflight_experiment_storage(
    root: Path,
    estimated_peak_bytes: int,
    *,
    reserve_bytes: int = DEFAULT_STORAGE_RESERVE_BYTES,
    require_research: bool | None = None,
) -> dict[str, Any]:
    if (
        isinstance(estimated_peak_bytes, bool)
        or not isinstance(estimated_peak_bytes, int)
        or estimated_peak_bytes < 0
    ):
        raise ValidationError("experiment peak storage estimate is invalid")
    if (
        isinstance(reserve_bytes, bool)
        or not isinstance(reserve_bytes, int)
        or reserve_bytes < 0
    ):
        raise ValidationError("experiment storage reserve is invalid")
    required_boundary = (
        validation_storage_required()
        if require_research is None
        else require_research
    )
    validated_root = validate_experiment_write_path(
        root, require_research=required_boundary
    )
    budget = storage_budget(validated_root, query_quota=required_boundary)
    required = estimated_peak_bytes + reserve_bytes
    quota_known = not required_boundary or budget["quota_available_bytes"] is not None
    return {
        "status": (
            "pass"
            if quota_known and budget["available_bytes"] >= required
            else "blocked_storage"
        ),
        "block_reason": (
            None
            if quota_known and budget["available_bytes"] >= required
            else "quota-unavailable"
            if not quota_known
            else "insufficient-space"
        ),
        "estimated_peak_bytes": estimated_peak_bytes,
        "reserve_bytes": reserve_bytes,
        "required_available_bytes": required,
        **budget,
    }


def prepare_experiment_scratch(
    run_root: Path, *, require_research: bool | None = None
) -> tuple[Path, dict[str, str]]:
    run_root = validate_experiment_write_path(
        run_root, require_research=require_research
    )
    scratch = run_root / "scratch"
    temporary = scratch / "tmp"
    cache = scratch / "cache"
    temporary.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    return scratch, {
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "XDG_CACHE_HOME": str(cache),
    }
