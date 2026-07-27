#!/usr/bin/env python3

"""Generate FPGA-safe black-box declarations for NVDLA ASIC SRAM wrappers."""

import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple


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


def generate(source_dir: Path, output: Path) -> int:
    wrappers = sorted(
        path
        for path in source_dir.glob("nv_ram_*.v")
        if not path.name.endswith("_logic.v")
    )
    if not wrappers:
        raise ValueError(f"{source_dir}: no NVDLA SRAM wrappers found")
    blocks = [
        "// Generated from pinned NVDLA SRAM wrapper interfaces.",
        "// SRAM contents are intentionally black-boxed for FPGA scale screening.",
        "",
    ]
    names = set()
    for path in wrappers:
        name, parameter, ports = _parse_wrapper(path)
        if name in names:
            raise ValueError(f"{path}: duplicate wrapper module {name}")
        names.add(name)
        blocks.append('(* black_box = "yes" *)')
        blocks.append(f"module {name} #({parameter}) (")
        for index, (direction, width, port_name) in enumerate(ports):
            comma = "," if index + 1 < len(ports) else ""
            width_text = f" {width}" if width else ""
            blocks.append(f"  {direction}{width_text} {port_name}{comma}")
        blocks.extend([" );", "endmodule", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(blocks), encoding="utf-8")
    return len(wrappers)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate black-box Verilog for NVDLA ASIC SRAM wrappers."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    count = generate(arguments.source_dir.resolve(), arguments.output.resolve())
    print(f"generated_nvdla_ram_stubs={count} output={arguments.output.resolve()}")


if __name__ == "__main__":
    main()
