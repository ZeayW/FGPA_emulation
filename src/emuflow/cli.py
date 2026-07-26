import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .architecture import ArchitectureDB
from .benchmark import run_benchmark
from .errors import EmuFlowError
from .io import write_json
from .ir import EmuIR
from .lowering import run_placement_ir_lowering
from .phase1 import run_phase1
from .phase2 import run_phase2
from .phase3 import run_phase3, validate_phase3
from .phase4 import run_phase4, validate_phase4
from .phase5 import run_phase5, validate_phase5
from .phase6 import run_phase6, validate_phase6
from .placement import Placement
from .platform import Platform
from .synthesis import (
    VALID_SYNTHESIS_POLICIES,
    VALID_XILINX_FAMILIES,
    run_yosys,
)
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
    synthesis.add_argument(
        "--verilog-output",
        type=Path,
        help="optional flattened mapped Verilog for downstream physical tools",
    )
    synthesis.add_argument(
        "--policy",
        choices=sorted(VALID_SYNTHESIS_POLICIES),
        default="native",
    )

    benchmark = subparsers.add_parser(
        "benchmark", help="run a pinned RTL benchmark through Phase 1"
    )
    benchmark.add_argument("spec", type=Path)
    benchmark.add_argument("--source-root", type=Path, required=True)
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--yosys", help="path to the Yosys executable")

    phase1 = subparsers.add_parser(
        "phase1", help="run the board-independent Phase 1 pipeline"
    )
    phase1.add_argument("--yosys-json", type=Path, required=True)
    phase1.add_argument("--platform", type=Path, required=True)
    phase1.add_argument("--out", type=Path, required=True)
    phase1.add_argument("--top")
    phase1.add_argument("--clock", action="append", default=[])

    arch_parser = subparsers.add_parser(
        "arch", help="UltraScale+ ArchitectureDB operations"
    )
    arch_subparsers = arch_parser.add_subparsers(
        dest="arch_command", required=True
    )
    arch_validate = arch_subparsers.add_parser(
        "validate", help="validate and summarize an ArchitectureDB"
    )
    arch_validate.add_argument("path", type=Path)
    arch_import = arch_subparsers.add_parser(
        "import-vivado-tsv", help="import Vivado Site/BEL inventory TSV"
    )
    arch_import.add_argument("input", type=Path)
    arch_import.add_argument("--output", "-o", type=Path, required=True)

    placement_parser = subparsers.add_parser(
        "placement", help="physical placement operations"
    )
    placement_subparsers = placement_parser.add_subparsers(
        dest="placement_command", required=True
    )
    placement_validate = placement_subparsers.add_parser(
        "validate", help="validate a placement against ArchitectureDB and EmuIR"
    )
    placement_validate.add_argument("path", type=Path)
    placement_validate.add_argument("--arch", type=Path, required=True)
    placement_validate.add_argument("--ir", type=Path)
    placement_import = placement_subparsers.add_parser(
        "import-openparf", help="convert OpenPARF .pl to legal Site/BEL placement"
    )
    placement_import.add_argument("input", type=Path)
    placement_import.add_argument("--arch", type=Path, required=True)
    placement_import.add_argument("--ir", type=Path, required=True)
    placement_import.add_argument("--output", "-o", type=Path, required=True)
    placement_import.add_argument("--xdc", type=Path)

    phase2 = subparsers.add_parser(
        "phase2", help="run the UltraScale+ physical-backend risk spike"
    )
    phase2.add_argument("--ir", type=Path, required=True)
    phase2.add_argument("--arch", type=Path, required=True)
    phase2.add_argument("--out", type=Path, required=True)
    phase2.add_argument(
        "--openparf-result",
        type=Path,
        help="OpenPARF output .pl; omit only for deterministic adapter testing",
    )

    partition_parser = subparsers.add_parser(
        "partition", help="multi-FPGA partition artifact operations"
    )
    partition_subparsers = partition_parser.add_subparsers(
        dest="partition_command", required=True
    )
    partition_validate = partition_subparsers.add_parser(
        "validate", help="independently validate Phase 3 partition artifacts"
    )
    partition_validate.add_argument("assignment", type=Path)
    partition_validate.add_argument("--clusters", type=Path, required=True)
    partition_validate.add_argument("--ir", type=Path, required=True)
    partition_validate.add_argument("--platform", type=Path, required=True)

    phase3 = subparsers.add_parser(
        "phase3", help="run sequential clustering and multi-FPGA partitioning"
    )
    phase3.add_argument("--ir", type=Path, required=True)
    phase3.add_argument("--platform", type=Path, required=True)
    phase3.add_argument("--out", type=Path, required=True)
    phase3.add_argument("--constraints", type=Path)
    phase3.add_argument("--seed", type=int, default=0)
    phase3.add_argument("--min-used-fpgas", type=int)
    phase3.add_argument("--balance-tolerance", type=float)

    route_parser = subparsers.add_parser(
        "route", help="board-level system route artifact operations"
    )
    route_subparsers = route_parser.add_subparsers(
        dest="route_command", required=True
    )
    route_validate = route_subparsers.add_parser(
        "validate", help="independently validate Phase 4 system routes"
    )
    route_validate.add_argument("routes", type=Path)
    route_validate.add_argument("--assignment", type=Path, required=True)
    route_validate.add_argument("--platform", type=Path, required=True)

    phase4 = subparsers.add_parser(
        "phase4", help="route partition cut nets over BoardDB links"
    )
    phase4.add_argument("--assignment", type=Path, required=True)
    phase4.add_argument("--platform", type=Path, required=True)
    phase4.add_argument("--out", type=Path, required=True)
    phase4.add_argument("--constraints", type=Path)
    phase4.add_argument("--frame-slots", type=int)
    phase4.add_argument("--max-iterations", type=int)

    schedule_parser = subparsers.add_parser(
        "schedule", help="TDM schedule artifact operations"
    )
    schedule_subparsers = schedule_parser.add_subparsers(
        dest="schedule_command", required=True
    )
    schedule_validate = schedule_subparsers.add_parser(
        "validate", help="independently validate a Phase 5 TDM schedule"
    )
    schedule_validate.add_argument("schedule", type=Path)
    schedule_validate.add_argument("--routes", type=Path, required=True)
    schedule_validate.add_argument("--platform", type=Path, required=True)

    phase5 = subparsers.add_parser(
        "phase5", help="schedule routed bit-hops into TDM lanes and slots"
    )
    phase5.add_argument("--routes", type=Path, required=True)
    phase5.add_argument("--platform", type=Path, required=True)
    phase5.add_argument("--out", type=Path, required=True)
    phase5.add_argument("--simulation-frames", type=int, default=16)

    split_parser = subparsers.add_parser(
        "split", help="per-FPGA netlist split artifact operations"
    )
    split_subparsers = split_parser.add_subparsers(
        dest="split_command", required=True
    )
    split_validate = split_subparsers.add_parser(
        "validate", help="independently validate Phase 6 split artifacts"
    )
    split_validate.add_argument("manifest", type=Path)
    split_validate.add_argument("--ir", type=Path, required=True)
    split_validate.add_argument("--assignment", type=Path, required=True)
    split_validate.add_argument("--schedule", type=Path, required=True)
    split_validate.add_argument("--platform", type=Path, required=True)

    phase6 = subparsers.add_parser(
        "phase6",
        help="split EmuIR and bind cut signals to logical TDM lanes",
    )
    phase6.add_argument("--ir", type=Path, required=True)
    phase6.add_argument("--assignment", type=Path, required=True)
    phase6.add_argument("--schedule", type=Path, required=True)
    phase6.add_argument("--platform", type=Path, required=True)
    phase6.add_argument("--out", type=Path, required=True)
    phase6.add_argument("--equivalence-cycles", type=int, default=16)
    phase6.add_argument("--equivalence-seed", type=int, default=20260727)

    lower = subparsers.add_parser(
        "lower-placement-ir",
        help="merge one partition with its synthesized transport EmuIR",
    )
    lower.add_argument("--netlist", type=Path, required=True)
    lower.add_argument("--transport", type=Path, required=True)
    lower.add_argument("--transport-ir", type=Path, required=True)
    lower.add_argument("--output", "-o", type=Path, required=True)
    lower.add_argument("--report", type=Path)
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
            policy=args.policy,
            verilog_output=args.verilog_output,
            executable=args.yosys,
            log_path=args.log,
        )
        _print_json(
            {
                "family": args.family,
                "policy": args.policy,
                "output": str(args.output),
                "verilog_output": (
                    str(args.verilog_output)
                    if args.verilog_output is not None
                    else None
                ),
                "sources": [str(source) for source in args.sources],
                "status": "pass",
                "top": args.top,
            }
        )
        return 0

    if args.command == "benchmark":
        report = run_benchmark(
            spec_path=args.spec,
            source_root=args.source_root,
            output_dir=args.out,
            yosys=args.yosys,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

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

    if args.command == "arch":
        if args.arch_command == "import-vivado-tsv":
            architecture = ArchitectureDB.from_vivado_tsv(args.input)
            write_json(args.output, architecture.to_dict())
        else:
            architecture = ArchitectureDB.load(args.path)
        _print_json(architecture.summary())
        return 0

    if args.command == "placement":
        architecture = ArchitectureDB.load(args.arch)
        ir = EmuIR.load(args.ir) if args.ir is not None else None
        if args.placement_command == "import-openparf":
            assert ir is not None
            placement = Placement.from_openparf_pl(
                args.input, architecture, ir
            )
            write_json(args.output, placement.to_dict())
            if args.xdc is not None:
                args.xdc.parent.mkdir(parents=True, exist_ok=True)
                args.xdc.write_text(placement.to_xdc(), encoding="utf-8")
        else:
            placement = Placement.load(args.path, architecture, ir)
        _print_json(placement.summary())
        return 0

    if args.command == "phase2":
        report = run_phase2(
            ir_path=args.ir,
            architecture_path=args.arch,
            output_dir=args.out,
            openparf_result=args.openparf_result,
        )
        _print_json(report)
        return 0

    if args.command == "partition":
        report = validate_phase3(
            ir_path=args.ir,
            platform_path=args.platform,
            clusters_path=args.clusters,
            assignment_path=args.assignment,
        )
        _print_json(report)
        return 0

    if args.command == "phase3":
        report = run_phase3(
            ir_path=args.ir,
            platform_path=args.platform,
            output_dir=args.out,
            constraints_path=args.constraints,
            seed=args.seed,
            min_used_fpgas=args.min_used_fpgas,
            balance_tolerance=args.balance_tolerance,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "route":
        report = validate_phase4(
            assignment_path=args.assignment,
            platform_path=args.platform,
            routes_path=args.routes,
        )
        _print_json(report)
        return 0

    if args.command == "phase4":
        report = run_phase4(
            assignment_path=args.assignment,
            platform_path=args.platform,
            output_dir=args.out,
            constraints_path=args.constraints,
            frame_slots=args.frame_slots,
            max_iterations=args.max_iterations,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "schedule":
        report = validate_phase5(
            routes_path=args.routes,
            platform_path=args.platform,
            schedule_path=args.schedule,
        )
        _print_json(report)
        return 0

    if args.command == "phase5":
        report = run_phase5(
            routes_path=args.routes,
            platform_path=args.platform,
            output_dir=args.out,
            simulation_frames=args.simulation_frames,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "split":
        report = validate_phase6(
            ir_path=args.ir,
            assignment_path=args.assignment,
            schedule_path=args.schedule,
            platform_path=args.platform,
            manifest_path=args.manifest,
        )
        _print_json(report)
        return 0

    if args.command == "phase6":
        report = run_phase6(
            ir_path=args.ir,
            assignment_path=args.assignment,
            schedule_path=args.schedule,
            platform_path=args.platform,
            output_dir=args.out,
            equivalence_cycles=args.equivalence_cycles,
            equivalence_seed=args.equivalence_seed,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "lower-placement-ir":
        report = run_placement_ir_lowering(
            netlist_path=args.netlist,
            transport_path=args.transport,
            transport_ir_path=args.transport_ir,
            output_path=args.output,
            report_path=args.report,
        )
        _print_json(report)
        return 0

    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (EmuFlowError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"emuflow: error: {error}", file=sys.stderr)
        return 1
