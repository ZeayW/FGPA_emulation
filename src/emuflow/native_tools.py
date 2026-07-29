"""Resolve native tools built from this repository without PATH fallbacks."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from .errors import EmuFlowError


REPO_ROOT = Path(__file__).resolve().parents[2]


def native_install_roots() -> tuple[Path, ...]:
    roots = []
    configured = os.environ.get("EMUFLOW_NATIVE_ROOT")
    if configured:
        roots.append(Path(configured).expanduser())

    roots.append(REPO_ROOT / "build" / "native" / "install")

    launcher = Path(sys.argv[0]).expanduser()
    if launcher.parent.name == "bin":
        roots.append(launcher.resolve().parent.parent)

    unique = []
    seen = set()
    for root in roots:
        normalized = root.resolve()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return tuple(unique)


def resolve_native_executable(
    name: str,
    explicit: Optional[str] = None,
) -> str:
    """Return an explicit or in-tree-built executable.

    An explicit path remains useful for controlled comparison experiments.
    There is deliberately no ``PATH`` lookup: the default flow must execute
    the source revision built by this repository.
    """

    if explicit is not None:
        return str(Path(explicit).expanduser())

    candidates = tuple(root / "bin" / name for root in native_install_roots())
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise EmuFlowError(
        f"in-tree {name} build product was not found; searched: {searched}. "
        "Build the monorepo with `cmake --preset release && "
        "cmake --build --preset release` or set EMUFLOW_NATIVE_ROOT."
    )
