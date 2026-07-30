"""One-command, source-built open FPGA physical-flow orchestration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import EmuFlowError, ValidationError
from .io import write_json
from .packed_netlist import run_packed_netlist_import
from .packed_placement import run_packed_openparf_placement
from .vpr import (
    VTR_HARD_BLOCK_PROFILE,
    run_vpr,
    run_vpr_route_packed,
    run_vtr_yosys,
)
from .vtr_architecture import (
    fetch_pinned_vtr_architecture,
    read_vpr_placement_dimensions,
    run_vtr_architecture_import,
)


OPEN_PHYSICAL_FLOW_SCHEMA = "emuflow.open-physical-flow/v1"
OPEN_PHYSICAL_FLOW_PROVIDER = (
    "yosys+vpr-pack+openparf-place+vpr-route"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_open_physical_flow_report(
    report: Dict[str, Any],
) -> Dict[str, Any]:
    if report.get("schema") != OPEN_PHYSICAL_FLOW_SCHEMA:
        raise ValidationError("open physical-flow report schema is invalid")
    if report.get("status") != "pass":
        raise ValidationError("open physical-flow report did not pass")
    stages = report.get("stages")
    required = (
        "synthesis",
        "baseline_vpr",
        "architecture_import",
        "packed_contract",
        "openparf_placement",
        "final_vpr_route",
    )
    if not isinstance(stages, dict) or tuple(stages) != required:
        raise ValidationError("open physical-flow stages are incomplete")
    for name in required:
        if not isinstance(stages[name], dict) or (
            stages[name].get("status") != "pass"
        ):
            raise ValidationError(
                f"open physical-flow stage {name!r} did not pass"
            )

    synthesis = stages["synthesis"]
    baseline = stages["baseline_vpr"]
    packed = stages["packed_contract"]
    placement = stages["openparf_placement"]
    routed = stages["final_vpr_route"]
    if baseline.get("circuit", {}).get("sha256") != synthesis.get("sha256"):
        raise ValidationError("baseline VPR circuit is not the synthesized eBLIF")
    if packed.get("source_sha256") != baseline.get("artifacts", {}).get(
        "packed_netlist", {}
    ).get("sha256"):
        raise ValidationError("packed contract is not bound to baseline VPR")
    designs = {
        packed.get("design"),
        placement.get("design"),
        routed.get("route_check", {}).get("design"),
    }
    if None in designs or len(designs) != 1:
        raise ValidationError("open physical-flow design identities disagree")
    if routed.get("architecture", {}).get("sha256") != baseline.get(
        "architecture", {}
    ).get("sha256"):
        raise ValidationError("baseline and final VPR architectures disagree")
    if routed.get("circuit", {}).get("sha256") != synthesis.get("sha256"):
        raise ValidationError("final VPR circuit is not the synthesized eBLIF")
    if routed.get("route_check", {}).get("status") != "pass":
        raise ValidationError("final independent route check did not pass")

    array = report.get("array")
    placement_array = placement.get("vpr_placement", {}).get("array")
    if (
        not isinstance(array, dict)
        or array != placement_array
        or not all(
            isinstance(array.get(key), int) and array[key] > 0
            for key in ("width", "height")
        )
    ):
        raise ValidationError("open physical-flow array identity is invalid")
    return {
        "status": "pass",
        "design": designs.pop(),
        "array": array,
        "hard_blocks": report.get("hard_blocks") is True,
    }


def run_open_physical_flow(
    sources: Iterable[Path],
    top: str,
    output_dir: Path,
    *,
    architecture: Optional[Path] = None,
    architecture_id: str = VTR_HARD_BLOCK_PROFILE,
    hard_blocks: bool = True,
    yosys: Optional[str] = None,
    vpr: Optional[str] = None,
    architecture_importer: Optional[str] = None,
    packed_importer: Optional[str] = None,
    route_checker: Optional[str] = None,
    openparf_install: Optional[Path] = None,
    openparf_python: Optional[Path] = None,
    seed: int = 1,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    """Run the checked open academic RTL-to-routed-artifact flow."""

    source_list = [path.resolve() for path in sources]
    if not source_list:
        raise EmuFlowError("open physical flow requires at least one RTL source")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise EmuFlowError(
                "open physical-flow output path must be an empty directory: "
                f"{output_dir}"
            )
    output_dir.mkdir(parents=True, exist_ok=True)

    architecture_dir = output_dir / "architecture"
    synthesis_dir = output_dir / "synthesis"
    architecture_dir.mkdir(parents=True, exist_ok=True)
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    source_url = None
    if architecture is None:
        architecture_path = architecture_dir / "vtr-flagship.xml"
        architecture_source = fetch_pinned_vtr_architecture(
            architecture_path
        )
        architecture_source["mode"] = "pinned-fetch"
        source_url = architecture_source.get("source")
    else:
        architecture_path = architecture.resolve()
        if not architecture_path.is_file():
            raise EmuFlowError(
                f"VTR architecture does not exist: {architecture_path}"
            )
        architecture_source = {
            "status": "pass",
            "mode": "provided",
            "path": str(architecture_path),
            "sha256": _sha256(architecture_path),
        }

    circuit = synthesis_dir / "design.eblif"
    synthesis_report = run_vtr_yosys(
        source_list,
        top,
        circuit,
        executable=yosys,
        log_path=synthesis_dir / "yosys.log",
        hard_blocks=hard_blocks,
    )
    baseline_dir = output_dir / "vpr-baseline"
    baseline_report = run_vpr(
        architecture_path,
        circuit,
        baseline_dir,
        executable=vpr,
        seed=seed,
        route_channel_width=route_channel_width,
    )
    baseline_netlist = Path(
        baseline_report["artifacts"]["packed_netlist"]["path"]
    )
    baseline_placement = Path(
        baseline_report["artifacts"]["placement"]["path"]
    )
    width, height = read_vpr_placement_dimensions(baseline_placement)

    architecture_db = architecture_dir / "architecture.json"
    timing_db = architecture_dir / "timing.json"
    architecture_report = run_vtr_architecture_import(
        input_path=architecture_path,
        architecture_output_path=architecture_db,
        timing_output_path=timing_db,
        architecture_id=architecture_id,
        width=width,
        height=height,
        source_url=source_url,
        executable=architecture_importer,
    )
    packed_contract = baseline_dir / "packed-contract.json"
    packed_report = run_packed_netlist_import(
        baseline_netlist,
        packed_contract,
        architecture_path=architecture_path,
        circuit_path=circuit,
        executable=packed_importer,
    )
    openparf_dir = output_dir / "openparf-placement"
    placement_report = run_packed_openparf_placement(
        packed_contract,
        architecture_db,
        openparf_dir,
        openparf_install=openparf_install,
        openparf_python=openparf_python,
    )
    final_placement = Path(
        placement_report["artifacts"]["vpr_placement"]
    )
    route_report = run_vpr_route_packed(
        architecture_path,
        circuit,
        baseline_netlist,
        packed_contract,
        final_placement,
        output_dir / "vpr-final-route",
        executable=vpr,
        route_checker=route_checker,
        route_channel_width=route_channel_width,
    )

    report = {
        "schema": OPEN_PHYSICAL_FLOW_SCHEMA,
        "status": "pass",
        "provider": OPEN_PHYSICAL_FLOW_PROVIDER,
        "top": top,
        "sources": [str(path) for path in source_list],
        "hard_blocks": hard_blocks,
        "architecture_source": architecture_source,
        "array": {"width": width, "height": height},
        "stages": {
            "synthesis": synthesis_report,
            "baseline_vpr": baseline_report,
            "architecture_import": architecture_report,
            "packed_contract": packed_report,
            "openparf_placement": placement_report,
            "final_vpr_route": route_report,
        },
    }
    report["summary"] = validate_open_physical_flow_report(report)
    write_json(output_dir / "open-physical-flow-report.json", report)
    return report
