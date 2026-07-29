#!/usr/bin/env python3

"""Reject opaque build artifacts and incomplete direct provider source trees."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REQUIRED_SOURCE_FILES = {
    "yosys": (
        "Makefile",
        "kernel/yosys.cc",
        "abc/Makefile",
        "libs/cxxopts/include/cxxopts.hpp",
        "COPYING",
        "EMUFLOW_PROVENANCE.md",
    ),
    "repart": (
        "RePart/partitioner.cpp",
        "RePart/datastructure/hypergraph.h",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
    "openroad": (
        "CMakeLists.txt",
        "src/par/src/TritonPart.cpp",
        "src/sta/CMakeLists.txt",
        "third-party/abc/CMakeLists.txt",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
    "openparf": (
        "CMakeLists.txt",
        "openparf/placement/placer.py",
        "openparf/ops/electric_potential/src/electric_force.cpp",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
}

OPAQUE_SUFFIXES = {
    ".a",
    ".dll",
    ".dylib",
    ".exe",
    ".o",
    ".obj",
    ".pyd",
    ".so",
}


def audit(repo_root: Path) -> list[str]:
    errors: list[str] = []
    third_party = repo_root / "third_party"
    for component, relative_files in REQUIRED_SOURCE_FILES.items():
        component_root = third_party / component
        for relative_file in relative_files:
            path = component_root / relative_file
            if not path.is_file():
                errors.append(
                    f"{component}: required source file is missing: {relative_file}"
                )

        for path in component_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in OPAQUE_SUFFIXES:
                errors.append(
                    f"{component}: opaque build artifact is checked in: "
                    f"{path.relative_to(repo_root)}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=Path)
    args = parser.parse_args()

    errors = audit(args.repo_root.resolve())
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("source audit passed: all direct providers are present as source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
