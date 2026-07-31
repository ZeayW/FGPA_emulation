"""Vivado implementation/timing provider behind the common physical API."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import EmuFlowError, ValidationError
from .io import write_json
from .physical_backend import (
    PHYSICAL_PARTITION_RESULT_SCHEMA,
    validate_physical_partition_result,
)
from .sta import (
    import_vivado_path_database_tsv,
    validate_sta_path_database,
    write_vivado_net_map,
)
from .ir import EmuIR
from .vivado_netlist import emit_vivado_mapped_verilog


VIVADO_PARTITION_REPORT_SCHEMA = "emuflow.vivado-partition-report/v1"
_ROOT = Path(__file__).resolve().parents[2]
_IMPLEMENT_SCRIPT = _ROOT / "scripts/vivado/implement_partition.tcl"
_TIMING_ANALYSIS_SCRIPT = _ROOT / "scripts/vivado/analyze_timing.tcl"
_TIMING_SCRIPT = _ROOT / "scripts/vivado/export_timing_path_database.tcl"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_vivado(executable: Optional[str]) -> str:
    candidate = executable or "vivado"
    resolved = shutil.which(candidate)
    if resolved is None:
        path = Path(candidate).expanduser()
        if path.is_file():
            resolved = str(path.resolve())
    if resolved is None:
        raise EmuFlowError(
            "Vivado executable was not found; pass --physical-vivado or "
            "select --physical-backend open"
        )
    return resolved


def vivado_runtime_xdc(
    ir_path: Path,
    runtime: Mapping[str, Any],
) -> str:
    ir = EmuIR.load(ir_path)
    ports = {item["id"] for item in ir.value["ports"]}
    timing = runtime["timing_model"]
    dut_port = timing["dut_clock_port"]
    fabric_port = timing["fabric_clock_port"]
    missing = sorted({dut_port, fabric_port} - ports)
    if missing:
        raise ValidationError(
            "Vivado physical partition lacks required clock ports: "
            + ", ".join(missing)
        )
    dut_period = runtime["virtual_dut_clock"]["nominal_period_ns"]
    fabric_period = runtime["fabric_clock"]["period_ns"]
    cross_delay = timing["fabric_to_dut_max_delay_ns"]
    return "\n".join(
        [
            "# EmuFlow provider-neutral runtime timing contract.",
            f"create_clock -name emuflow_dut_clk -period {dut_period:.9f} "
            f"[get_ports {{{dut_port}}}]",
            f"create_clock -name emuflow_fabric_clk -period "
            f"{fabric_period:.9f} [get_ports {{{fabric_port}}}]",
            f"set_max_delay -datapath_only {cross_delay:.9f} "
            "-from [get_clocks {emuflow_fabric_clk}] "
            "-to [get_clocks {emuflow_dut_clk}]",
            "",
        ]
    )


def vivado_design_timing_xdc(
    ir_path: Path,
    clocks: Mapping[str, float],
) -> str:
    ir = EmuIR.load(ir_path)
    ports = {item["id"] for item in ir.value["ports"]}
    if not clocks:
        raise ValidationError("Vivado timing requires at least one clock")
    lines = ["# EmuFlow Vivado timing-provider clock contract."]
    for index, (clock, period) in enumerate(sorted(clocks.items())):
        if clock not in ports:
            raise ValidationError(
                f"Vivado timing clock port {clock!r} is absent from EmuIR"
            )
        if (
            isinstance(period, bool)
            or not isinstance(period, (int, float))
            or period <= 0
        ):
            raise ValidationError(
                f"Vivado timing period for {clock!r} must be positive"
            )
        lines.append(
            f"create_clock -name emuflow_clock_{index} "
            f"-period {float(period):.9f} [get_ports {{{clock}}}]"
        )
    lines.append("")
    return "\n".join(lines)


def _read_metrics(path: Path) -> Dict[str, str]:
    if not path.is_file():
        raise ValidationError(
            f"Vivado did not emit implementation metrics: {path}"
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "metric\tvalue":
        raise ValidationError("Vivado implementation metrics header is invalid")
    result: Dict[str, str] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or fields[0] in result:
            raise ValidationError(
                f"Vivado implementation metrics line {line_number} is invalid"
            )
        result[fields[0]] = fields[1]
    return result


def _integer(metrics: Mapping[str, str], name: str) -> int:
    try:
        value = int(metrics[name])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"Vivado metric {name!r} is not an integer") from error
    if value < 0:
        raise ValidationError(f"Vivado metric {name!r} is negative")
    return value


def _number(metrics: Mapping[str, str], name: str) -> float:
    try:
        return float(metrics[name])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"Vivado metric {name!r} is not numeric") from error


def _run_vivado(
    executable: str,
    script: Path,
    arguments: list[str],
    output_dir: Path,
    log_name: str,
) -> Dict[str, Any]:
    command = [
        executable,
        "-mode",
        "batch",
        "-nojournal",
        "-nolog",
        "-source",
        str(script),
        "-tclargs",
        *arguments,
    ]
    completed = subprocess.run(
        command,
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path = output_dir / log_name
    log_path.write_text(completed.stdout, encoding="utf-8")
    critical_warnings = completed.stdout.count("CRITICAL WARNING:")
    if completed.returncode != 0 or critical_warnings:
        reason = (
            f"exit code {completed.returncode}"
            if completed.returncode != 0
            else f"{critical_warnings} critical warning(s)"
        )
        raise EmuFlowError(
            f"Vivado script {script.name} failed with {reason}\n"
            + "\n".join(completed.stdout.splitlines()[-40:])
        )
    return {
        "status": "pass",
        "command": command,
        "log": str(log_path),
        "log_sha256": _sha256(log_path),
        "critical_warnings": 0,
    }


def run_vivado_timing_path_database(
    *,
    ir_path: Path,
    output_path: Path,
    clocks: Mapping[str, float],
    part: str,
    executable: Optional[str] = None,
    max_paths: int = 200000,
) -> Dict[str, Any]:
    """Use Vivado timing as a drop-in producer of the common STA PathDB."""
    if not part.startswith("xc"):
        raise ValidationError(
            f"Vivado timing requires a concrete Xilinx part, got {part!r}"
        )
    if max_paths <= 0:
        raise ValidationError("Vivado timing max paths must be positive")
    ir_path = ir_path.resolve()
    output_path = output_path.resolve()
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    vivado = _resolve_vivado(executable)
    ir = EmuIR.load(ir_path)
    mapped_verilog = output_dir / "vivado-timing-netlist.v"
    netlist_report = emit_vivado_mapped_verilog(
        ir_path,
        mapped_verilog,
        output_dir / "vivado-timing-netlist-report.json",
    )
    xdc_path = output_dir / "vivado-timing.xdc"
    xdc_path.write_text(
        vivado_design_timing_xdc(ir_path, clocks), encoding="utf-8"
    )
    analysis = _run_vivado(
        vivado,
        _TIMING_ANALYSIS_SCRIPT,
        [
            part,
            str(mapped_verilog),
            ir.value["design"]["top"],
            str(xdc_path),
            str(output_dir),
            str(len(ir.value["instances"])),
        ],
        output_dir,
        "vivado-timing-analysis.log",
    )
    metrics = _read_metrics(output_dir / "timing_metrics.tsv")
    if metrics.get("part") != part:
        raise ValidationError("Vivado timing analyzed a different part")
    if _integer(metrics, "mapped_cells") != len(ir.value["instances"]):
        raise ValidationError("Vivado timing mapped-cell coverage disagrees")
    net_map = output_dir / "vivado-net-map.tsv"
    net_map_report = write_vivado_net_map(ir_path, net_map)
    timing_tsv = output_dir / "vivado-timing-paths.tsv"
    export = _run_vivado(
        vivado,
        _TIMING_SCRIPT,
        [
            str(output_dir / "timing.dcp"),
            str(net_map),
            str(timing_tsv),
            str(max_paths),
        ],
        output_dir,
        "vivado-timing-path-export.log",
    )
    rows = [
        line
        for line in timing_tsv.read_text(encoding="utf-8").splitlines()[1:]
        if line
    ]
    if not rows:
        raise ValidationError(
            "Vivado timing produced no mapped register-to-register paths"
        )
    import_report = import_vivado_path_database_tsv(
        timing_tsv, ir_path, output_path
    )
    validation = validate_sta_path_database(output_path, ir_path)
    report = {
        "status": "pass",
        "provider": "vivado-get-timing-path-database-v1",
        "mode": "vivado-post-synthesis",
        "tool": {"executable": vivado, "version": metrics["vivado_version"]},
        "part": part,
        "netlist": netlist_report,
        "analysis": analysis,
        "path_export": export,
        "net_map": net_map_report,
        "database": import_report,
        "validation": validation,
        "output": str(output_path),
    }
    write_json(output_dir / "vivado-timing-provider-report.json", report)
    return report


def run_vivado_partition_backend(
    *,
    fpga: str,
    part: str,
    ir_path: Path,
    mapped_verilog_path: Path,
    runtime: Mapping[str, Any],
    original_cells: int,
    transport_cells: int,
    output_dir: Path,
    executable: Optional[str] = None,
    max_timing_paths: int = 10000,
    place_directive: str = "Default",
    route_directive: str = "Default",
) -> Dict[str, Any]:
    if not part.startswith("xc"):
        raise ValidationError(
            f"Vivado backend requires a concrete Xilinx part, got {part!r}"
        )
    if max_timing_paths <= 0:
        raise ValidationError("Vivado max timing paths must be positive")
    ir_path = ir_path.resolve()
    mapped_verilog_path = mapped_verilog_path.resolve()
    for name, path in (("EmuIR", ir_path), ("mapped Verilog", mapped_verilog_path)):
        if not path.is_file() or path.stat().st_size == 0:
            raise EmuFlowError(f"Vivado {name} input is missing: {path}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    vivado = _resolve_vivado(executable)
    ir = EmuIR.load(ir_path)
    top = ir.value["design"]["top"]
    expected_cells = original_cells + transport_cells
    if len(ir.value["instances"]) != expected_cells:
        raise ValidationError(
            f"Vivado input instance count for {fpga} disagrees with accounting"
        )

    xdc_path = output_dir / "runtime.xdc"
    xdc_path.write_text(vivado_runtime_xdc(ir_path, runtime), encoding="utf-8")
    implementation = _run_vivado(
        vivado,
        _IMPLEMENT_SCRIPT,
        [
            part,
            str(mapped_verilog_path),
            top,
            str(xdc_path),
            str(output_dir),
            str(expected_cells),
            place_directive,
            route_directive,
        ],
        output_dir,
        "vivado-implementation.log",
    )
    metrics_path = output_dir / "implementation_metrics.tsv"
    metrics = _read_metrics(metrics_path)
    if metrics.get("part") != part:
        raise ValidationError("Vivado implemented a different part")
    if _integer(metrics, "mapped_cells") != expected_cells:
        raise ValidationError("Vivado mapped-cell coverage disagrees")

    net_map = output_dir / "vivado-net-map.tsv"
    net_map_report = write_vivado_net_map(ir_path, net_map)
    timing_tsv = output_dir / "timing-paths.tsv"
    timing_export = _run_vivado(
        vivado,
        _TIMING_SCRIPT,
        [
            str(output_dir / "routed.dcp"),
            str(net_map),
            str(timing_tsv),
            str(max_timing_paths),
        ],
        output_dir,
        "vivado-timing-export.log",
    )
    timing_database = output_dir / "timing-path-database.json"
    timing_rows = [
        line
        for line in timing_tsv.read_text(encoding="utf-8").splitlines()[1:]
        if line
    ]
    if timing_rows:
        timing_database_report = import_vivado_path_database_tsv(
            timing_tsv, ir_path, timing_database
        )
        timing_database_validation = validate_sta_path_database(
            timing_database, ir_path
        )
    else:
        timing_database_report = {
            "status": "pass",
            "design": ir.value["design"]["name"],
            "paths": 0,
            "qualification": "no-register-to-register-paths",
        }
        timing_database_validation = dict(timing_database_report)

    artifact_names = (
        "synthesized.dcp",
        "placed.dcp",
        "routed.dcp",
        "route_status.rpt",
        "drc.rpt",
        "timing_summary.rpt",
        "utilization.rpt",
        "implementation_metrics.tsv",
        *(("timing-path-database.json",) if timing_rows else ()),
    )
    artifacts = {}
    for name in artifact_names:
        path = output_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValidationError(f"Vivado artifact is missing: {path}")
        artifacts[name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    infrastructure_cells = _integer(metrics, "infrastructure_cells")
    physical_cells = _integer(metrics, "physical_cells")
    result = {
        "schema": PHYSICAL_PARTITION_RESULT_SCHEMA,
        "status": "pass",
        "identity": {"backend": "vivado", "fpga": fpga, "part": part},
        "cell_accounting": {
            "original_cells": original_cells,
            "transport_cells": transport_cells,
            "routed_cells": expected_cells,
            "physical_cells": physical_cells,
            "infrastructure_cells": infrastructure_cells,
            "optimization_cells": _integer(metrics, "optimization_cells"),
        },
        "closure": {
            "unrouted_nets": _integer(metrics, "unrouted_nets"),
            "drc_violations": _integer(metrics, "drc_violations"),
        },
        "clocks": {
            "fabric_period_ns": _number(metrics, "fabric_period_ns"),
            "dut_period_ns": _number(metrics, "dut_period_ns"),
        },
        "timing": {
            "wns_ns": _number(metrics, "wns_ns"),
            "critical_path_ns": _number(metrics, "critical_path_ns"),
            "dut_wns_ns": _number(metrics, "dut_wns_ns"),
            "fabric_wns_ns": _number(metrics, "fabric_wns_ns"),
            "fabric_to_dut_wns_ns": _number(
                metrics, "fabric_to_dut_wns_ns"
            ),
            "clock_domain_delays_ns": {
                "dut": _number(metrics, "dut_delay_ns"),
                "fabric": _number(metrics, "fabric_delay_ns"),
                "cross": _number(metrics, "fabric_to_dut_delay_ns"),
                "overall": _number(metrics, "critical_path_ns"),
            },
        },
        "artifacts": artifacts,
    }
    validation = validate_physical_partition_result(
        result,
        backend="vivado",
        fpga=fpga,
        part=part,
        original_cells=original_cells,
        transport_cells=transport_cells,
    )
    report = {
        "schema": VIVADO_PARTITION_REPORT_SCHEMA,
        "status": "pass",
        "provider": "vivado-implementation-and-timing-v1",
        "tool": {"executable": vivado, "version": metrics["vivado_version"]},
        "implementation": implementation,
        "timing_export": timing_export,
        "net_map": net_map_report,
        "timing_path_database": timing_database_report,
        "timing_path_validation": timing_database_validation,
        "result": result,
        "validation": validation,
    }
    write_json(output_dir / "vivado-partition-report.json", report)
    return report
