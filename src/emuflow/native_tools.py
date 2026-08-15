"""Resolve native tools built from this repository without PATH fallbacks."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Optional

from .errors import EmuFlowError


REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_FLOAT_SIGNIFICANT_DIGITS = 12


def canonical_native_float(value: str) -> float:
    """Remove sub-convergence host noise from native floating output.

    Native continuous solvers use libm and IEEE-754 reductions whose final
    one or two decimal digits can differ across compatible CPU generations.
    Twelve significant digits retains roughly 1e-12 relative precision,
    three orders tighter than EmuFlow's default 1e-9 convergence/checking
    tolerance.  The two guard digits beyond the observed cross-host libm
    variation prevent that noise from perturbing a discrete tie or a sealed
    artifact hash.
    """

    parsed = float(value)
    if not math.isfinite(parsed):
        return parsed
    if parsed == 0.0:
        return 0.0
    return float(f"{parsed:.{NATIVE_FLOAT_SIGNIFICANT_DIGITS}g}")


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
        # Backends such as VPR intentionally execute in an artifact directory.
        # Bind a caller-supplied relative path to the invocation directory now,
        # before any backend changes its subprocess working directory.
        return str(Path(explicit).expanduser().resolve())

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
