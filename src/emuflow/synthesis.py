import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from .errors import EmuFlowError


VALID_XILINX_FAMILIES = {"xcup", "xcu", "xc7"}
YOSYS_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _yosys_quote(value: str) -> str:
    # Yosys accepts double-quoted strings with JSON-compatible escaping.
    return json.dumps(value)


def _yosys_identifier(value: str) -> str:
    if not YOSYS_IDENTIFIER.fullmatch(value):
        raise EmuFlowError(
            f"unsupported Yosys identifier {value!r}; "
            "expected a simple Verilog module name"
        )
    return value


def build_yosys_script(
    sources: Iterable[Path],
    top: str,
    output: Path,
    family: str = "xcup",
) -> str:
    source_list = list(sources)
    if not source_list:
        raise EmuFlowError("synthesis requires at least one RTL source")
    if family not in VALID_XILINX_FAMILIES:
        raise EmuFlowError(
            f"unsupported Xilinx family {family!r}; "
            f"expected one of {sorted(VALID_XILINX_FAMILIES)}"
        )
    top_identifier = _yosys_identifier(top)

    read_sources = " ".join(_yosys_quote(str(path)) for path in source_list)
    return "; ".join(
        (
            f"read_verilog -sv {read_sources}",
            f"hierarchy -check -top {top_identifier}",
            (
                f"synth_xilinx -family {family} -top {top_identifier} "
                "-noiopad -noclkbuf"
            ),
            f"write_json {_yosys_quote(str(output))}",
        )
    )


def run_yosys(
    sources: Iterable[Path],
    top: str,
    output: Path,
    family: str = "xcup",
    executable: Optional[str] = None,
    log_path: Optional[Path] = None,
) -> None:
    source_list = list(sources)
    for source in source_list:
        if not source.is_file():
            raise EmuFlowError(f"RTL source does not exist: {source}")

    command = executable or shutil.which("yosys")
    if not command:
        raise EmuFlowError(
            "Yosys executable was not found; install Yosys or pass --yosys PATH"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    script = build_yosys_script(source_list, top, output, family)
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
        tail = "\n".join(completed.stdout.splitlines()[-20:])
        raise EmuFlowError(
            f"Yosys synthesis failed with exit code {completed.returncode}\n{tail}"
        )
    if not output.is_file():
        raise EmuFlowError(
            f"Yosys reported success but did not create expected output: {output}"
        )
