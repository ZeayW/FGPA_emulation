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
from .contest_eda2023 import (
    import_eda2023_case,
    materialize_eda2023_rtl_boarddb,
)
from .contest_eda2024 import (
    import_eda2024_case,
    materialize_eda2024_rtl_boarddb,
)
from .contest_eda2025 import (
    import_eda2025_instance,
    materialize_eda2025_rtl_boarddb,
)
from .validation_farm import validate_validation_farm


PUBLIC_CONTEST_FETCH_REPORT_SCHEMA = "emuflow.public-contest-fetch-report/v1"
PUBLIC_CONTEST_IMPORT_REPORT_SCHEMA = "emuflow.public-contest-import-report/v1"
PUBLIC_CONTEST_BOARDDB_REPORT_SCHEMA = "emuflow.public-contest-boarddb-report/v1"
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


def _artifact_manifest(output_dir: Path) -> list[Dict[str, Any]]:
    records = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "import_report.json":
            continue
        records.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return records


def import_public_contest_case(
    matrix_path: Path,
    case_id: str,
    source_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """Validate pinned fetch provenance and dispatch one semantic importer."""

    matrix, _ = load_contest_validation_matrix(matrix_path)
    case = _find_case(matrix, case_id)
    if "import" not in case["target_gates"]:
        raise ValidationError(f"public contest case {case_id!r} has no import gate")
    source_dir = source_dir.resolve()
    checked = _validate_fetch_provenance(case, source_dir)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise EmuFlowError(f"public contest import output is not empty: {output_dir}")

    name = case_id.replace(".", "-")
    if case["suite"] == "eda2023":
        adapter = import_eda2023_case(source_dir, output_dir, name)
    elif case["suite"] == "eda2024-repart":
        adapter = import_eda2024_case(source_dir, output_dir, name)
    elif case["suite"] == "eda2025":
        adapter = import_eda2025_instance(
            source_dir / "design.info",
            source_dir / "design.net",
            source_dir / "design.topo",
            source_dir / "design.fpga.out",
            output_dir,
            name,
        )
    else:
        raise ValidationError(
            f"public contest suite {case['suite']!r} has no public import adapter"
        )
    artifacts = _artifact_manifest(output_dir)
    if not artifacts:
        raise ValidationError("public contest importer produced no artifacts")
    report = {
        "schema": PUBLIC_CONTEST_IMPORT_REPORT_SCHEMA,
        "status": "pass",
        "case_id": case_id,
        "suite": case["suite"],
        "case": case["case"],
        "tier": case["tier"],
        "gate": "import",
        "matrix_sha256": canonical_matrix_sha256(matrix),
        "source": {
            "revision_kind": case["source"]["revision_kind"],
            "revision": checked["revision"],
            "files": checked["files"],
            "input_bytes": checked["input_bytes"],
        },
        "adapter": adapter,
        "artifacts": artifacts,
        "evaluation_status": "not-run",
    }
    write_json(output_dir / "import_report.json", report)
    return report


def _validate_import_artifacts(
    matrix: Mapping[str, Any], case: Mapping[str, Any], import_dir: Path
) -> Dict[str, Any]:
    report = read_json(import_dir / "import_report.json")
    if (
        report.get("schema") != PUBLIC_CONTEST_IMPORT_REPORT_SCHEMA
        or report.get("status") != "pass"
        or report.get("case_id") != case["id"]
        or report.get("matrix_sha256") != canonical_matrix_sha256(matrix)
        or report.get("evaluation_status") != "not-run"
    ):
        raise ValidationError("public contest import report is invalid")
    records = report.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ValidationError("public contest import artifact manifest is invalid")
    seen = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValidationError(f"public contest import artifact {index} is invalid")
        relative = record.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in seen
        ):
            raise ValidationError("public contest import artifact path is invalid")
        seen.add(relative)
        path = import_dir / relative
        if (
            not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256")
        ):
            raise ValidationError(
                f"public contest import artifact {relative!r} seal is broken"
            )
    actual = {
        path.relative_to(import_dir).as_posix()
        for path in import_dir.rglob("*")
        if path.is_file() and path.name != "import_report.json"
    }
    if actual != seen:
        raise ValidationError("public contest import artifact coverage is not exact")
    return report


