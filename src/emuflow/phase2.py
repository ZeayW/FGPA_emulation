from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .architecture import ArchitectureDB
from .io import write_json
from .ir import EmuIR
from .openparf import export_bookshelf, run_openparf
from .placement import Placement


PHASE2_REPORT_SCHEMA = "emuflow.phase2-report/v1"


def run_phase2(
    ir_path: Path,
    architecture_path: Path,
    output_dir: Path,
    openparf_result: Optional[Path] = None,
    openparf_global_result: bool = False,
    site_utilization_limit: float = 0.75,
    site_y_range: Optional[Tuple[int, int]] = None,
    openparf_install: Optional[Path] = None,
    openparf_python: Optional[Path] = None,
    reference_placement: bool = False,
) -> Dict[str, Any]:
    ir = EmuIR.load(ir_path)
    architecture = ArchitectureDB.load(architecture_path)
    bookshelf_dir = output_dir / "openparf"
    manifest = export_bookshelf(ir, architecture, bookshelf_dir)
    if openparf_result is None:
        if reference_placement:
            if openparf_global_result:
                raise ValueError(
                    "openparf_global_result is incompatible with "
                    "reference_placement"
                )
            placement = Placement.greedy_reference(architecture, ir)
            provider = "emuflow-greedy-reference"
        else:
            openparf_result = run_openparf(
                bookshelf_dir / "openparf.json",
                log_path=bookshelf_dir / "openparf.log",
                install_root=openparf_install,
                python_executable=openparf_python,
            )
            placement = Placement.from_openparf_pl(
                openparf_result, architecture, ir
            )
            provider = (
                "openparf-root-build"
                if openparf_install is None
                else "openparf-comparison-install"
            )
    elif reference_placement:
        raise ValueError(
            "reference_placement is incompatible with openparf_result"
        )
    elif openparf_global_result:
        placement = Placement.from_openparf_global_pl(
            openparf_result,
            architecture,
            ir,
            site_utilization_limit=site_utilization_limit,
            site_y_range=site_y_range,
        )
        provider = "openparf-global+emuflow-archdb-legalizer"
    else:
        placement = Placement.from_openparf_pl(
            openparf_result, architecture, ir
        )
        provider = "openparf-comparison-import"

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
