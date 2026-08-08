"""Version-pinned public contest fetching and validation-farm planning."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .contest_validation_matrix import (
    canonical_matrix_sha256,
    load_contest_validation_matrix,
)
from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json


PUBLIC_CONTEST_FETCH_REPORT_SCHEMA = "emuflow.public-contest-fetch-report/v1"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _find_case(matrix: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    matches = [record for record in matrix["cases"] if record["id"] == case_id]
    if len(matches) != 1:
        raise ValidationError(f"public contest case {case_id!r} is not in the matrix")
    return matches[0]


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_fetcher(relative: str, fetcher_root: Optional[Path]) -> Path:
    if fetcher_root is not None:
        candidate = fetcher_root.resolve() / relative
    else:
        root = _runtime_root()
        installed = root / "share" / "emuflow" / relative
        candidate = installed if installed.is_file() else root / relative
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise EmuFlowError(f"public contest fetcher is missing: {candidate}")
    return candidate


def _validate_fetch_provenance(
    case: Mapping[str, Any], source_root: Path
) -> Dict[str, Any]:
    provenance = read_json(source_root / "SOURCE.json")
    if provenance.get("schema") != "emuflow.public-benchmark-fetch/v1":
        raise ValidationError("public contest fetch provenance schema is invalid")
    if provenance.get("case") != case["case"]:
        raise ValidationError("public contest fetch provenance case does not agree")
    source = case["source"]
    revision_kind = source["revision_kind"]
    if revision_kind == "git-commit":
        actual_revision = provenance.get("commit")
    elif revision_kind == "sha256":
        actual_revision = provenance.get("archive_sha256")
    else:
        raise ValidationError("embedded contest fixtures cannot be fetched")
    if actual_revision != source["revision"]:
        raise ValidationError("public contest fetch revision does not agree")
    files = provenance.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError("public contest fetch provenance has no files")
    names = []
    total_bytes = 0
    for index, record in enumerate(files):
        if not isinstance(record, dict):
            raise ValidationError(f"public contest fetch file {index} is invalid")
        name = record.get("name")
        size = record.get("bytes")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
        ):
            raise ValidationError(f"public contest fetch file {index} is invalid")
        path = source_root / name
        if not path.is_file() or path.stat().st_size != size:
            raise ValidationError(f"public contest fetched file {name!r} is missing")
        payload = path.read_bytes()
        if "git_blob_sha1" in record:
            expected = record["git_blob_sha1"]
            actual = hashlib.sha1(
                f"blob {len(payload)}\0".encode("ascii") + payload
            ).hexdigest()
        elif "sha256" in record:
            expected = record["sha256"]
            actual = hashlib.sha256(payload).hexdigest()
        else:
            raise ValidationError(
                f"public contest fetched file {name!r} has no content digest"
            )
        if expected != actual:
            raise ValidationError(
                f"public contest fetched file {name!r} digest does not agree"
            )
        names.append(name)
        total_bytes += size
    if len(set(names)) != len(names):
        raise ValidationError("public contest fetch provenance duplicates files")
    if total_bytes != case["input_bytes"]:
        raise ValidationError("public contest fetched byte count does not agree")
    return {
        "revision": actual_revision,
        "files": len(files),
        "input_bytes": total_bytes,
    }


def fetch_public_contest_case(
    matrix_path: Path,
    case_id: str,
    output_dir: Path,
    *,
    fetcher_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run a pinned fetcher and independently validate its provenance."""

    matrix, _ = load_contest_validation_matrix(matrix_path)
    case = _find_case(matrix, case_id)
    source = case["source"]
    if source["revision_kind"] == "embedded-sha256":
        raise ValidationError("embedded contest fixtures do not have a fetch gate")
    fetcher = _resolve_fetcher(source["fetcher"], fetcher_root)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise EmuFlowError(f"public contest output is not empty: {output_dir}")
    source_root = output_dir / "input"
    completed = subprocess.run(
        [
            sys.executable,
            str(fetcher),
            "--case",
            case["case"],
            "--out",
            str(source_root),
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise EmuFlowError(
            "public contest fetch failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    checked = _validate_fetch_provenance(case, source_root)
    report = {
        "schema": PUBLIC_CONTEST_FETCH_REPORT_SCHEMA,
        "status": "pass",
        "case_id": case_id,
        "suite": case["suite"],
        "case": case["case"],
        "tier": case["tier"],
        "gate": "fetch",
        "matrix_sha256": canonical_matrix_sha256(matrix),
        "source": {
            "revision_kind": source["revision_kind"],
            "revision": checked["revision"],
        },
        "validation": {
            "files": checked["files"],
            "input_bytes": checked["input_bytes"],
            "status": "pass",
        },
        "artifacts": {"source": "input", "provenance": "input/SOURCE.json"},
    }
    write_json(output_dir / "fetch_report.json", report)
    return report


def build_contest_fetch_farm_spec(
    matrix_path: Path,
    *,
    source_commit: str,
    install_dir: Path,
    nodes: Sequence[str],
    output_path: Path,
    farm_id: str,
    tiers: Iterable[str] = ("smoke",),
    suites: Optional[Iterable[str]] = None,
    slots_per_node: int = 1,
) -> Dict[str, Any]:
    """Compile selected matrix fetch gates into a deterministic farm spec."""

    matrix, coverage = load_contest_validation_matrix(matrix_path)
    source_commit = source_commit.lower()
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise ValidationError("contest farm source commit must be full 40-hex")
    if not nodes or len(set(nodes)) != len(nodes):
        raise ValidationError("contest farm nodes must be non-empty and unique")
    if slots_per_node < 1:
        raise ValidationError("contest farm slots per node must be positive")
    selected_tiers = set(tiers)
    selected_suites = set(suites) if suites is not None else None
    tasks = []
    for case in matrix["cases"]:
        if case["tier"] not in selected_tiers:
            continue
        if selected_suites is not None and case["suite"] not in selected_suites:
            continue
        if "fetch" not in case["target_gates"]:
            continue
        if case["source"]["revision_kind"] == "embedded-sha256":
            continue
        tasks.append(
            {
                "id": "fetch-" + case["id"].replace(".", "-"),
                "command": [
                    "{install}/bin/emuflow",
                    "contest",
                    "fetch-public",
                    "--matrix",
                    (
                        "{install}/share/emuflow/benchmarks/"
                        "contest_validation_matrix.json"
                    ),
                    "--case-id",
                    case["id"],
                    "--out",
                    "{run_dir}",
                ],
            }
        )
    if not tasks:
        raise ValidationError("contest farm selection produced no fetch tasks")
    spec = {
        "schema": "emuflow.validation-farm-spec/v1",
        "farm_id": farm_id,
        "source_commit": source_commit,
        "install_dir": str(install_dir.resolve()),
        "nodes": list(nodes),
        "slots_per_node": slots_per_node,
        "tasks": tasks,
    }
    write_json(output_path, spec)
    return {
        "schema": "emuflow.contest-fetch-farm-plan/v1",
        "status": "generated",
        "farm_id": farm_id,
        "matrix_sha256": coverage["matrix_sha256"],
        "tasks": len(tasks),
        "input_bytes": sum(
            case["input_bytes"]
            for case in matrix["cases"]
            if any(task["id"] == "fetch-" + case["id"].replace(".", "-") for task in tasks)
        ),
        "output": str(output_path.resolve()),
    }