def materialize_public_contest_boarddb(
    matrix_path: Path,
    case_id: str,
    source_dir: Path,
    import_dir: Path,
    device_template_path: Path,
    output_dir: Path,
    *,
    lane_scale: int = 1,
    unweighted_link_lanes: int = 1,
    fabric_clock_mhz: float = 50.0,
    latency_cycles: int = 2,
) -> Dict[str, Any]:
    """Project a passed public import onto a source-qualified FPGA template."""

    matrix, _ = load_contest_validation_matrix(matrix_path)
    case = _find_case(matrix, case_id)
    if "materialize-boarddb" not in case["target_gates"]:
        raise ValidationError(
            f"public contest case {case_id!r} has no BoardDB materialization gate"
        )
    source_dir = source_dir.resolve()
    import_dir = import_dir.resolve()
    _validate_fetch_provenance(case, source_dir)
    _validate_import_artifacts(matrix, case, import_dir)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise EmuFlowError(f"public contest BoardDB output is not empty: {output_dir}")
    output_path = output_dir / "boarddb.json"
    name = case_id.replace(".", "-") + "-rtl"
    if case["suite"] == "eda2023":
        adapter = materialize_eda2023_rtl_boarddb(
            import_dir / "contest_instance.json",
            device_template_path,
            output_path,
            name=name,
            lane_scale=lane_scale,
            fabric_clock_mhz=fabric_clock_mhz,
            latency_cycles=latency_cycles,
            route_constraints_path=output_dir / "route_constraints.json",
        )
        projection = {"lane_scale": lane_scale}
    elif case["suite"] == "eda2024-repart":
        adapter = materialize_eda2024_rtl_boarddb(
            source_dir,
            device_template_path,
            output_path,
            name=name,
            lanes_per_edge=unweighted_link_lanes,
            fabric_clock_mhz=fabric_clock_mhz,
            latency_cycles=latency_cycles,
            route_constraints_path=output_dir / "route_constraints.json",
        )
        projection = {"unweighted_link_lanes": unweighted_link_lanes}
    elif case["suite"] == "eda2025":
        adapter = materialize_eda2025_rtl_boarddb(
            import_dir / "contest_instance.json",
            device_template_path,
            output_path,
            name=name,
            lane_scale=lane_scale,
            fabric_clock_mhz=fabric_clock_mhz,
            latency_cycles=latency_cycles,
            route_constraints_path=output_dir / "route_constraints.json",
        )
        projection = {"lane_scale": lane_scale}
    else:
        raise ValidationError(
            f"public contest suite {case['suite']!r} has no BoardDB adapter"
        )
    artifacts = _artifact_manifest(output_dir)
    report = {
        "schema": PUBLIC_CONTEST_BOARDDB_REPORT_SCHEMA,
        "status": "pass",
        "case_id": case_id,
        "suite": case["suite"],
        "gate": "materialize-boarddb",
        "matrix_sha256": canonical_matrix_sha256(matrix),
        "qualification": "academic-architecture-projection",
        "projection": {
            **projection,
            "fabric_clock_mhz": float(fabric_clock_mhz),
            "latency_cycles": latency_cycles,
        },
        "adapter": adapter,
        "artifacts": artifacts,
        "phase3_status": "not-run",
    }
    write_json(output_dir / "boarddb_report.json", report)
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


