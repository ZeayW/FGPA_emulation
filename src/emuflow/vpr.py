"""Source-built VPR orchestration and independently checked flow reports."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import EmuFlowError, ValidationError
from .io import write_json
from .native_tools import resolve_native_executable
from .route_artifact import validate_vpr_route_artifacts
from .synthesis import _yosys_identifier, _yosys_quote


VPR_REPORT_SCHEMA = "emuflow.vpr-report/v1"
VPR_PROVIDER = "vpr-root-build"
VTR_HARD_BLOCK_PROFILE = "vtr-flagship-k6-n10-40nm"
_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
_YOSYS_SCRIPT_ROOT = _RUNTIME_ROOT / "scripts" / "yosys"
_VTR_MODEL_LIBRARY = _YOSYS_SCRIPT_ROOT / "vtr_models.v"
_VTR_MULTIPLY_MAP = _YOSYS_SCRIPT_ROOT / "vtr_multiply_map.v"
_VTR_MEMORY_LIBRARY = _YOSYS_SCRIPT_ROOT / "vtr_memories.txt"
_VTR_MEMORY_MAP = _YOSYS_SCRIPT_ROOT / "vtr_memory_map.v"
_VTR_HARD_BLOCK_MODELS = frozenset(
    {"multiply", "single_port_ram", "dual_port_ram"}
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_vtr_yosys_script(
    sources: Iterable[Path],
    top: str,
    output: Path,
    *,
    hard_blocks: bool = False,
) -> str:
    """Build a VTR-compatible LUT6/DFF and optional hard-block eBLIF script.

    ``dffunmap`` is deliberately applied before and after ABC. It lowers
    enable/reset FF variants into muxes plus the generic DFF form emitted by
    ``write_blif`` as ``.latch`` rather than architecture-specific subckts.
    """

    source_list = list(sources)
    if not source_list:
        raise EmuFlowError("VTR synthesis requires at least one RTL source")
    top_identifier = _yosys_identifier(top)
    read_sources = " ".join(_yosys_quote(str(path)) for path in source_list)
    commands = [
        f"read_verilog -sv {read_sources}",
        f"hierarchy -check -top {top_identifier}",
    ]
    if hard_blocks:
        for path in (
            _VTR_MODEL_LIBRARY,
            _VTR_MULTIPLY_MAP,
            _VTR_MEMORY_LIBRARY,
            _VTR_MEMORY_MAP,
        ):
            if not path.is_file():
                raise EmuFlowError(
                    f"VTR hard-block mapping file is missing: {path}"
                )
        commands.extend(
            (
                f"synth -top {top_identifier} -run begin:fine -noalumacc",
                f"read_verilog -lib {_yosys_quote(str(_VTR_MODEL_LIBRARY))}",
                "wreduce t:$mul",
                f"techmap -map {_yosys_quote(str(_VTR_MULTIPLY_MAP))}",
                f"memory_libmap -lib {_yosys_quote(str(_VTR_MEMORY_LIBRARY))}",
                f"techmap -map {_yosys_quote(str(_VTR_MEMORY_MAP))}",
                "memory_map",
                "opt -full",
                "techmap",
            )
        )
    else:
        commands.append(f"synth -top {top_identifier} -noabc")
    commands.extend(("dffunmap", "abc -lut 6", "dffunmap", "clean", "check"))
    if hard_blocks:
        commands.extend(
            (
                "chtype -set multiply t:VTR_MULTIPLY_*",
                "chtype -set single_port_ram t:VTR_SP_BIT_*",
                "chtype -set dual_port_ram t:VTR_DP_BIT_*",
            )
        )
    commands.append(
        f"write_blif -attr -cname {_yosys_quote(str(output))}"
    )
    return "; ".join(commands)


def run_vtr_yosys(
    sources: Iterable[Path],
    top: str,
    output: Path,
    *,
    executable: Optional[str] = None,
    log_path: Optional[Path] = None,
    hard_blocks: bool = False,
) -> Dict[str, Any]:
    source_list = [path.resolve() for path in sources]
    for source in source_list:
        if not source.is_file():
            raise EmuFlowError(f"RTL source does not exist: {source}")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = resolve_native_executable("yosys", executable)
    script = build_vtr_yosys_script(
        source_list,
        top,
        output,
        hard_blocks=hard_blocks,
    )
    completed = subprocess.run(
        [command, "-p", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-30:])
        raise EmuFlowError(
            "VTR-targeted Yosys synthesis failed with exit code "
            f"{completed.returncode}\n{tail}"
        )
    if not output.is_file() or output.stat().st_size == 0:
        raise EmuFlowError(
            f"Yosys did not create the expected eBLIF: {output}"
        )
    text = output.read_text(encoding="utf-8", errors="replace")
    subcircuits = [
        line.split()[1]
        for line in text.splitlines()
        if line.startswith(".subckt ") and len(line.split()) >= 2
    ]
    unsupported = sorted(set(subcircuits) - _VTR_HARD_BLOCK_MODELS)
    if unsupported:
        raise ValidationError(
            "VTR eBLIF contains unsupported architecture subckts: "
            + ", ".join(unsupported)
        )
    if not hard_blocks and subcircuits:
        raise ValidationError(
            "logic-only VTR eBLIF contains architecture-specific subckts: "
            + ", ".join(sorted(set(subcircuits)))
        )
    report = {
        "status": "pass",
        "provider": "yosys-root-build",
        "mapping": (
            "vtr-flagship-heterogeneous" if hard_blocks else "logic-only"
        ),
        "mapping_profile": VTR_HARD_BLOCK_PROFILE if hard_blocks else None,
        "top": top,
        "output": str(output),
        "sha256": _sha256(output),
        "lut_functions": sum(
            line.startswith(".names") for line in text.splitlines()
        ),
        "latches": sum(
            line.startswith(".latch") for line in text.splitlines()
        ),
        # VTR's public memory model exposes one bit-slice atom per data bit;
        # VPR subsequently packs those atoms into one physical memory block.
        "hard_block_atoms": {
            model: subcircuits.count(model)
            for model in sorted(_VTR_HARD_BLOCK_MODELS)
        },
    }
    if hard_blocks:
        report["mapping_inputs"] = {
            path.name: _sha256(path)
            for path in (
                _VTR_MODEL_LIBRARY,
                _VTR_MULTIPLY_MAP,
                _VTR_MEMORY_LIBRARY,
                _VTR_MEMORY_MAP,
            )
        }
    return report


_INTEGER_PATTERNS = {
    "packed_nets": re.compile(r"Netlist num_nets:\s+(\d+)"),
    "packed_blocks": re.compile(r"Netlist num_blocks:\s+(\d+)"),
    "io_blocks": re.compile(r"Netlist io blocks:\s+(\d+)"),
    "clb_blocks": re.compile(r"Netlist clb blocks:\s+(\d+)"),
    "multiplier_blocks": re.compile(r"Netlist mult_36 blocks:\s+(\d+)"),
    "memory_blocks": re.compile(r"Netlist memory blocks:\s+(\d+)"),
    "wirelength": re.compile(r"Total wirelength:\s+(\d+)"),
}
_FLOAT_PATTERNS = {
    "device_utilization": re.compile(r"Device Utilization:\s+([0-9.eE+-]+)"),
    "critical_path_ns": re.compile(
        r"Final critical path delay \(least slack\):\s+([0-9.eE+-]+)\s+ns"
    ),
    "fmax_mhz": re.compile(r"Fmax:\s+([0-9.eE+-]+)\s+MHz"),
}


def validate_vpr_outputs(
    log_text: str,
    *,
    packed_netlist: Path,
    placement: Path,
    route: Path,
    stages: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if "VPR succeeded" not in log_text:
        raise ValidationError("VPR log does not contain a success marker")
    artifacts = {
        "packed_netlist": packed_netlist,
        "placement": placement,
        "route": route,
    }
    artifact_report: Dict[str, Dict[str, Any]] = {}
    for name, path in artifacts.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValidationError(f"VPR {name} artifact is missing: {path}")
        artifact_report[name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    metrics: Dict[str, Any] = {}
    for name, pattern in _INTEGER_PATTERNS.items():
        matches = pattern.findall(log_text)
        if matches:
            metrics[name] = int(matches[-1])
    for name, pattern in _FLOAT_PATTERNS.items():
        matches = pattern.findall(log_text)
        if matches:
            metrics[name] = float(matches[-1])
    for required in ("packed_nets", "packed_blocks", "wirelength"):
        if required not in metrics:
            raise ValidationError(
                f"VPR log is missing required metric {required!r}"
            )
    return {
        "status": "pass",
        "provider": VPR_PROVIDER,
        "stages": list(stages or ("pack", "place", "route", "analysis")),
        "metrics": metrics,
        "artifacts": artifact_report,
    }


def run_vpr(
    architecture: Path,
    circuit: Path,
    output_dir: Path,
    *,
    executable: Optional[str] = None,
    seed: int = 1,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    architecture = architecture.resolve()
    circuit = circuit.resolve()
    if not architecture.is_file():
        raise EmuFlowError(f"VTR architecture does not exist: {architecture}")
    if not circuit.is_file():
        raise EmuFlowError(f"eBLIF circuit does not exist: {circuit}")
    if seed < 0:
        raise EmuFlowError("VPR seed must be non-negative")
    if route_channel_width <= 0 or route_channel_width % 2:
        raise EmuFlowError(
            "VPR route channel width must be a positive even integer"
        )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    command = resolve_native_executable("vpr", executable)
    arguments = [
        command,
        str(architecture),
        str(circuit),
        "--disp",
        "off",
        "--seed",
        str(seed),
        "--route_chan_width",
        str(route_channel_width),
    ]
    completed = subprocess.run(
        arguments,
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path = output_dir / "vpr.console.log"
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-40:])
        raise EmuFlowError(
            f"VPR failed with exit code {completed.returncode}\n{tail}"
        )

    stem = circuit.stem
    report = validate_vpr_outputs(
        completed.stdout,
        packed_netlist=output_dir / f"{stem}.net",
        placement=output_dir / f"{stem}.place",
        route=output_dir / f"{stem}.route",
    )
    report.update(
        {
            "architecture": {
                "path": str(architecture),
                "sha256": _sha256(architecture),
            },
            "circuit": {
                "path": str(circuit),
                "sha256": _sha256(circuit),
            },
            "configuration": {
                "seed": seed,
                "route_channel_width": route_channel_width,
            },
            "command": arguments,
            "log": str(log_path),
        }
    )
    write_json(output_dir / "vpr-report.json", report)
    return report


def run_vpr_route_packed(
    architecture: Path,
    circuit: Path,
    packed_netlist: Path,
    packed_contract: Path,
    placement: Path,
    output_dir: Path,
    *,
    executable: Optional[str] = None,
    route_checker: Optional[str] = None,
    route_channel_width: int = 300,
) -> Dict[str, Any]:
    """Route an existing VPR packing and OpenPARF cluster placement."""

    inputs = {
        "architecture": architecture.resolve(),
        "circuit": circuit.resolve(),
        "packed_netlist": packed_netlist.resolve(),
        "packed_contract": packed_contract.resolve(),
        "placement": placement.resolve(),
    }
    for name, path in inputs.items():
        if not path.is_file():
            raise EmuFlowError(f"VPR {name} does not exist: {path}")
    if route_channel_width <= 0 or route_channel_width % 2:
        raise EmuFlowError(
            "VPR route channel width must be a positive even integer"
        )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    route = output_dir / f"{inputs['circuit'].stem}.route"
    rr_graph = output_dir / "rr_graph.xml"
    command = resolve_native_executable("vpr", executable)
    arguments = [
        command,
        str(inputs["architecture"]),
        str(inputs["circuit"]),
        "--route",
        "--analysis",
        "--disp",
        "off",
        "--net_file",
        str(inputs["packed_netlist"]),
        "--place_file",
        str(inputs["placement"]),
        "--route_file",
        str(route),
        "--write_rr_graph",
        str(rr_graph),
        "--route_chan_width",
        str(route_channel_width),
    ]
    completed = subprocess.run(
        arguments,
        cwd=output_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path = output_dir / "vpr.console.log"
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-40:])
        raise EmuFlowError(
            f"VPR routing failed with exit code {completed.returncode}\n{tail}"
        )
    report = validate_vpr_outputs(
        completed.stdout,
        packed_netlist=inputs["packed_netlist"],
        placement=inputs["placement"],
        route=route,
        stages=("route", "analysis"),
    )
    route_check = validate_vpr_route_artifacts(
        route,
        rr_graph,
        inputs["packed_contract"],
        inputs["placement"],
        output_dir / "vpr-route-check.json",
        executable=route_checker,
    )
    report.update(
        {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in inputs.items()
        }
    )
    report["configuration"] = {
        "route_channel_width": route_channel_width
    }
    report["command"] = arguments
    report["log"] = str(log_path)
    report["route_check"] = route_check
    write_json(output_dir / "vpr-route-report.json", report)
    return report
