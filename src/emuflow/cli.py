import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .architecture import ArchitectureDB
from .benchmark import run_benchmark
from .bsp import run_phase8a
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
from .phase7c import run_phase7c
from .placement import Placement
from .platform import Platform
from .release import run_phase7d
from .synthesis import (
    VALID_SYNTHESIS_POLICIES,
    VALID_XILINX_FAMILIES,
    run_yosys,
)
from .sta import import_vivado_sta_tsv, write_vivado_cut_net_map
from .tdm import TDM_BASELINE_PROVIDER
from .tdm_ratio import TDM_RATIO_PROVIDER
from .yosys import import_yosys_json
from .verilog import emit_mapped_verilog


def _print_json(value: Dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _keyed_paths(values: Sequence[str], option: str) -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = value.partition("=")
        if not separator or not key or not raw_path:
            raise ValueError(f"{option}: expected KEY=PATH, got {value!r}")
        if key in result:
            raise ValueError(f"{option}: duplicate key {key!r}")
        result[key] = Path(raw_path)
    return result


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
    synthesis.add_argument(
        "--yosys",
        help="explicit comparison override; defaults to the in-tree build",
    )
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
    benchmark.add_argument(
        "--yosys",
        help="explicit comparison override; defaults to the in-tree build",
    )

    phase1 = subparsers.add_parser(
        "phase1", help="run the board-independent Phase 1 pipeline"
    )
    phase1.add_argument("--yosys-json", type=Path, required=True)
    phase1.add_argument("--platform", type=Path, required=True)
    phase1.add_argument("--out", type=Path, required=True)
    phase1.add_argument("--top")
    phase1.add_argument("--clock", action="append", default=[])
    phase1.add_argument(
        "--require-no-fabric-clock",
        action="store_true",
        help="fail when a LUT output drives an FD*.C clock pin",
    )

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
        help="explicit comparison/import .pl; default runs root-built OpenPARF",
    )
    phase2.add_argument(
        "--openparf-global-result",
        action="store_true",
        help=(
            "treat --openparf-result as global coordinates and legalize them "
            "onto exact ArchitectureDB Site/BEL slots"
        ),
    )
    phase2.add_argument(
        "--site-utilization-limit",
        type=float,
        default=0.75,
        help="maximum fraction of compatible slots exposed per site",
    )
    phase2.add_argument(
        "--site-y-range",
        type=int,
        nargs=2,
        metavar=("MIN_Y", "MAX_Y"),
        help=(
            "affinely map OpenPARF y coordinates into this inclusive "
            "ArchitectureDB region (for example, one SLR)"
        ),
    )
    phase2.add_argument(
        "--openparf-install",
        type=Path,
        help="explicit comparison override for an OpenPARF install root",
    )
    phase2.add_argument(
        "--openparf-python",
        type=Path,
        help=(
            "Python used to load root-built OpenPARF; defaults to the "
            "interpreter recorded by the root CMake build"
        ),
    )
    phase2.add_argument(
        "--reference-placement",
        action="store_true",
        help="use the deterministic greedy adapter reference in tests only",
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
    phase3.add_argument(
        "--provider",
        choices=("repart-replication", "repart", "tritonpart", "greedy"),
        default="tritonpart",
        help="partition provider (default: tritonpart)",
    )
    phase3.add_argument(
        "--openroad",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "OpenROAD/TritonPart build"
        ),
    )
    phase3.add_argument(
        "--tritonpart-solution",
        type=Path,
        help="import a precomputed TritonPart .part file instead of executing",
    )
    phase3.add_argument(
        "--net-weights",
        type=Path,
        help="optional emuflow.partition-net-weights/v1 JSON",
    )
    phase3.add_argument(
        "--tritonpart-timeout-seconds",
        type=int,
        default=3600,
    )
    phase3.add_argument(
        "--tritonpart-seed-attempts",
        type=int,
        default=1,
        help=(
            "try consecutive deterministic seeds until min-used-fpgas "
            "is satisfied"
        ),
    )
    phase3.add_argument(
        "--tritonpart-repair-min-used-fpgas",
        action="store_true",
        help=(
            "minimally move the smallest legal atomic clusters when the "
            "provider leaves required partitions empty"
        ),
    )
    phase3.add_argument(
        "--tritonpart-repair-balance",
        action="store_true",
        help=(
            "legalize a best-effort TritonPart solution against EmuFlow's "
            "independently checked multi-resource upper bounds"
        ),
    )
    phase3.add_argument(
        "--repart",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "RePart build"
        ),
    )
    phase3.add_argument(
        "--repart-solution",
        type=Path,
        help=(
            "import a precomputed RePart solution; '*' records are accepted "
            "only by --provider repart-replication"
        ),
    )
    phase3.add_argument(
        "--repart-timeout-seconds",
        type=int,
        default=3600,
    )

    sta_parser = subparsers.add_parser(
        "sta", help="STA path extraction artifact operations"
    )
    sta_subparsers = sta_parser.add_subparsers(
        dest="sta_command", required=True
    )
    sta_map = sta_subparsers.add_parser(
        "emit-vivado-cut-map",
        help="map stable EmuIR cut nets to mapped-Verilog net names",
    )
    sta_map.add_argument("--ir", type=Path, required=True)
    sta_map.add_argument("--assignment", type=Path, required=True)
    sta_map.add_argument("--output", "-o", type=Path, required=True)
    sta_import = sta_subparsers.add_parser(
        "import-vivado-tsv",
        help="import export_cut_timing_paths.tcl output",
    )
    sta_import.add_argument("--input", type=Path, required=True)
    sta_import.add_argument("--assignment", type=Path, required=True)
    sta_import.add_argument("--output", "-o", type=Path, required=True)

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
    route_validate.add_argument("--timing-paths", type=Path)

    phase4 = subparsers.add_parser(
        "phase4", help="route partition cut nets over BoardDB links"
    )
    phase4.add_argument("--assignment", type=Path, required=True)
    phase4.add_argument("--platform", type=Path, required=True)
    phase4.add_argument("--out", type=Path, required=True)
    phase4.add_argument("--constraints", type=Path)
    phase4.add_argument("--frame-slots", type=int)
    phase4.add_argument("--max-iterations", type=int)
    phase4.add_argument(
        "--provider",
        choices=[
            "negotiated-shortest-path-tree-v1",
            "timing-aware-load-balanced-v1",
        ],
        default=None,
        help=(
            "defaults to timing-aware-load-balanced-v1 when --timing-paths "
            "is supplied, otherwise the negotiated baseline"
        ),
    )
    phase4.add_argument(
        "--timing-paths",
        type=Path,
        help="emuflow.sta-paths/v1 input required by the timing-aware provider",
    )
    phase4.add_argument(
        "--router",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "emuflow_tlr_router build"
        ),
    )

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
    schedule_validate.add_argument("--ratio-plan", type=Path)

    phase5 = subparsers.add_parser(
        "phase5", help="schedule routed bit-hops into TDM lanes and slots"
    )
    phase5.add_argument("--routes", type=Path, required=True)
    phase5.add_argument("--platform", type=Path, required=True)
    phase5.add_argument("--out", type=Path, required=True)
    phase5.add_argument("--simulation-frames", type=int, default=16)
    phase5.add_argument(
        "--provider",
        choices=(TDM_RATIO_PROVIDER, TDM_BASELINE_PROVIDER),
        default=None,
        help=(
            "defaults to the academic provider when routes contain timing, "
            "otherwise the deterministic baseline"
        ),
    )
    phase5.add_argument(
        "--ratio-optimizer",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "emuflow_tdm_ratio_optimizer build"
        ),
    )
    phase5.add_argument("--ratio-max-iterations", type=int, default=500)
    phase5.add_argument("--max-ratio", type=int)
    phase5.add_argument("--ratio-quantum", type=int, default=8)
    phase5.add_argument(
        "--post-refinement-iterations", type=int, default=200
    )
    phase5.add_argument("--ratio-convergence", type=float, default=1.0e-9)

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

    emit_verilog = subparsers.add_parser(
        "emit-mapped-verilog",
        help="emit a structural Xilinx primitive netlist from EmuIR",
    )
    emit_verilog.add_argument("--ir", type=Path, required=True)
    emit_verilog.add_argument("--output", "-o", type=Path, required=True)
    emit_verilog.add_argument("--report", type=Path)

    phase7c = subparsers.add_parser(
        "phase7c",
        help="build and validate the virtual runtime/timing/QoR contract",
    )
    phase7c.add_argument("--schedule", type=Path, required=True)
    phase7c.add_argument("--platform", type=Path, required=True)
    phase7c.add_argument("--phase3-report", type=Path, required=True)
    phase7c.add_argument("--phase4-report", type=Path, required=True)
    phase7c.add_argument("--phase5-report", type=Path, required=True)
    phase7c.add_argument("--phase6-report", type=Path, required=True)
    phase7c.add_argument("--physical-summary", type=Path)
    phase7c.add_argument("--simulation-frames", type=int, default=12)
    phase7c.add_argument("--out", type=Path, required=True)

    phase7d = subparsers.add_parser(
        "phase7d",
        help="audit and hash a complete board-independent G0-G9 release",
    )
    phase7d.add_argument("--benchmark-report", type=Path, required=True)
    phase7d.add_argument("--phase3-report", type=Path, required=True)
    phase7d.add_argument("--phase4-report", type=Path, required=True)
    phase7d.add_argument("--phase5-report", type=Path, required=True)
    phase7d.add_argument("--phase6-report", type=Path, required=True)
    phase7d.add_argument("--phase7c-report", type=Path, required=True)
    phase7d.add_argument("--runtime-contract", type=Path, required=True)
    phase7d.add_argument("--qor-report", type=Path, required=True)
    phase7d.add_argument("--physical-summary", type=Path, required=True)
    phase7d.add_argument("--platform", type=Path, required=True)
    phase7d.add_argument(
        "--lowering-report", action="append", default=[], metavar="FPGA=PATH"
    )
    phase7d.add_argument(
        "--placement-report", action="append", default=[], metavar="FPGA=PATH"
    )
    phase7d.add_argument(
        "--emission-report", action="append", default=[], metavar="FPGA=PATH"
    )
    phase7d.add_argument(
        "--artifact", action="append", default=[], metavar="LABEL=PATH"
    )
    phase7d.add_argument("--source-commit", required=True)
    phase7d.add_argument("--out", type=Path, required=True)

    phase8a = subparsers.add_parser(
        "phase8a",
        help="seal the hardware-BSP requirements for a G0-G9 release",
    )
    phase8a.add_argument("--release-manifest", type=Path, required=True)
    phase8a.add_argument("--phase6-report", type=Path, required=True)
    phase8a.add_argument("--platform", type=Path, required=True)
    phase8a.add_argument(
        "--anchor", action="append", default=[], metavar="FPGA=PATH"
    )
    phase8a.add_argument("--out", type=Path, required=True)
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
            require_no_fabric_clock=args.require_no_fabric_clock,
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
            openparf_global_result=args.openparf_global_result,
            site_utilization_limit=args.site_utilization_limit,
            site_y_range=(
                tuple(args.site_y_range)
                if args.site_y_range is not None
                else None
            ),
            openparf_install=args.openparf_install,
            openparf_python=args.openparf_python,
            reference_placement=args.reference_placement,
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
            provider=args.provider,
            openroad=args.openroad,
            tritonpart_solution=args.tritonpart_solution,
            net_weights_path=args.net_weights,
            tritonpart_timeout_seconds=args.tritonpart_timeout_seconds,
            tritonpart_seed_attempts=args.tritonpart_seed_attempts,
            tritonpart_repair_min_used_fpgas=(
                args.tritonpart_repair_min_used_fpgas
            ),
            tritonpart_repair_balance=args.tritonpart_repair_balance,
            repart=args.repart,
            repart_solution=args.repart_solution,
            repart_timeout_seconds=args.repart_timeout_seconds,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "sta":
        if args.sta_command == "emit-vivado-cut-map":
            report = write_vivado_cut_net_map(
                args.ir, args.assignment, args.output
            )
        else:
            report = import_vivado_sta_tsv(
                args.input, args.assignment, args.output
            )
        _print_json(report)
        return 0

    if args.command == "route":
        report = validate_phase4(
            assignment_path=args.assignment,
            platform_path=args.platform,
            routes_path=args.routes,
            timing_paths_path=args.timing_paths,
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
            provider=args.provider,
            timing_paths_path=args.timing_paths,
            router=args.router,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "schedule":
        report = validate_phase5(
            routes_path=args.routes,
            platform_path=args.platform,
            schedule_path=args.schedule,
            ratio_plan_path=args.ratio_plan,
        )
        _print_json(report)
        return 0

    if args.command == "phase5":
        report = run_phase5(
            routes_path=args.routes,
            platform_path=args.platform,
            output_dir=args.out,
            simulation_frames=args.simulation_frames,
            provider=args.provider,
            ratio_optimizer=args.ratio_optimizer,
            ratio_max_iterations=args.ratio_max_iterations,
            max_ratio=args.max_ratio,
            ratio_quantum=args.ratio_quantum,
            post_refinement_iterations=args.post_refinement_iterations,
            convergence=args.ratio_convergence,
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

    if args.command == "emit-mapped-verilog":
        report = emit_mapped_verilog(
            ir_path=args.ir,
            output_path=args.output,
            report_path=args.report,
        )
        _print_json(dict(report))
        return 0

    if args.command == "phase7c":
        report = run_phase7c(
            schedule_path=args.schedule,
            platform_path=args.platform,
            phase3_report_path=args.phase3_report,
            phase4_report_path=args.phase4_report,
            phase5_report_path=args.phase5_report,
            phase6_report_path=args.phase6_report,
            physical_summary_path=args.physical_summary,
            simulation_frames=args.simulation_frames,
            output_dir=args.out,
        )
        _print_json(report)
        return 0 if report["status"] in {"generated", "pass"} else 2

    if args.command == "phase7d":
        report = run_phase7d(
            benchmark_report_path=args.benchmark_report,
            phase3_report_path=args.phase3_report,
            phase4_report_path=args.phase4_report,
            phase5_report_path=args.phase5_report,
            phase6_report_path=args.phase6_report,
            phase7c_report_path=args.phase7c_report,
            runtime_contract_path=args.runtime_contract,
            qor_report_path=args.qor_report,
            physical_summary_path=args.physical_summary,
            platform_path=args.platform,
            lowering_report_paths=_keyed_paths(
                args.lowering_report, "--lowering-report"
            ),
            placement_report_paths=_keyed_paths(
                args.placement_report, "--placement-report"
            ),
            emission_report_paths=_keyed_paths(
                args.emission_report, "--emission-report"
            ),
            artifact_paths=_keyed_paths(args.artifact, "--artifact"),
            source_commit=args.source_commit,
            output_dir=args.out,
        )
        _print_json(report)
        return 0

    if args.command == "phase8a":
        report = run_phase8a(
            release_manifest_path=args.release_manifest,
            phase6_report_path=args.phase6_report,
            platform_path=args.platform,
            anchor_paths=_keyed_paths(args.anchor, "--anchor"),
            output_dir=args.out,
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