def build_contest_import_farm_spec(
    matrix_path: Path,
    fetch_farm_dir: Path,
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
    """Compile semantic import gates from an already-passed fetch farm."""

    matrix, coverage = load_contest_validation_matrix(matrix_path)
    source_commit = source_commit.lower()
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise ValidationError("contest farm source commit must be full 40-hex")
    if not nodes or len(set(nodes)) != len(nodes):
        raise ValidationError("contest farm nodes must be non-empty and unique")
    if slots_per_node < 1:
        raise ValidationError("contest farm slots per node must be positive")
    fetch_farm_dir = fetch_farm_dir.resolve()
    validate_validation_farm(fetch_farm_dir)
    manifest = read_json(fetch_farm_dir / "farm-manifest.json")
    if manifest.get("schema") != "emuflow.validation-farm-manifest/v1":
        raise ValidationError("contest fetch farm manifest schema is invalid")
    fetch_records = {
        record.get("id"): record
        for record in manifest.get("tasks", [])
        if isinstance(record, dict)
    }
    selected_tiers = set(tiers)
    selected_suites = set(suites) if suites is not None else None
    tasks = []
    selected_cases = []
    for case in matrix["cases"]:
        if case["tier"] not in selected_tiers or "import" not in case["target_gates"]:
            continue
        if selected_suites is not None and case["suite"] not in selected_suites:
            continue
        if case["source"]["revision_kind"] == "embedded-sha256":
            continue
        fetch_id = "fetch-" + case["id"].replace(".", "-")
        if fetch_id not in fetch_records:
            raise ValidationError(
                f"contest import case {case['id']!r} has no fetch-farm task"
            )
        state = read_json(fetch_farm_dir / "tasks" / fetch_id / "state.json")
        if state.get("status") != "pass":
            raise ValidationError(
                f"contest import case {case['id']!r} fetch task did not pass"
            )
        fetch_run = fetch_farm_dir / "runs" / fetch_id
        fetch_report = read_json(fetch_run / "fetch_report.json")
        if (
            fetch_report.get("schema") != PUBLIC_CONTEST_FETCH_REPORT_SCHEMA
            or fetch_report.get("status") != "pass"
            or fetch_report.get("case_id") != case["id"]
            or fetch_report.get("matrix_sha256") != coverage["matrix_sha256"]
        ):
            raise ValidationError(
                f"contest import case {case['id']!r} fetch report is invalid"
            )
        source_dir = (fetch_run / "input").resolve()
        _validate_fetch_provenance(case, source_dir)
        tasks.append(
            {
                "id": "import-" + case["id"].replace(".", "-"),
                "command": [
                    "{install}/bin/emuflow",
                    "contest",
                    "import-public",
                    "--matrix",
                    (
                        "{install}/share/emuflow/benchmarks/"
                        "contest_validation_matrix.json"
                    ),
                    "--case-id",
                    case["id"],
                    "--source-dir",
                    str(source_dir),
                    "--out",
                    "{run_dir}",
                ],
            }
        )
        selected_cases.append(case)
    if not tasks:
        raise ValidationError("contest farm selection produced no import tasks")
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
        "schema": "emuflow.contest-import-farm-plan/v1",
        "status": "generated",
        "farm_id": farm_id,
        "matrix_sha256": coverage["matrix_sha256"],
        "fetch_farm": str(fetch_farm_dir),
        "tasks": len(tasks),
        "input_bytes": sum(case["input_bytes"] for case in selected_cases),
        "output": str(output_path.resolve()),
    }


