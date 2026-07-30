"""OpenSTA-backed, partition-independent FPGA timing-path extraction."""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .errors import EmuFlowError, ValidationError
from .io import read_json
from .ir import EmuIR
from .native_tools import resolve_native_executable
from .sta import (
    import_sta_path_database_tsv,
    validate_sta_path_database,
    write_emuir_net_map,
)
from .verilog import mapped_verilog


FPGA_TIMING_MODEL_SCHEMA = "emuflow.fpga-timing-model/v1"
OPENSTA_PROVIDER = "opensta-fpga-path-database-v1"


def _runtime_data_path(relative: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / relative,
        root / "share" / "emuflow" / relative,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


DEFAULT_TIMING_MODEL = _runtime_data_path(
    Path("resources/timing/ultrascaleplus-softlogic-v1.json")
)
OPENSTA_EXPORT_SCRIPT = _runtime_data_path(
    Path("scripts/opensta/export_timing_path_database.tcl")
)


def _finite_nonnegative(value: Any, context: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValidationError(f"{context}: expected a non-negative number")
    return float(value)


def load_timing_model(path: Path) -> Dict[str, Any]:
    value = read_json(path)
    if value.get("schema") != FPGA_TIMING_MODEL_SCHEMA:
        raise ValidationError(
            f"timing_model.schema: expected {FPGA_TIMING_MODEL_SCHEMA!r}"
        )
    for key in ("name", "family"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValidationError(
                f"timing_model.{key}: expected a non-empty string"
            )
    source = value.get("source")
    if not isinstance(source, dict):
        raise ValidationError("timing_model.source: expected an object")
    if source.get("qualification") not in {
        "analytical_uncharacterized",
        "calibrated",
        "characterized",
    }:
        raise ValidationError(
            "timing_model.source.qualification: unsupported value"
        )
    cells = value.get("cells")
    if not isinstance(cells, dict) or not cells:
        raise ValidationError("timing_model.cells: expected a non-empty object")
    for cell_name, raw_cell in sorted(cells.items()):
        context = f"timing_model.cells[{cell_name!r}]"
        if not isinstance(cell_name, str) or not cell_name:
            raise ValidationError("timing_model.cells: invalid cell name")
        if not isinstance(raw_cell, dict):
            raise ValidationError(f"{context}: expected an object")
        kind = raw_cell.get("kind")
        if kind == "combinational":
            inputs = raw_cell.get("inputs")
            output = raw_cell.get("output")
            if (
                not isinstance(inputs, list)
                or not inputs
                or not all(isinstance(pin, str) and pin for pin in inputs)
                or len(inputs) != len(set(inputs))
                or not isinstance(output, str)
                or not output
                or output in inputs
            ):
                raise ValidationError(f"{context}: invalid pin definition")
            _finite_nonnegative(raw_cell.get("delay_ns"), f"{context}.delay_ns")
        elif kind == "rising_edge_ff":
            pins = [
                raw_cell.get("clock"),
                raw_cell.get("data"),
                raw_cell.get("output"),
            ]
            controls = raw_cell.get("controls")
            if (
                not all(isinstance(pin, str) and pin for pin in pins)
                or len(pins) != len(set(pins))
                or not isinstance(controls, list)
                or not all(
                    isinstance(pin, str) and pin for pin in controls
                )
                or len(controls) != len(set(controls))
                or set(pins) & set(controls)
            ):
                raise ValidationError(f"{context}: invalid pin definition")
            _finite_nonnegative(
                raw_cell.get("setup_ns"), f"{context}.setup_ns"
            )
            _finite_nonnegative(
                raw_cell.get("clock_to_q_ns"),
                f"{context}.clock_to_q_ns",
            )
        elif kind == "constant":
            if (
                not isinstance(raw_cell.get("output"), str)
                or not raw_cell["output"]
            ):
                raise ValidationError(f"{context}: invalid output pin")
        else:
            raise ValidationError(f"{context}.kind: unsupported value {kind!r}")
    return value


def _scalar_table(name: str, value: float, indent: str) -> list[str]:
    return [
        f"{indent}{name} (scalar) {{",
        f'{indent}  values ("{value:.12g}");',
        f"{indent}}}",
    ]


def render_opensta_liberty(model: Mapping[str, Any]) -> str:
    """Render the validated open timing model into deterministic Liberty."""
    name = str(model["name"]).replace("-", "_")
    lines = [
        f"library ({name}) {{",
        '  delay_model : "table_lookup";',
        '  time_unit : "1ns";',
        '  voltage_unit : "1V";',
        '  current_unit : "1mA";',
        '  leakage_power_unit : "1nW";',
        '  pulling_resistance_unit : "1kohm";',
        "  capacitive_load_unit (1,pF);",
        "  input_threshold_pct_rise : 50;",
        "  input_threshold_pct_fall : 50;",
        "  output_threshold_pct_rise : 50;",
        "  output_threshold_pct_fall : 50;",
        "  slew_lower_threshold_pct_rise : 20;",
        "  slew_lower_threshold_pct_fall : 20;",
        "  slew_upper_threshold_pct_rise : 80;",
        "  slew_upper_threshold_pct_fall : 80;",
        "",
    ]
    for cell_name, cell in sorted(model["cells"].items()):
        lines.extend([f"  cell ({cell_name}) {{", "    area : 1.0;"])
        kind = cell["kind"]
        if kind == "combinational":
            for pin in cell["inputs"]:
                lines.extend(
                    [
                        f"    pin ({pin}) {{",
                        "      direction : input;",
                        "      capacitance : 0.001;",
                        "    }",
                    ]
                )
            lines.extend(
                [
                    f"    pin ({cell['output']}) {{",
                    "      direction : output;",
                ]
            )
            for pin in cell["inputs"]:
                lines.extend(
                    [
                        "      timing () {",
                        f'        related_pin : "{pin}";',
                        "        timing_sense : non_unate;",
                        *_scalar_table(
                            "cell_rise", float(cell["delay_ns"]), "        "
                        ),
                        *_scalar_table(
                            "cell_fall", float(cell["delay_ns"]), "        "
                        ),
                        *_scalar_table(
                            "rise_transition", 0.01, "        "
                        ),
                        *_scalar_table(
                            "fall_transition", 0.01, "        "
                        ),
                        "      }",
                    ]
                )
            lines.append("    }")
        elif kind == "rising_edge_ff":
            lines.extend(
                [
                    "    ff (IQ, IQN) {",
                    f'      clocked_on : "{cell["clock"]}";',
                    f'      next_state : "{cell["data"]}";',
                    "    }",
                    f"    pin ({cell['clock']}) {{",
                    "      direction : input;",
                    "      clock : true;",
                    "      capacitance : 0.001;",
                    "    }",
                    f"    pin ({cell['data']}) {{",
                    "      direction : input;",
                    "      capacitance : 0.001;",
                    "      timing () {",
                    f'        related_pin : "{cell["clock"]}";',
                    "        timing_type : setup_rising;",
                    *_scalar_table(
                        "rise_constraint",
                        float(cell["setup_ns"]),
                        "        ",
                    ),
                    *_scalar_table(
                        "fall_constraint",
                        float(cell["setup_ns"]),
                        "        ",
                    ),
                    "      }",
                    "    }",
                ]
            )
            for pin in cell["controls"]:
                lines.extend(
                    [
                        f"    pin ({pin}) {{",
                        "      direction : input;",
                        "      capacitance : 0.001;",
                        "    }",
                    ]
                )
            lines.extend(
                [
                    f"    pin ({cell['output']}) {{",
                    "      direction : output;",
                    '      function : "IQ";',
                    "      timing () {",
                    f'        related_pin : "{cell["clock"]}";',
                    "        timing_type : rising_edge;",
                    "        timing_sense : non_unate;",
                    *_scalar_table(
                        "cell_rise",
                        float(cell["clock_to_q_ns"]),
                        "        ",
                    ),
                    *_scalar_table(
                        "cell_fall",
                        float(cell["clock_to_q_ns"]),
                        "        ",
                    ),
                    *_scalar_table(
                        "rise_transition", 0.01, "        "
                    ),
                    *_scalar_table(
                        "fall_transition", 0.01, "        "
                    ),
                    "      }",
                    "    }",
                ]
            )
        else:
            lines.extend(
                [
                    f"    pin ({cell['output']}) {{",
                    "      direction : output;",
                    "    }",
                ]
            )
        lines.extend(["  }", ""])
    lines.extend(["}", ""])
    return "\n".join(lines)


def validate_timing_model_coverage(
    ir: EmuIR,
    model: Mapping[str, Any],
) -> Dict[str, Any]:
    used = sorted({instance["type"] for instance in ir.value["instances"]})
    supported = set(model["cells"])
    unsupported = sorted(set(used) - supported)
    if unsupported:
        raise ValidationError(
            "OpenSTA timing model does not cover mapped primitives: "
            f"{unsupported}"
        )
    return {
        "status": "pass",
        "used_cell_types": used,
        "model_cell_types": sorted(supported),
    }


def _clock_map(
    ir: EmuIR,
    clocks: Optional[Mapping[str, float]],
) -> Dict[str, float]:
    available = {clock["id"]: clock for clock in ir.value["clocks"]}
    if clocks is None:
        result = {
            clock_id: float(clock["period_ns"])
            for clock_id, clock in available.items()
            if isinstance(clock.get("period_ns"), (int, float))
            and not isinstance(clock.get("period_ns"), bool)
        }
    else:
        result = dict(clocks)
    if not result:
        raise ValidationError(
            "OpenSTA requires at least one CLOCK=PERIOD_NS definition"
        )
    unknown = sorted(set(result) - set(available))
    if unknown:
        raise ValidationError(f"OpenSTA clocks are absent from EmuIR: {unknown}")
    for name, period in result.items():
        if (
            isinstance(period, bool)
            or not isinstance(period, (int, float))
            or not math.isfinite(float(period))
            or float(period) <= 0.0
        ):
            raise ValidationError(
                f"OpenSTA clock {name!r} period must be positive"
            )
    return {name: float(result[name]) for name in sorted(result)}


def parse_clock_definitions(values: Iterable[str]) -> Dict[str, float]:
    clocks: Dict[str, float] = {}
    for value in values:
        name, separator, raw_period = value.partition("=")
        if not separator or not name or not raw_period:
            raise ValidationError(
                f"--clock-period: expected CLOCK=PERIOD_NS, got {value!r}"
            )
        if name in clocks:
            raise ValidationError(
                f"--clock-period: duplicate clock {name!r}"
            )
        try:
            clocks[name] = float(raw_period)
        except ValueError as error:
            raise ValidationError(
                f"--clock-period: invalid period in {value!r}"
            ) from error
    return clocks


def run_opensta_path_database(
    ir_path: Path,
    output_path: Path,
    clocks: Optional[Mapping[str, float]] = None,
    timing_model_path: Path = DEFAULT_TIMING_MODEL,
    executable: Optional[str] = None,
    max_paths: int = 10000,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if max_paths <= 0:
        raise ValidationError("OpenSTA max_paths must be positive")
    ir = EmuIR.load(ir_path)
    model = load_timing_model(timing_model_path)
    coverage = validate_timing_model_coverage(ir, model)
    clock_map = _clock_map(ir, clocks)
    opensta = resolve_native_executable("sta", executable)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="emuflow-opensta-") as temporary:
        root = Path(temporary)
        verilog_path = root / "mapped.v"
        liberty_path = root / "timing.lib"
        net_map_path = root / "net-map.tsv"
        clock_path = root / "clocks.tsv"
        raw_path = root / "paths.tsv"
        # OpenSTA's intentionally small Verilog reader does not accept net
        # declaration keywords on ports, attributes, or instance parameters.
        # Those constructs do not affect static timing connectivity.
        verilog_path.write_text(
            mapped_verilog(ir, timing_only=True), encoding="utf-8"
        )
        liberty_path.write_text(
            render_opensta_liberty(model), encoding="utf-8"
        )
        write_emuir_net_map(ir_path, net_map_path)
        with clock_path.open("w", encoding="utf-8") as stream:
            stream.write("clock_hex\tperiod_ns\n")
            for name, period in clock_map.items():
                stream.write(f"{name.encode().hex()}\t{period:.12g}\n")

        environment = os.environ.copy()
        environment.update(
            {
                "EMUFLOW_STA_LIBERTY": str(liberty_path),
                "EMUFLOW_STA_VERILOG": str(verilog_path),
                "EMUFLOW_STA_TOP": ir.value["design"]["top"],
                "EMUFLOW_STA_NET_MAP": str(net_map_path),
                "EMUFLOW_STA_CLOCKS": str(clock_path),
                "EMUFLOW_STA_OUTPUT": str(raw_path),
                "EMUFLOW_STA_MAX_PATHS": str(max_paths),
            }
        )
        completed = subprocess.run(
            [opensta, "-exit", str(OPENSTA_EXPORT_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            env=environment,
        )
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(completed.stdout, encoding="utf-8")
        if completed.returncode != 0:
            tail = "\n".join(completed.stdout.splitlines()[-30:])
            raise EmuFlowError(
                "OpenSTA path extraction failed with exit code "
                f"{completed.returncode}\n{tail}"
            )
        if not raw_path.is_file():
            raise EmuFlowError(
                "OpenSTA reported success but did not create its path TSV"
            )
        imported = import_sta_path_database_tsv(
            raw_path,
            ir_path,
            output_path,
            provider=OPENSTA_PROVIDER,
            source={
                "timing_model": model["name"],
                "timing_model_qualification": model["source"][
                    "qualification"
                ],
            },
        )

    checked = validate_sta_path_database(output_path, ir_path)
    return {
        "status": "pass",
        "design": ir.value["design"]["name"],
        "provider": OPENSTA_PROVIDER,
        "timing_model": model["name"],
        "timing_model_qualification": model["source"]["qualification"],
        "clocks": clock_map,
        "paths": imported["paths"],
        "unique_path_nets": imported["unique_path_nets"],
        "used_cell_types": coverage["used_cell_types"],
        "checker": checked,
        "output": str(output_path),
        "log": str(log_path) if log_path is not None else None,
    }
