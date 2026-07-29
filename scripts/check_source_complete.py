#!/usr/bin/env python3

"""Reject opaque build artifacts and incomplete direct provider source trees."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


REQUIRED_SOURCE_FILES = {
    "cudd": (
        "configure",
        "cudd/cudd.h",
        "cudd/cuddAPI.c",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
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
        "src/gui/resources/resource.qrc",
        "src/gui/resources/icon.png",
        "src/gui/resources/google_icons/LICENSE",
        "src/gui/resources/google_icons/round_zoom_in_black_36dp.png",
        "third-party/abc/CMakeLists.txt",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
    "openparf": (
        "CMakeLists.txt",
        "cmake/Ccache.cmake/CMakeLists.txt",
        "cmake/Ccache.cmake/LICENSE",
        "openparf/placement/placer.py",
        "openparf/ops/electric_potential/src/electric_force.cpp",
        "thirdparty/blend2d/CMakeLists.txt",
        "thirdparty/blend2d/LICENSE.md",
        "thirdparty/googletest/CMakeLists.txt",
        "thirdparty/googletest/LICENSE",
        "thirdparty/pugixml/CMakeLists.txt",
        "thirdparty/pugixml/LICENSE.md",
        "thirdparty/pybind11/CMakeLists.txt",
        "thirdparty/pybind11/LICENSE",
        "thirdparty/yaml-cpp/CMakeLists.txt",
        "thirdparty/yaml-cpp/LICENSE",
        "LICENSE",
        "EMUFLOW_PROVENANCE.md",
    ),
}

REQUIRED_FIRST_PARTY_NATIVE_FILES = (
    "src/native/tlr_router.cpp",
    "src/native/tdm_ratio_optimizer.cpp",
    "src/native/tdm_partition_feedback.cpp",
    "src/native/placement_aware_pin_planner.cpp",
    "src/native/bsp_pin_solver.cpp",
    "schemas/tdm-ratio-plan-v1.schema.json",
    "schemas/sta-path-database-v1.schema.json",
    "schemas/partition-net-weights-v1.schema.json",
    "schemas/signal-position-hints-v1.schema.json",
    "schemas/placement-aware-pin-plan-v1.schema.json",
    "schemas/hardware-bsp-v1.schema.json",
    "schemas/package-pin-binding-v1.schema.json",
    "scripts/generate_synthetic_vu9p_bsp.py",
    "platforms/synthetic/xcvu9p_4fpga_mesh_bsp.json",
    "scripts/vivado/export_cut_timing_paths.tcl",
    "scripts/vivado/export_timing_path_database.tcl",
)

OPAQUE_SUFFIXES = {
    ".a",
    ".bin",
    ".bit",
    ".dll",
    ".dylib",
    ".exe",
    ".o",
    ".obj",
    ".pyd",
    ".so",
}

OPAQUE_MAGICS = (
    b"\x7fELF",
    b"!<arch>\n",
    b"MZ",
    b"\xca\xfe\xba\xbe",
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xfe\xed\xfa\xce",
)
GIT_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"

SOURCE_MANIFEST = "SOURCE_MANIFEST.json"
ALLOWED_INTEGRATIONS = {
    "default-in-tree-build",
    "in-tree-python",
    "in-tree-python-baseline",
    "source-present-runner-pending",
    "source-present-ultrascale-plus-integration-pending",
}


def _opaque_format(path: Path) -> Optional[str]:
    if path.suffix.lower() in OPAQUE_SUFFIXES:
        return "opaque build or bitstream suffix"
    try:
        with path.open("rb") as stream:
            prefix = stream.read(128)
    except OSError as error:
        return f"unreadable file: {error}"
    if prefix.startswith(GIT_LFS_POINTER):
        return "Git LFS pointer"
    if any(prefix.startswith(magic) for magic in OPAQUE_MAGICS):
        return "opaque executable/library format"
    return None


def audit(repo_root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = repo_root / SOURCE_MANIFEST
    if not manifest_path.is_file():
        errors.append(f"source manifest is missing: {SOURCE_MANIFEST}")
        manifest = {}
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"source manifest is not valid JSON: {error}")
            manifest = {}
    if manifest.get("schema") != "emuflow.source-manifest/v1":
        errors.append("source manifest has an unsupported or missing schema")
    component_ids: set[str] = set()
    for component in manifest.get("components", []):
        component_id = component.get("id")
        if not isinstance(component_id, str) or not component_id:
            errors.append("source manifest component has no stable id")
            continue
        if component_id in component_ids:
            errors.append(f"duplicate source manifest component: {component_id}")
        component_ids.add(component_id)
        integration = component.get("integration")
        if integration not in ALLOWED_INTEGRATIONS:
            errors.append(
                f"{component_id}: unsupported integration state {integration!r}"
            )
        source_paths = component.get("source_paths")
        if not isinstance(source_paths, list) or not source_paths:
            errors.append(f"{component_id}: no implementation source paths")
            continue
        for relative_path in source_paths:
            if not isinstance(relative_path, str):
                errors.append(f"{component_id}: non-string source path")
                continue
            source_path = repo_root / relative_path
            if not source_path.exists():
                errors.append(
                    f"{component_id}: implementation source is missing: "
                    f"{relative_path}"
                )
    for blocker in manifest.get("open_path_blockers", []):
        if blocker.get("status") == "complete":
            errors.append(
                f"completed blocker must be moved into components: "
                f"{blocker.get('id', '<unknown>')}"
            )

    for relative_file in REQUIRED_FIRST_PARTY_NATIVE_FILES:
        if not (repo_root / relative_file).is_file():
            errors.append(
                f"first-party native source is missing: {relative_file}"
            )
    native_root = repo_root / "src" / "native"
    for path in native_root.rglob("*"):
        if path.is_file() and (reason := _opaque_format(path)) is not None:
            errors.append(
                "first-party native tree contains a non-source artifact "
                f"({reason}): {path.relative_to(repo_root)}"
            )
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
            if path.is_file() and (reason := _opaque_format(path)) is not None:
                errors.append(
                    f"{component}: non-source artifact is present ({reason}): "
                    f"{path.relative_to(repo_root)}"
                )

    try:
        staged = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-files", "--stage"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        errors.append(f"could not inspect tracked source entries: {error}")
    else:
        for line in staged.splitlines():
            mode, _object_id, _stage_and_path = line.split(maxsplit=2)
            relative_path = _stage_and_path.split("\t", 1)[-1]
            tracked_path = repo_root / relative_path
            if mode == "160000":
                errors.append(
                    "git submodules are not allowed in the source-complete "
                    f"tree: {relative_path}"
                )
                continue
            if mode == "120000":
                try:
                    target = tracked_path.resolve(strict=False)
                    target.relative_to(repo_root)
                except (OSError, ValueError):
                    errors.append(
                        "tracked symlink escapes the source-complete tree: "
                        f"{relative_path}"
                    )
                continue
            if not tracked_path.is_file():
                errors.append(f"tracked file is missing: {relative_path}")
                continue
            if (reason := _opaque_format(tracked_path)) is not None:
                errors.append(
                    f"tracked non-source artifact is not allowed ({reason}): "
                    f"{relative_path}"
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
