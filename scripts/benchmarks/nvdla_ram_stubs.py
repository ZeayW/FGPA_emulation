#!/usr/bin/env python3

"""Generate FPGA-safe declarations or models for NVDLA ASIC SRAM wrappers."""

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


MODULE = re.compile(
    r"\bmodule\s+(nv_ram_[A-Za-z0-9_]+)\s*\((.*?)\)\s*;",
    re.DOTALL,
)
PARAMETER = re.compile(
    r"^\s*parameter\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);",
    re.MULTILINE,
)
PORT = re.compile(
    r"^\s*(input|output|inout)\s+"
    r"(?:(?:wire|reg|logic)\s+)?"
    r"(\[[^;\n]+\])?\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*;",
    re.MULTILINE,
)


def _parse_wrapper(path: Path) -> Tuple[str, str, List[Tuple[str, str, str]]]:
    text = path.read_text(encoding="utf-8")
    module_match = MODULE.search(text)
    if module_match is None:
        raise ValueError(f"{path}: expected one nv_ram_* module declaration")
    name = module_match.group(1)
    header_ports = [
        token.strip()
        for token in module_match.group(2).replace("\n", " ").split(",")
        if token.strip()
    ]
    parameter_match = PARAMETER.search(text)
    if parameter_match is None:
        raise ValueError(f"{path}: expected an SRAM wrapper parameter")
    parameter = (
        f"parameter {parameter_match.group(1)}="
        f"{parameter_match.group(2).strip()}"
    )
    declarations: Dict[str, Tuple[str, str, str]] = {}
    for direction, width, port_name in PORT.findall(text):
        declarations[port_name] = (direction, width.strip(), port_name)
    missing = [port_name for port_name in header_ports if port_name not in declarations]
    if missing:
        raise ValueError(f"{path}: missing port declarations for {missing}")
    return name, parameter, [declarations[port_name] for port_name in header_ports]


RWS_NAME = re.compile(r"^nv_ram_rws_(\d+)x(\d+)$")
RWS_PORTS = {
    "clk": ("input", ""),
    "ra": ("input", None),
    "re": ("input", ""),
    "dout": ("output", None),
    "wa": ("input", None),
    "we": ("input", ""),
    "di": ("input", None),
    "pwrbus_ram_pd": ("input", "[31:0]"),
}


def _emit_register_model(
    blocks: List[str],
    name: str,
    parameter: str,
    ports: List[Tuple[str, str, str]],
) -> None:
    match = RWS_NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"{name}: register model requires nv_ram_rws_DEPTHxWIDTH")
    depth = int(match.group(1))
    data_width = int(match.group(2))
    address_width = max(1, (depth - 1).bit_length())
    actual_ports = {
        port_name: (direction, width)
        for direction, width, port_name in ports
    }
    if set(actual_ports) != set(RWS_PORTS):
        raise ValueError(f"{name}: unsupported register-model port set")
    expected_widths = {
        **RWS_PORTS,
        "ra": ("input", f"[{address_width - 1}:0]"),
        "wa": ("input", f"[{address_width - 1}:0]"),
        "dout": ("output", f"[{data_width - 1}:0]"),
        "di": ("input", f"[{data_width - 1}:0]"),
    }
    for port_name, expected in expected_widths.items():
        if actual_ports[port_name] != expected:
            raise ValueError(
                f"{name}: {port_name} is {actual_ports[port_name]}, "
                f"expected {expected}"
            )

    blocks.append(f"module {name} #({parameter}) (")
    for index, (direction, width, port_name) in enumerate(ports):
        comma = "," if index + 1 < len(ports) else ""
        width_text = f" {width}" if width else ""
        blocks.append(f"  {direction}{width_text} {port_name}{comma}")
    blocks.extend(
        [
            " );",
            '  (* ram_style = "registers" *)',
            f"  reg [{data_width - 1}:0] mem [0:{depth - 1}];",
            f"  reg [{data_width - 1}:0] dout_reg;",
            "  always @(posedge clk) begin",
            "    if (we)",
            "      mem[wa] <= di;",
            "    if (re)",
            "      dout_reg <= mem[ra];",
            "  end",
            "  assign dout = dout_reg;",
            "  wire _unused_pwrbus = ^pwrbus_ram_pd;",
            "endmodule",
            "",
        ]
    )


def generate(
    source_dir: Path,
    output: Path,
    register_model_pattern: Optional[str] = None,
) -> Tuple[int, int]:
    wrappers = sorted(
        path
        for path in source_dir.glob("nv_ram_*.v")
        if not path.name.endswith("_logic.v")
    )
    if not wrappers:
        raise ValueError(f"{source_dir}: no NVDLA SRAM wrappers found")
    blocks = [
        "// Generated from pinned NVDLA SRAM wrapper interfaces.",
        "// Selected rws macros use synchronous-read FPGA register models.",
        "// All other ASIC SRAM wrappers remain black boxes.",
        "",
    ]
    model_pattern = (
        re.compile(register_model_pattern)
        if register_model_pattern is not None
        else None
    )
    modeled = 0
    names = set()
    for path in wrappers:
        name, parameter, ports = _parse_wrapper(path)
        if name in names:
            raise ValueError(f"{path}: duplicate wrapper module {name}")
        names.add(name)
        if model_pattern is not None and model_pattern.fullmatch(name):
            _emit_register_model(blocks, name, parameter, ports)
            modeled += 1
        else:
            blocks.append('(* black_box = "yes" *)')
            blocks.append(f"module {name} #({parameter}) (")
            for index, (direction, width, port_name) in enumerate(ports):
                comma = "," if index + 1 < len(ports) else ""
                width_text = f" {width}" if width else ""
                blocks.append(f"  {direction}{width_text} {port_name}{comma}")
            blocks.extend([" );", "endmodule", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(blocks), encoding="utf-8")
    return len(wrappers), modeled


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate black-box Verilog for NVDLA ASIC SRAM wrappers."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--register-model-pattern",
        help=(
            "full-match regular expression selecting nv_ram_rws_DEPTHxWIDTH "
            "wrappers for synchronous-read register-array modeling"
        ),
    )
    arguments = parser.parse_args()
    count, modeled = generate(
        arguments.source_dir.resolve(),
        arguments.output.resolve(),
        arguments.register_model_pattern,
    )
    print(
        f"generated_nvdla_ram_wrappers={count} "
        f"register_models={modeled} output={arguments.output.resolve()}"
    )


if __name__ == "__main__":
    main()
