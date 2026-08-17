"""Portable source/tool closure identities for experiment DAG stages."""

from __future__ import annotations

import ast
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


def _component(value: Any) -> tuple[str, tuple[str, ...]]:
    """Parse ``path`` or ``path::symbol,...`` implementation components.

    Python symbol components let a stage seal the code it actually executes
    without inheriting unrelated runners that happen to share a module.  The
    selected definitions are closed recursively over module-level helpers,
    constants, and imports, then represented by canonical Python AST.  This is
    deliberately limited to regular ``.py`` files; directories and non-Python
    tools continue to use byte-exact whole-file identities.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError("experiment implementation component is invalid")
    fields = value.split("::")
    if len(fields) > 2:
        raise ValidationError("experiment implementation component scope is invalid")
    relative = _safe_relative(fields[0], "component")
    if len(fields) == 1:
        return relative, ()
    if not relative.endswith(".py"):
        raise ValidationError(
            "experiment implementation symbol scope requires a Python file"
        )
    symbols = tuple(sorted(set(fields[1].split(","))))
    if (
        not symbols
        or any(not symbol or not symbol.isidentifier() for symbol in symbols)
        or fields[1].split(",") != list(symbols)
    ):
        raise ValidationError(
            "experiment implementation symbols must be sorted unique identifiers"
        )
    return relative, symbols


def _normalized_component(value: Any) -> str:
    relative, symbols = _component(value)
    return relative + ("::" + ",".join(symbols) if symbols else "")


def _python_symbol_payload(path: Path, requested: Sequence[str]) -> bytes:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ValidationError(
            f"experiment implementation Python source is invalid: {path.name}"
        ) from error
    definitions: dict[str, ast.AST] = {}
    import_bindings: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                import_bindings[name] = ast.Import(names=[alias])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                import_bindings[name] = ast.ImportFrom(
                    module=node.module,
                    names=[alias],
                    level=node.level,
                )
    missing = sorted(set(requested) - set(definitions))
    if missing:
        raise ValidationError(
            "experiment implementation Python symbols are missing: "
            + ", ".join(missing)
        )
    selected: dict[str, ast.AST] = {}
    pending = list(requested)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        node = definitions.get(name) or import_bindings.get(name)
        if node is None:
            continue
        selected[name] = node
        referenced = {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
        available = definitions.keys() | import_bindings.keys()
        pending.extend(sorted((referenced & available) - selected.keys()))
    document = {
        "schema": "emuflow.python-symbol-closure/v1",
        "requested": list(requested),
        "definitions": {
            name: ast.dump(
                selected[name], annotate_fields=True, include_attributes=False
            )
            for name in sorted(selected)
        },
    }
    return json.dumps(
        document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


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
    scoped: dict[str, tuple[Path, tuple[str, ...]]] = {}
    for component in components:
        relative, symbols = _component(component)
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
        if symbols:
            if not path.is_file():
                raise ValidationError(
                    "experiment implementation symbol component is not a file"
                )
            scoped[component] = (path, symbols)
            continue
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
    records = [
        {
            "path": relative,
            "bytes": selected[relative].stat().st_size,
            "sha256": _sha256(selected[relative]),
        }
        for relative in sorted(selected)
    ]
    for component in sorted(scoped):
        path, symbols = scoped[component]
        payload = _python_symbol_payload(path, symbols)
        records.append(
            {
                "path": component,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return sorted(records, key=lambda item: item["path"])


def build_implementation_closure(
    root: Path, components: Sequence[str]
) -> dict[str, Any]:
    if not components:
        raise ValidationError("experiment implementation closure requires components")
    normalized_components = sorted(
        {_normalized_component(item) for item in components}
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
        {_normalized_component(item) for item in components}
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
        relative = _normalized_component(record.get("path"))
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
