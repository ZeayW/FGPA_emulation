import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .errors import EmuFlowError
from .io import write_json
from .ir import EmuIR
from .phase1 import run_phase1
from .platform import Platform
from .synthesis import VALID_XILINX_FAMILIES, run_yosys
from .yosys import import_yosys_json


def _print_json(value: Dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emuflow",
        description="Open multi-FPGA emulation flow frontend",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    platform_parser = subparsers.add_parser("platform", help="BoardDB operations")
    platform_subparsers = platform_parser.add_subparsers(
        dest="platform_command", required=True
    )
    platform_validate = platform_subparsers.add_parser(
        "validate", help="validate and summarize a BoardDB"
    )
    platform_validate.add_argument("path", type=Path)
    platform_validate.add_argument("--normalized-out", type=Path)

    ir_parser = subparsers.add_parser("ir", help="EmuIR operations")
    ir_subparsers = ir_parser.add_subparsers(dest="ir_command", required=True)
    ir_validate = ir_subparsers.add_parser("validate", help="validate an EmuIR")
    ir_validate.add_argument("path", type=Path)
    ir_stats = ir_subparsers.add_parser("stats", help="show EmuIR statistics")
    ir_stats.add_argument("path", type=Path)

    importer = subparsers.add_parser(
        "import-yosys", help="convert Yosys JSON to EmuIR"
    )
    importer.add_argument("input", type=Path)
    importer.add_argument("--output", "-o", type=Path, required=True)
    importer.add_argument("--top")
    importer.add_argument("--clock", action="append", default=[])

    synthesis = subparsers.add_parser(
        "synth-yosys", help="synthesize RTL to mapped Xilinx Yosys JSON"
    )
    synthesis.add_argument("sources", nargs="+", type=Path)
    synthesis.add_argument("--top", required=True)
    synthesis.add_argument("--output", "-o", type=Path, required=True)
    synthesis.add_argument(
        "--family",
        choices=sorted(VALID_XILINX_FAMILIES),
        default="xcup",
    )
    synthesis.add_argument("--yosys", help="path to the Yosys executable")
    synthesis.add_argument("--log", type=Path)

    phase1 = subparsers.add_parser(
        "phase1", help="run the board-independent Phase 1 pipeline"
    )
    phase1.add_argument("--yosys-json", type=Path, required=True)
    phase1.add_argument("--platform", type=Path, required=True)
    phase1.add_argument("--out", type=Path, required=True)
    phase1.add_argument("--top")
    phase1.add_argument("--clock", action="append", default=[])
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "platform":
        platform = Platform.load(args.path)
        if args.normalized_out is not None:
            write_json(args.normalized_out, platform.to_dict())
        _print_json(platform.summary())
        return 0

    if args.command == "ir":
        ir = EmuIR.load(args.path)
        if args.ir_command == "validate":
            _print_json(
                {
                    "schema": ir.value["schema"],
                    "design": ir.value["design"]["name"],
                    "status": "valid",
                }
            )
        else:
            _print_json(ir.stats())
        return 0

    if args.command == "import-yosys":
        ir = import_yosys_json(args.input, top=args.top, clocks=args.clock)
        write_json(args.output, ir.to_dict())
        _print_json(ir.stats())
        return 0

    if args.command == "synth-yosys":
        run_yosys(
            sources=args.sources,
            top=args.top,
            output=args.output,
            family=args.family,
            executable=args.yosys,
            log_path=args.log,
        )
        _print_json(
            {
                "family": args.family,
                "output": str(args.output),
                "sources": [str(source) for source in args.sources],
                "status": "pass",
                "top": args.top,
            }
        )
        return 0

    if args.command == "phase1":
        report = run_phase1(
            yosys_json=args.yosys_json,
            platform_path=args.platform,
            output_dir=args.out,
            top=args.top,
            clocks=args.clock,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (EmuFlowError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"emuflow: error: {error}", file=sys.stderr)
        return 1
