#!/usr/bin/env python3

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "benchmarks" / "rtl_catalog.json"
DEFAULT_DESTINATION = ROOT / "third_party" / "rtl"


def load_catalog() -> Dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as stream:
        catalog = json.load(stream)
    if catalog.get("schema") != "emuflow.rtl-catalog/v1":
        raise SystemExit(f"unsupported catalog schema in {CATALOG_PATH}")
    return catalog


def find_design(catalog: Dict[str, Any], design_id: str) -> Dict[str, Any]:
    for design in catalog["designs"]:
        if design["id"] == design_id:
            return design
    choices = ", ".join(item["id"] for item in catalog["designs"])
    raise SystemExit(f"unknown design {design_id!r}; choose one of: {choices}")


def run(command: List[str]) -> None:
    subprocess.run(command, check=True)


def _archive_stamp_matches(destination: Path, design: Dict[str, Any]) -> bool:
    stamp_path = destination / ".emuflow-source.json"
    if not stamp_path.is_file():
        return False
    with stamp_path.open("r", encoding="utf-8") as stream:
        stamp = json.load(stream)
    return (
        stamp.get("schema") == "emuflow.source-archive/v1"
        and stamp.get("design_id") == design["id"]
        and stamp.get("revision") == design["revision"]
        and stamp.get("archive_sha256") == design.get("archive_sha256")
    )


def _safe_archive_members(archive: tarfile.TarFile) -> List[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe path in source archive: {member.name!r}")
        if member.issym() or member.islnk():
            target = Path(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                raise SystemExit(
                    f"unsafe link target in source archive: {member.linkname!r}"
                )
    return members


def fetch_archive(design: Dict[str, Any], destination_root: Path) -> Path:
    url = design["archive_url"]
    expected_sha256 = design["archive_sha256"]
    destination = destination_root / design["id"]
    destination_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{design['id']}-", dir=destination_root
    ) as temporary:
        temporary_path = Path(temporary)
        archive_path = temporary_path / "source.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as response:
            with archive_path.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise SystemExit(
                f"source archive hash mismatch: got {actual_sha256}, "
                f"expected {expected_sha256}"
            )
        extract_root = temporary_path / "extract"
        extract_root.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _safe_archive_members(archive)
            archive.extractall(extract_root, members=members)
        roots = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise SystemExit(
                f"source archive must contain one root directory, found {len(roots)}"
            )
        stamp = {
            "schema": "emuflow.source-archive/v1",
            "design_id": design["id"],
            "revision": design["revision"],
            "archive_url": url,
            "archive_sha256": actual_sha256,
        }
        with (roots[0] / ".emuflow-source.json").open(
            "w", encoding="utf-8"
        ) as stream:
            json.dump(stamp, stream, indent=2, sort_keys=True)
            stream.write("\n")
        shutil.move(str(roots[0]), destination)
    return destination


def fetch(design: Dict[str, Any], destination_root: Path) -> Path:
    destination = destination_root / design["id"]
    revision = design["revision"]
    if destination.exists():
        if _archive_stamp_matches(destination, design):
            return destination
        if not (destination / ".git").is_dir():
            raise SystemExit(
                f"{destination} exists but has neither a matching archive stamp "
                "nor a Git checkout; refusing to replace it"
            )
        completed = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        current = completed.stdout.strip()
        if current != revision:
            raise SystemExit(
                f"{destination} is at {current}, expected pinned revision {revision}"
            )
        return destination

    if design.get("archive_url") and design.get("archive_sha256"):
        return fetch_archive(design, destination_root)

    destination_root.mkdir(parents=True, exist_ok=True)
    run(["git", "init", str(destination)])
    run(
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "add",
            "origin",
            design["repository"],
        ]
    )
    run(["git", "-C", str(destination), "sparse-checkout", "init", "--no-cone"])
    run(
        [
            "git",
            "-C",
            str(destination),
            "sparse-checkout",
            "set",
            "--no-cone",
            *design["sparse_paths"],
        ]
    )
    run(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--depth",
            "1",
            "origin",
            revision,
        ]
    )
    run(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List or fetch pinned open-source RTL benchmark sources."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list catalog entries")
    fetch_parser = subparsers.add_parser("fetch", help="fetch one pinned design")
    fetch_parser.add_argument("design_id")
    fetch_parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"checkout root (default: {DEFAULT_DESTINATION})",
    )
    arguments = parser.parse_args()
    catalog = load_catalog()

    if arguments.command == "list":
        for design in sorted(catalog["designs"], key=lambda item: item["priority"]):
            tops = ",".join(design["tops"])
            print(
                f"{design['id']:<14} {design['scale']:<12} "
                f"{design['language']:<13} top={tops}"
            )
        return

    design = find_design(catalog, arguments.design_id)
    destination = fetch(design, arguments.destination.resolve())
    print(
        f"fetched={design['id']} revision={design['revision']} "
        f"path={destination}"
    )


if __name__ == "__main__":
    main()
