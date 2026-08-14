"""Portable source/tool closure identities for experiment DAG stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import ValidationError


EXPERIMENT_IMPLEMENTATION_CLOSURE_SCHEMA = (
    "emuflow.experiment-implementation-closure/v1"
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


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"experiment implementation {label} is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValidationError(
            f"experiment implementation {label} must be a safe relative path"
        )
    return path.as_posix()


def _closure_identity(files: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_sha256(
        {
            "schema": "emuflow.experiment-implementation-identity/v1",
            "files": list(files),
        }
    )


def _collect_files(root: Path, components: Sequence[str]) -> list[dict[str, Any]]:
    root = root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValidationError("experiment implementation root must be a directory")
    selected: dict[str, Path] = {}
    for component in components:
        relative = _safe_relative(component, "component")
        path = root / relative
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValidationError(
                    f"experiment implementation component uses a symlink: {relative}"
                )
        if not path.exists():
            raise ValidationError(
                f"experiment implementation component is missing: {relative}"
            )
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise ValidationError(
                    "experiment implementation closure contains a symlink: "
                    f"{candidate.relative_to(root).as_posix()}"
                )
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ValidationError(
                    "experiment implementation closure contains a special file"
                )
            rel = candidate.relative_to(root).as_posix()
            selected[rel] = candidate
    return [
        {
            "path": relative,
            "bytes": selected[relative].stat().st_size,
            "sha256": _sha256(selected[relative]),
        }
        for relative in sorted(selected)
    ]


def build_implementation_closure(
    root: Path, components: Sequence[str]
) -> dict[str, Any]:
    if not components:
        raise ValidationError("experiment implementation closure requires components")
    normalized_components = sorted(
        {_safe_relative(item, "component") for item in components}
    )
    files = _collect_files(root, normalized_components)
    if not files:
        raise ValidationError("experiment implementation closure contains no files")
    return {
        "schema": EXPERIMENT_IMPLEMENTATION_CLOSURE_SCHEMA,
        "status": "pass",
        "components": normalized_components,
        "files": files,
        "implementation_sha256": _closure_identity(files),
    }


def validate_implementation_closure(
    value: Mapping[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != (
        EXPERIMENT_IMPLEMENTATION_CLOSURE_SCHEMA
    ):
        raise ValidationError("experiment implementation closure schema is invalid")
    if value.get("status") != "pass":
        raise ValidationError("experiment implementation closure did not pass")
    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise ValidationError("experiment implementation components are invalid")
    normalized_components = sorted(
        {_safe_relative(item, "component") for item in components}
    )
    if components != normalized_components:
        raise ValidationError(
            "experiment implementation components must be sorted and unique"
        )
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError("experiment implementation files are invalid")
    normalized_files = []
    for record in files:
        if not isinstance(record, dict):
            raise ValidationError("experiment implementation file record is invalid")
        relative = _safe_relative(record.get("path"), "file path")
        size = record.get("bytes")
        digest = record.get("sha256")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValidationError(
                f"experiment implementation file record is invalid: {relative}"
            )
        normalized_files.append(
            {"path": relative, "bytes": size, "sha256": digest}
        )
    if files != sorted(normalized_files, key=lambda item: item["path"]) or len(
        {item["path"] for item in normalized_files}
    ) != len(normalized_files):
        raise ValidationError(
            "experiment implementation files must be sorted and unique"
        )
    digest = _closure_identity(normalized_files)
    if value.get("implementation_sha256") != digest:
        raise ValidationError("experiment implementation closure seal is broken")
    if root is not None:
        actual = _collect_files(root, normalized_components)
        if actual != normalized_files:
            raise ValidationError(
                "experiment implementation closure disagrees with its source root"
            )
    return {
        "schema": EXPERIMENT_IMPLEMENTATION_CLOSURE_SCHEMA,
        "status": "pass",
        "components": normalized_components,
        "files": normalized_files,
        "implementation_sha256": digest,
    }
