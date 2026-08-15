"""Source-built VPR orchestration and independently checked flow reports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
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
    json_output: Optional[Path] = None,
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
                (
                    f"synth -top {top_identifier} -run begin:fine "
                    "-noalumacc -flatten"
                ),
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
    commands.extend(
        (
            "dffunmap",
            "abc -lut 6",
            "dffunmap",
            "delete t:$scopeinfo",
            "clean",
            "check",
        )
    )
    if hard_blocks:
        commands.extend(
            (
                "chtype -set multiply t:VTR_MULTIPLY_*",
                "chtype -set single_port_ram t:VTR_SP_BIT_*",
                "chtype -set dual_port_ram t:VTR_DP_BIT_*",
            )
        )
    if json_output is not None:
        commands.append(
            f"write_json {_yosys_quote(str(json_output))}"
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
    json_output: Optional[Path] = None,
) -> Dict[str, Any]:
    source_list = [path.resolve() for path in sources]
    for source in source_list:
        if not source.is_file():
            raise EmuFlowError(f"RTL source does not exist: {source}")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if json_output is not None:
        json_output = json_output.resolve()
        json_output.parent.mkdir(parents=True, exist_ok=True)
    command = resolve_native_executable("yosys", executable)
    script = build_vtr_yosys_script(
        source_list,
        top,
        output,
        hard_blocks=hard_blocks,
        json_output=json_output,
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
    if json_output is not None and (
        not json_output.is_file() or json_output.stat().st_size == 0
    ):
        raise EmuFlowError(
            f"Yosys did not create the expected JSON netlist: {json_output}"
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
    if json_output is not None:
        report["json_output"] = str(json_output)
        report["json_sha256"] = _sha256(json_output)
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
    "setup_wns_ns": re.compile(
        r"Final setup Worst Negative Slack \(sWNS\):\s+([0-9.eE+-]+)\s+ns"
    ),
    "setup_worst_slack_ns": re.compile(
        r"Final setup Worst Slack:\s+([0-9.eE+-]+)\s+ns"
    ),
    "setup_tns_ns": re.compile(
        r"Final setup Total Negative Slack \(sTNS\):\s+([0-9.eE+-]+)\s+ns"
    ),
}
_FAILING_ENDPOINT_PATTERN = re.compile(
    r"Final setup Failing Endpoint Constraints \(sFEC\):\s+(\d+)"
)
_FAILING_LOGICAL_ENDPOINT_PATTERN = re.compile(
    r"Final setup Failing Endpoints:\s+(\d+)"
)
_CLOCK_DOMAIN_CPD = re.compile(
    r"^\s+(\S+)\s+to\s+(\S+)\s+CPD:\s+([0-9.eE+-]+)\s+ns",
    re.MULTILINE,
)


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
    failing_endpoint_matches = _FAILING_ENDPOINT_PATTERN.findall(log_text)
    if failing_endpoint_matches:
        metrics["setup_failing_endpoint_constraints"] = int(
            failing_endpoint_matches[-1]
        )
    failing_logical_endpoint_matches = _FAILING_LOGICAL_ENDPOINT_PATTERN.findall(
        log_text
    )
    if failing_logical_endpoint_matches:
        metrics["setup_failing_endpoints"] = int(
            failing_logical_endpoint_matches[-1]
        )
    clock_domain_cpd = {
        f"{launch}->{capture}": float(delay)
        for launch, capture, delay in _CLOCK_DOMAIN_CPD.findall(log_text)
    }
    if clock_domain_cpd:
        metrics["clock_domain_cpd_ns"] = dict(sorted(clock_domain_cpd.items()))
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


def validate_vpr_timing_summary(
    summary_path: Path,
    log_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Independently bind VPR's endpoint-complete timing summary to its log."""
    if not summary_path.is_file() or summary_path.stat().st_size == 0:
        raise ValidationError(f"VPR timing summary is missing: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError("VPR timing summary is not valid JSON") from error
    expected = {
        "critical_path_ns": "cpd",
        "fmax_mhz": "fmax",
        "setup_wns_ns": "swns",
        "setup_worst_slack_ns": "worst_slack",
        "setup_tns_ns": "stns",
        "setup_failing_endpoint_constraints": "sfec",
        "setup_failing_endpoints": "failing_endpoints",
    }
    normalized: Dict[str, Any] = {}
    for metric, field in expected.items():
        value = summary.get(field)
        if metric == "setup_failing_endpoint_constraints":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"VPR timing summary field {field!r} is invalid")
            normalized[metric] = value
        else:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValidationError(f"VPR timing summary field {field!r} is invalid")
            normalized[metric] = float(value)
        log_value = log_metrics.get(metric)
        # VPR intentionally omits the single-number Fmax console line when a
        # design has multiple clock-domain pairs.  Its machine summary still
        # reports the least-slack-domain Fmax, which must equal 1000 / CPD.
        derived_fmax = metric == "fmax_mhz" and log_value is None
        if derived_fmax:
            log_value = 1000.0 / float(normalized["critical_path_ns"])
        # VPR's machine summary prints Fmax to two decimal places while CPD is
        # independently serialized at a different precision.  When the
        # single-number console Fmax is intentionally absent for a
        # multi-clock design, bind the two through the maximum rounding error
        # of the machine field instead of requiring impossible bit equality.
        abs_tolerance = 0.005 if derived_fmax else 1e-9
        if log_value is None or not math.isclose(
            float(normalized[metric]),
            float(log_value),
            rel_tol=1e-9,
            abs_tol=abs_tolerance,
        ):
            raise ValidationError(
                f"VPR timing summary field {field!r} disagrees with the console log"
            )
    if normalized["setup_wns_ns"] > 0 or normalized["setup_tns_ns"] > 0:
        raise ValidationError("VPR negative-slack metrics must be non-positive")
    if (normalized["setup_tns_ns"] < 0) != (
        normalized["setup_failing_endpoint_constraints"] > 0
    ):
        raise ValidationError("VPR TNS and failing-endpoint count disagree")
    return {
        "status": "pass",
        "path": str(summary_path),
        "bytes": summary_path.stat().st_size,
        "sha256": _sha256(summary_path),
        "metrics": normalized,
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


def run_vpr_pack_place(
    architecture: Path,
    circuit: Path,
    output_dir: Path,
    *,
    executable: Optional[str] = None,
    seed: int = 1,
    resume: bool = False,
) -> Dict[str, Any]:
    """Pack and place only, for an OpenPARF-to-VPR physical handoff.

    The placement establishes VPR's exact auto-sized device and the packing
    fixes the clusters that OpenPARF subsequently places.  Routing here would
    be discarded, so it is deliberately left to the final checked route.
    """

    architecture = architecture.resolve()
    circuit = circuit.resolve()
    for name, path in (("architecture", architecture), ("circuit", circuit)):
        if not path.is_file():
            raise EmuFlowError(f"VPR {name} does not exist: {path}")
    if seed < 0:
        raise EmuFlowError("VPR seed must be non-negative")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / "vpr-pack-place-report.json"
    if resume:
        if checkpoint.is_file() or checkpoint.is_symlink():
            return validate_vpr_pack_place_checkpoint(
                architecture,
                circuit,
                output_dir,
                seed=seed,
            )
        if any(output_dir.iterdir()):
            raise ValidationError(
                "VPR pack/place resume found a partial checkpoint without "
                "a completed report"
            )
    command = resolve_native_executable("vpr", executable)
    arguments = [
        command,
        str(architecture),
        str(circuit),
        "--pack",
        "--place",
        "--disp",
        "off",
        "--seed",
        str(seed),
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
            f"VPR pack/place failed with exit code {completed.returncode}\n{tail}"
        )
    if "VPR succeeded" not in completed.stdout:
        raise ValidationError("VPR pack/place log has no success marker")
    stem = circuit.stem
    artifacts = {}
    for name, path in (
        ("packed_netlist", output_dir / f"{stem}.net"),
        ("placement", output_dir / f"{stem}.place"),
    ):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValidationError(f"VPR {name} artifact is missing: {path}")
        artifacts[name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    metrics: Dict[str, Any] = {}
    for name, pattern in _INTEGER_PATTERNS.items():
        matches = pattern.findall(completed.stdout)
        if matches:
            metrics[name] = int(matches[-1])
    for required in ("packed_nets", "packed_blocks"):
        if required not in metrics:
            raise ValidationError(
                f"VPR pack/place log is missing metric {required!r}"
            )
    report = {
        "status": "pass",
        "provider": VPR_PROVIDER,
        "stages": ["pack", "place"],
        "metrics": metrics,
        "artifacts": artifacts,
        "architecture": {
            "path": str(architecture),
            "sha256": _sha256(architecture),
        },
        "circuit": {"path": str(circuit), "sha256": _sha256(circuit)},
        "configuration": {"seed": seed},
        "command": arguments,
        "log": str(log_path),
    }
    write_json(output_dir / "vpr-pack-place-report.json", report)
    return report


def validate_vpr_pack_place_checkpoint(
    architecture: Path,
    circuit: Path,
    output_dir: Path,
    *,
    seed: int = 1,
) -> Dict[str, Any]:
    """Independently validate and return a completed pack/place checkpoint."""

    architecture = architecture.resolve()
    circuit = circuit.resolve()
    output_dir = output_dir.resolve()
    report_path = output_dir / "vpr-pack-place-report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ValidationError("VPR pack/place checkpoint report is missing")
    report = read_json(report_path)
    if (
        report.get("status") != "pass"
        or report.get("provider") != VPR_PROVIDER
        or report.get("stages") != ["pack", "place"]
        or report.get("configuration") != {"seed": seed}
    ):
        raise ValidationError("VPR pack/place checkpoint identity is invalid")

    stored_log = report.get("log")
    if not isinstance(stored_log, str):
        raise ValidationError("VPR pack/place checkpoint log is missing")
    stored_output_dir = Path(stored_log)
    if not stored_output_dir.is_absolute():
        raise ValidationError("VPR pack/place checkpoint paths are invalid")
    stored_output_dir = stored_output_dir.parent
    stored_physical_root = stored_output_dir.parent.parent
    current_physical_root = output_dir.parent.parent

    def _path_matches(stored_value: Any, expected: Path) -> bool:
        if not isinstance(stored_value, str):
            return False
        stored = Path(stored_value)
        if stored == expected:
            return True
        # A failed cache attempt is atomically moved from staging/ to failures/.
        # Accept only that root relocation: the old absolute path must be gone,
        # and the path below the physical-flow root must remain identical.
        if not stored.is_absolute() or stored.exists():
            return False
        try:
            stored_relative = stored.relative_to(stored_physical_root)
            expected_relative = expected.relative_to(current_physical_root)
        except ValueError:
            return False
        return stored_relative == expected_relative
    for label, expected in (
        ("architecture", architecture),
        ("circuit", circuit),
    ):
        binding = report.get(label)
        if (
            not isinstance(binding, dict)
            or not _path_matches(binding.get("path"), expected)
            or binding.get("sha256") != _sha256(expected)
        ):
            raise ValidationError(
                f"VPR pack/place checkpoint {label} binding disagrees"
            )
    stem = circuit.stem
    expected_artifacts = {
        "packed_netlist": output_dir / f"{stem}.net",
        "placement": output_dir / f"{stem}.place",
    }
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(
        expected_artifacts
    ):
        raise ValidationError("VPR pack/place checkpoint artifacts are incomplete")
    for label, expected in expected_artifacts.items():
        binding = artifacts[label]
        if (
            not isinstance(binding, dict)
            or set(binding) != {"path", "bytes", "sha256"}
            or not _path_matches(binding.get("path"), expected)
            or expected.is_symlink()
            or not expected.is_file()
            or binding.get("bytes") != expected.stat().st_size
            or binding.get("sha256") != _sha256(expected)
        ):
            raise ValidationError(
                f"VPR pack/place checkpoint {label} seal disagrees"
            )
    log_path = output_dir / "vpr.console.log"
    if (
        not _path_matches(report.get("log"), log_path)
        or log_path.is_symlink()
        or not log_path.is_file()
    ):
        raise ValidationError("VPR pack/place checkpoint log is missing")
    log = log_path.read_text(encoding="utf-8")
    if "VPR succeeded" not in log:
        raise ValidationError("VPR pack/place checkpoint log has no success marker")
    expected_metrics: Dict[str, Any] = {}
    for name, pattern in _INTEGER_PATTERNS.items():
        matches = pattern.findall(log)
        if matches:
            expected_metrics[name] = int(matches[-1])
    if (
        not {"packed_nets", "packed_blocks"}.issubset(expected_metrics)
        or report.get("metrics") != expected_metrics
    ):
        raise ValidationError("VPR pack/place checkpoint metrics disagree")
    # The sealed checkpoint may have been atomically relocated from a failed
    # cache staging tree.  Keep the on-disk report immutable, but return a
    # runtime view whose consumable paths point at the independently validated
    # current tree.  Returning the stale sealed paths would make the validator
    # pass and then fail immediately in the next physical-flow stage.
    validated = json.loads(json.dumps(report))
    validated["architecture"]["path"] = str(architecture)
    validated["circuit"]["path"] = str(circuit)
    validated["log"] = str(log_path)
    for label, expected in expected_artifacts.items():
        validated["artifacts"][label]["path"] = str(expected)
    return validated


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
    boundary_query: Optional[Path] = None,
    boundary_output: Optional[Path] = None,
    logic_query: Optional[Path] = None,
    logic_output: Optional[Path] = None,
    local_path_query: Optional[Path] = None,
    local_path_output: Optional[Path] = None,
    retain_rr_graph: bool = False,
    sdc_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Route an existing VPR packing and OpenPARF cluster placement."""

    inputs = {
        "architecture": architecture.resolve(),
        "circuit": circuit.resolve(),
        "packed_netlist": packed_netlist.resolve(),
        "packed_contract": packed_contract.resolve(),
        "placement": placement.resolve(),
    }
    if sdc_file is not None:
        inputs["sdc_file"] = sdc_file.resolve()
    for name, path in inputs.items():
        if not path.is_file():
            raise EmuFlowError(f"VPR {name} does not exist: {path}")
    if route_channel_width <= 0 or route_channel_width % 2:
        raise EmuFlowError(
            "VPR route channel width must be a positive even integer"
        )
    if (boundary_query is None) != (boundary_output is None):
        raise EmuFlowError(
            "VPR boundary timing requires both query and output paths"
        )
    boundary_query_path = None
    boundary_output_path = None
    if boundary_query is not None and boundary_output is not None:
        boundary_query_path = boundary_query.resolve()
        if not boundary_query_path.is_file():
            raise EmuFlowError(
                f"VPR boundary timing query does not exist: {boundary_query_path}"
            )
        boundary_output_path = boundary_output.resolve()
        boundary_output_path.parent.mkdir(parents=True, exist_ok=True)
    if (logic_query is None) != (logic_output is None):
        raise EmuFlowError(
            "VPR logic segment timing requires both query and output paths"
        )
    logic_query_path = None
    logic_output_path = None
    if logic_query is not None and logic_output is not None:
        logic_query_path = logic_query.resolve()
        if not logic_query_path.is_file():
            raise EmuFlowError(
                f"VPR logic segment query does not exist: {logic_query_path}"
            )
        logic_output_path = logic_output.resolve()
        logic_output_path.parent.mkdir(parents=True, exist_ok=True)
    if (local_path_query is None) != (local_path_output is None):
        raise EmuFlowError(
            "VPR local path timing requires both query and output paths"
        )
    local_path_query_path = None
    local_path_output_path = None
    if local_path_query is not None and local_path_output is not None:
        local_path_query_path = local_path_query.resolve()
        if not local_path_query_path.is_file():
            raise EmuFlowError(
                "VPR local path timing query does not exist: "
                f"{local_path_query_path}"
            )
        local_path_output_path = local_path_output.resolve()
        local_path_output_path.parent.mkdir(parents=True, exist_ok=True)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    route = output_dir / f"{inputs['circuit'].stem}.route"
    rr_graph = output_dir / "rr_graph.xml"
    timing_summary = output_dir / "timing-summary.json"
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
        "--write_timing_summary",
        str(timing_summary),
    ]
    if sdc_file is not None:
        arguments.extend(("--sdc_file", str(inputs["sdc_file"])))
    environment = os.environ.copy()
    if boundary_query_path is not None and boundary_output_path is not None:
        environment["EMUFLOW_VPR_BOUNDARY_QUERY"] = str(boundary_query_path)
        environment["EMUFLOW_VPR_BOUNDARY_OUTPUT"] = str(boundary_output_path)
    if logic_query_path is not None and logic_output_path is not None:
        environment["EMUFLOW_VPR_LOGIC_QUERY"] = str(logic_query_path)
        environment["EMUFLOW_VPR_LOGIC_OUTPUT"] = str(logic_output_path)
    if (
        local_path_query_path is not None
        and local_path_output_path is not None
    ):
        environment["EMUFLOW_VPR_LOCAL_PATH_QUERY"] = str(
            local_path_query_path
        )
        environment["EMUFLOW_VPR_LOCAL_PATH_OUTPUT"] = str(
            local_path_output_path
        )
    completed = subprocess.run(
        arguments,
        cwd=output_dir,
        env=environment,
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
    timing_validation = validate_vpr_timing_summary(
        timing_summary, report["metrics"]
    )
    report["metrics"].update(timing_validation["metrics"])
    route_check = validate_vpr_route_artifacts(
        route,
        rr_graph,
        inputs["packed_contract"],
        inputs["placement"],
        output_dir / "vpr-route-check.json",
        executable=route_checker,
    )
    boundary_artifact = None
    if boundary_output_path is not None:
        if (
            not boundary_output_path.is_file()
            or boundary_output_path.stat().st_size == 0
        ):
            raise ValidationError(
                "VPR did not emit the requested boundary timing report"
            )
        boundary_artifact = {
            "query": {
                "path": str(boundary_query_path),
                "sha256": _sha256(boundary_query_path),
            },
            "output": {
                "path": str(boundary_output_path),
                "sha256": _sha256(boundary_output_path),
            },
        }
    logic_artifact = None
    if logic_output_path is not None:
        if not logic_output_path.is_file() or logic_output_path.stat().st_size == 0:
            raise ValidationError(
                "VPR did not emit the requested logic segment timing report"
            )
        logic_artifact = {
            "query": {
                "path": str(logic_query_path),
                "sha256": _sha256(logic_query_path),
            },
            "output": {
                "path": str(logic_output_path),
                "sha256": _sha256(logic_output_path),
            },
        }
    local_path_artifact = None
    if local_path_output_path is not None:
        if (
            not local_path_output_path.is_file()
            or local_path_output_path.stat().st_size == 0
        ):
            raise ValidationError(
                "VPR did not emit the requested local path timing report"
            )
        local_path_artifact = {
            "query": {
                "path": str(local_path_query_path),
                "sha256": _sha256(local_path_query_path),
            },
            "output": {
                "path": str(local_path_output_path),
                "sha256": _sha256(local_path_output_path),
            },
        }
    report.update(
        {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in inputs.items()
        }
    )
    report["configuration"] = {
        "route_channel_width": route_channel_width,
        "retain_rr_graph": retain_rr_graph,
    }
    report["command"] = arguments
    report["log"] = str(log_path)
    report["route_check"] = route_check
    report["timing_summary"] = timing_validation
    if boundary_artifact is not None:
        report["boundary_timing"] = boundary_artifact
    if logic_artifact is not None:
        report["logic_segment_timing"] = logic_artifact
    if local_path_artifact is not None:
        report["local_path_timing"] = local_path_artifact
    rr_graph_artifact = route_check.get("artifacts", {}).get("rr_graph")
    if isinstance(rr_graph_artifact, dict):
        rr_graph_artifact["retained"] = retain_rr_graph
    write_json(output_dir / "vpr-route-report.json", report)
    if not retain_rr_graph:
        # The independent checker has already consumed the multi-gigabyte
        # routing-resource graph and recorded its size/hash in the report.
        # Keeping one copy per FPGA otherwise makes ordinary multi-FPGA runs
        # require tens of gigabytes of avoidable temporary storage.
        rr_graph.unlink(missing_ok=True)
    return report
