from pathlib import Path
from typing import Any, Dict, Optional

from .architecture import ArchitectureDB
from .io import write_json
from .ir import EmuIR
from .openparf import export_bookshelf
from .placement import Placement


PHASE2_REPORT_SCHEMA = "emuflow.phase2-report/v1"


def run_phase2(
    ir_path: Path,
    architecture_path: Path,
    output_dir: Path,
    openparf_result: Optional[Path] = None,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    architecture = ArchitectureDB.load(architecture_path)
    bookshelf_dir = output_dir / "openparf"
    manifest = export_bookshelf(ir, architecture, bookshelf_dir)
    if openparf_result is None:
        placement = Placement.greedy_reference(architecture, ir)
        provider = "emuflow-greedy-reference"
    else:
        placement = Placement.from_openparf_pl(
            openparf_result, architecture, ir
        )
        provider = "openparf"

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "placement.json", placement.to_dict())
    (output_dir / "placement.xdc").write_text(
        placement.to_xdc(), encoding="utf-8"
    )
    (output_dir / "placement.vivado.tsv").write_text(
        placement.to_vivado_tsv(), encoding="utf-8"
    )
    (output_dir / "normalized.pl").write_text(
        placement.to_openparf_pl(), encoding="utf-8"
    )
    report: Dict[str, Any] = {
        "schema": PHASE2_REPORT_SCHEMA,
        "phase": 2,
        "status": "pass",
        "design": ir.value["design"]["name"],
        "part": architecture.part,
        "provider": provider,
        "architecture": architecture.summary(),
        "openparf_export": manifest,
        "placement": placement.summary(),
        "artifacts": {
            "openparf": "openparf/",
            "placement": "placement.json",
            "normalized_openparf_placement": "normalized.pl",
            "vivado_constraints": "placement.xdc",
            "vivado_placement_table": "placement.vivado.tsv",
            "report": "phase2_report.json",
        },
    }
    write_json(output_dir / "phase2_report.json", report)
    return report
