from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .io import write_json
from .platform import Platform
from .yosys import import_yosys_json


PHASE1_REPORT_SCHEMA = "emuflow.phase1-report/v1"


def run_phase1(
    yosys_json: Path,
    platform_path: Path,
    output_dir: Path,
    top: Optional[str] = None,
    clocks: Iterable[str] = (),
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    ir = import_yosys_json(yosys_json, top=top, clocks=clocks)
    totals = ir.resource_totals()

    fit_by_fpga = {
        fpga.id: totals.fits_capacity(fpga.effective_capacity)
        for fpga in platform.fpgas
    }
    capacity_fields = sorted(
        {
            resource
            for fpga in platform.fpgas
            for resource in fpga.effective_capacity
        }
    )
    aggregate_capacity = {
        resource: sum(
            fpga.effective_capacity.get(resource, 0)
            for fpga in platform.fpgas
        )
        for resource in capacity_fields
    }
    fits_on_platform = totals.fits_capacity(aggregate_capacity)
    single_fpga_fit = any(fit_by_fpga.values())
    cut_classes = Counter(net["cut_class"] for net in ir.value["nets"])
    report: Dict[str, Any] = {
        "schema": PHASE1_REPORT_SCHEMA,
        "phase": 1,
        "status": "pass" if fits_on_platform else "capacity_error",
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "resource_totals": totals.to_dict(include_zeros=False),
        "fits_on_fpga": fit_by_fpga,
        "fits_on_platform": fits_on_platform,
        "fit_scope": (
            "single_fpga"
            if single_fpga_fit
            else "aggregate_platform"
            if fits_on_platform
            else None
        ),
        "aggregate_effective_capacity": aggregate_capacity,
        "cut_classes": dict(sorted(cut_classes.items())),
        "warnings": list(ir.value.get("warnings", [])),
        "artifacts": {
            "emuir": "design.emuir.json",
            "platform": "platform.normalized.json",
            "report": "phase1_report.json",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "design.emuir.json", ir.to_dict())
    write_json(output_dir / "platform.normalized.json", platform.to_dict())
    write_json(output_dir / "phase1_report.json", report)
    return report
