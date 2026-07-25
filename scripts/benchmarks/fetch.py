#!/usr/bin/env python3

import argparse
import json
import subprocess
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


def fetch(design: Dict[str, Any], destination_root: Path) -> Path:
    destination = destination_root / design["id"]
    revision = design["revision"]
    if destination.exists():
        if not (destination / ".git").is_dir():
            raise SystemExit(
                f"{destination} exists but is not a Git checkout; refusing to replace it"
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