def build_contest_boarddb_farm_spec(
    matrix_path: Path,
    fetch_farm_dir: Path,
    import_farm_dir: Path,
    *,
    source_commit: str,
    install_dir: Path,
    nodes: Sequence[str],
    output_path: Path,
    farm_id: str,
    tiers: Iterable[str] = ("smoke",),
    suites: Optional[Iterable[str]] = None,
    slots_per_node: int = 1,
    lane_scale: int = 1,
    unweighted_link_lanes: int = 1,
) -> Dict[str, Any]:
    """Compile BoardDB projection gates from passed fetch and import farms."""

    matrix, coverage = load_contest_validation_matrix(matrix_path)
    source_commit = source_commit.lower()
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise ValidationError("contest farm source commit must be full 40-hex")
    if not nodes or len(set(nodes)) != len(nodes):
        raise ValidationError("contest farm nodes must be non-empty and unique")
    if slots_per_node < 1:
        raise ValidationError("contest farm slots per node must be positive")
    if lane_scale < 1 or unweighted_link_lanes < 1:
        raise ValidationError("contest BoardDB lane projections must be positive")

    fetch_farm_dir = fetch_farm_dir.resolve()
    import_farm_dir = import_farm_dir.resolve()
    validate_validation_farm(fetch_farm_dir)
    validate_validation_farm(import_farm_dir)
    fetch_manifest = read_json(fetch_farm_dir / "farm-manifest.json")
    import_manifest = read_json(import_farm_dir / "farm-manifest.json")
    fetch_ids = {record["id"] for record in fetch_manifest["tasks"]}
    import_ids = {record["id"] for record in import_manifest["tasks"]}
    selected_tiers = set(tiers)
    selected_suites = set(suites) if suites is not None else None
    tasks = []
    selected_cases = []
    for case in matrix["cases"]:
        if (
            case["tier"] not in selected_tiers
            or "materialize-boarddb" not in case["target_gates"]
            or case["source"]["revision_kind"] == "embedded-sha256"
        ):
            continue
        if selected_suites is not None and case["suite"] not in selected_suites:
            continue
        suffix = case["id"].replace(".", "-")
        fetch_id = "fetch-" + suffix
        import_id = "import-" + suffix
        if fetch_id not in fetch_ids or import_id not in import_ids:
            raise ValidationError(
                f"contest BoardDB case {case['id']!r} lacks an upstream task"
            )
        fetch_state = read_json(fetch_farm_dir / "tasks" / fetch_id / "state.json")
        import_state = read_json(import_farm_dir / "tasks" / import_id / "state.json")
        if fetch_state.get("status") != "pass" or import_state.get("status") != "pass":
            raise ValidationError(
                f"contest BoardDB case {case['id']!r} upstream gates did not pass"
            )
        source_dir = (fetch_farm_dir / "runs" / fetch_id / "input").resolve()
        import_dir = (import_farm_dir / "runs" / import_id).resolve()
        _validate_fetch_provenance(case, source_dir)
        _validate_import_artifacts(matrix, case, import_dir)
        tasks.append(
            {
                "id": "boarddb-" + suffix,
                "command": [
                    "{install}/bin/emuflow",
                    "contest",
                    "materialize-public-boarddb",
                    "--matrix",
                    (
                        "{install}/share/emuflow/benchmarks/"
                        "contest_validation_matrix.json"
                    ),
                    "--case-id",
                    case["id"],
                    "--source-dir",
                    str(source_dir),
                    "--import-dir",
                    str(import_dir),
                    "--device-template",
                    (
                        "{install}/share/emuflow/platforms/virtual/"
                        "academic_vtr_4fpga_mesh.json"
                    ),
                    "--lane-scale",
                    str(lane_scale),
                    "--unweighted-link-lanes",
                    str(unweighted_link_lanes),
                    "--out",
                    "{run_dir}",
                ],
            }
        )
        selected_cases.append(case)
    if not tasks:
        raise ValidationError("contest farm selection produced no BoardDB tasks")
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
        "schema": "emuflow.contest-boarddb-farm-plan/v1",
        "status": "generated",
        "farm_id": farm_id,
        "matrix_sha256": coverage["matrix_sha256"],
        "fetch_farm": str(fetch_farm_dir),
        "import_farm": str(import_farm_dir),
        "tasks": len(tasks),
        "input_bytes": sum(case["input_bytes"] for case in selected_cases),
        "output": str(output_path.resolve()),
    }
