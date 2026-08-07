import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .archive import (
    DEFAULT_MAX_COPY_BYTES,
    cleanup_validation_source,
    create_validation_archive,
    validate_validation_archive,
)
from .architecture import ArchitectureDB
from .benchmark import run_benchmark
from .board_arm_mps4 import materialize_arm_mps4_boarddb
from .board_link_timing import (
    build_board_link_timing_model,
    validate_board_link_timing,
)
from .board_support import validate_board_support_overlay_file
from .bsp import run_phase8a
from .cross_stage import (
    evaluate_cross_stage_candidate,
    run_cross_stage_optimization,
    validate_cross_stage_candidate,
    validate_cross_stage_report,
)
from .contest_eda2025 import (
    evaluate_eda2025_routes,
    import_eda2025_instance,
    materialize_eda2025_rtl_boarddb,
    optimize_eda2025_routing,
    optimize_eda2025_topology,
)
from .contest_eda2024 import (
    evaluate_eda2024_solution,
    materialize_eda2024_rtl_boarddb,
)
from .contest_eda2023 import (
    evaluate_eda2023_solution,
    import_eda2023_case,
    materialize_eda2023_rtl_boarddb,
    optimize_eda2023_tdm,
)
from .contest_iccad2019 import (
    evaluate_iccad2019_solution,
    import_iccad2019_instance,
    materialize_iccad2019_rtl_boarddb,
    optimize_iccad2019_ratios,
)
from .errors import EmuFlowError
from .fpga_interchange import (
    check_ir_architecture_capacity,
    run_fpga_interchange_architecture_import,
    validate_fpga_interchange_architecture,
)
from .io import read_json, write_json
from .ir import EmuIR
from .lowering import run_placement_ir_lowering
from .multi_fpga_flow import run_multi_fpga_flow
from .multi_fpga_bsp_flow import run_multi_fpga_bsp_flow
from .multi_fpga_physical_flow import run_multi_fpga_physical_flow
from .opensta import (
    DEFAULT_TIMING_MODEL,
    parse_clock_definitions,
    run_opensta_path_database,
)
from .open_physical_flow import run_open_physical_flow
from .phase1 import run_phase1
from .phase2 import run_phase2
from .phase3 import run_phase3, validate_phase3
from .phase4 import run_phase4, validate_phase4
from .phase5 import run_phase5, validate_phase5
from .phase6 import run_phase6, validate_phase6
from .phase7c import run_phase7c
from .packed_netlist import (
    run_packed_netlist_import,
    validate_packed_netlist_file,
)
from .packed_placement import run_packed_openparf_placement
from .partition_feedback import run_partition_feedback
from .physical_pins import (
    SERIAL_TRANSCEIVER_PROVIDER,
    run_phase6b,
    validate_package_pin_binding,
    validate_serial_transceiver_binding,
)
from .physical_regions import (
    run_physical_region_merge,
    validate_fpga_interchange_architecture_regions,
)
from .placement import Placement
from .platform import Platform
from .pin_planning import (
    build_pin_plan,
    build_signal_position_hints,
    validate_pin_plan,
)
from .release import run_phase7d
from .route_artifact import validate_vpr_route_artifacts
from .runtime_sync import (
    run_runtime_sync_materialization,
    validate_runtime_sync_provider,
)
from .synthesis import (
    VALID_SYNTHESIS_POLICIES,
    VALID_XILINX_FAMILIES,
    run_yosys,
)
from .sta import (
    derive_partition_net_weights,
    import_vivado_path_database_tsv,
    import_vivado_sta_tsv,
    project_sta_path_database,
    validate_sta_path_database,
    write_vivado_cut_net_map,
    write_vivado_net_map,
)
from .serial_wrapper import run_phase6c
from .serial_phy_provider import validate_serial_phy_provider_file
from .serial_phy_elaboration import run_serial_phy_elaboration
from .serial_phy_recipe import materialize_serial_phy_recipe
from .tdm import TDM_BASELINE_PROVIDER
from .tdm_ratio import TDM_RATIO_PROVIDER, TDM_TIMING_DAG_RATIO_PROVIDER
from .timing_routing import (
    NATIVE_ROUTER_PROVIDER,
    ROUTE_TDM_PROVIDER,
    TLR_PROVIDER,
)
from .yosys import import_yosys_json
from .verilog import emit_mapped_verilog
from .vtr_architecture import (
    fetch_pinned_vtr_architecture,
    read_vpr_placement_dimensions,
    run_vtr_architecture_import,
    validate_vtr_architecture_db,
    validate_vtr_timing_db_file,
)
from .vpr import run_vpr, run_vpr_route_packed, run_vtr_yosys
from .vivado_board_flow import run_vivado_board_flow
from .vivado_board_timing import run_vivado_board_timing
from .vivado_pin_sites import derive_vivado_pin_sites


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


def _keyed_values(values: Sequence[str], option: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item:
            raise ValueError(f"{option}: expected KEY=VALUE, got {value!r}")
        if key in result:
            raise ValueError(f"{option}: duplicate key {key!r}")
        result[key] = item
    return result


def _jsonable_cli_configuration(args: argparse.Namespace) -> Dict[str, Any]:
    def normalize(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value.resolve())
        if isinstance(value, dict):
            return {
                str(key): normalize(item)
                for key, item in sorted(value.items())
            }
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    return {
        key: normalize(value)
        for key, value in sorted(vars(args).items())
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emuflow",
        description="Open multi-FPGA emulation flow frontend",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser(
        "archive", help="archive and safely clean completed validation runs"
    )
    archive_subparsers = archive_parser.add_subparsers(
        dest="archive_command", required=True
    )
    archive_create = archive_subparsers.add_parser(
        "create", help="create a checked, storage-bounded validation archive"
    )
    archive_create.add_argument("--flow", type=Path, required=True)
    archive_create.add_argument("--out", type=Path, required=True)
    archive_create.add_argument("--run-id", required=True)
    archive_create.add_argument("--source-commit")
    archive_create.add_argument(
        "--max-copy-bytes", type=int, default=DEFAULT_MAX_COPY_BYTES
    )
    archive_create.add_argument(
        "--tool-version", action="append", default=[], metavar="NAME=VERSION"
    )
    archive_validate = archive_subparsers.add_parser(
        "validate", help="verify an archive without its original run directory"
    )
    archive_validate.add_argument("archive", type=Path)
    archive_cleanup = archive_subparsers.add_parser(
        "cleanup", help="delete a source run only after sealed archive validation"
    )
    archive_cleanup.add_argument("archive", type=Path)
    archive_cleanup.add_argument("--flow", type=Path, required=True)

    platform_parser = subparsers.add_parser("platform", help="BoardDB operations")
    platform_subparsers = platform_parser.add_subparsers(
        dest="platform_command", required=True
    )
    platform_validate = platform_subparsers.add_parser(
        "validate", help="validate and summarize a BoardDB"
    )
    platform_validate.add_argument("path", type=Path)
    platform_validate.add_argument("--normalized-out", type=Path)
    platform_mps4 = platform_subparsers.add_parser(
        "arm-mps4-materialize",
        help="materialize Arm's documented three-MPS4 serial-link topology",
    )
    platform_mps4.add_argument("--output", "-o", type=Path, required=True)
    platform_mps4.add_argument("--name", default="arm_mps4_3board_ring")
    platform_mps4.add_argument(
        "--fabric-clock-mhz", type=float, required=True
    )
    platform_mps4.add_argument(
        "--payload-bits-per-lane-per-cycle", type=int, required=True
    )
    platform_mps4.add_argument(
        "--latency-cycles", type=int, required=True
    )
    platform_mps4.add_argument(
        "--utilization-limit", type=float, default=0.75
    )
    platform_overlay = platform_subparsers.add_parser(
        "overlay-validate",
        help="validate board-specific site, reference-clock, and reset bindings",
    )
    platform_overlay.add_argument("--platform", type=Path, required=True)
    platform_overlay.add_argument("--overlay", type=Path, required=True)
    platform_overlay.add_argument("--normalized-out", type=Path)
    platform_gt_sites = platform_subparsers.add_parser(
        "vivado-derive-gt-sites",
        help="derive GT sites from BoardDB package pins using a Vivado device DB",
    )
    platform_gt_sites.add_argument("--platform", type=Path, required=True)
    platform_gt_sites.add_argument("--vivado", type=Path, required=True)
    platform_gt_sites.add_argument("--out", type=Path, required=True)
    platform_link_model = platform_subparsers.add_parser(
        "link-timing-model",
        help="materialize explicit directed link-delay bounds from BoardDB",
    )
    platform_link_model.add_argument("--platform", type=Path, required=True)
    platform_link_model.add_argument("--output", "-o", type=Path, required=True)
    platform_link_validate = platform_subparsers.add_parser(
        "link-timing-validate",
        help="validate a characterized or measured BoardLinkTimingDB",
    )
    platform_link_validate.add_argument("--platform", type=Path, required=True)
    platform_link_validate.add_argument("--input", type=Path, required=True)

    phy_provider = subparsers.add_parser(
        "phy-provider", help="serial PHY provider and vendor recipe operations"
    )
    phy_provider_subparsers = phy_provider.add_subparsers(
        dest="phy_provider_command", required=True
    )
    phy_provider_validate = phy_provider_subparsers.add_parser(
        "validate", help="validate source inventory and BoardDB compatibility"
    )
    phy_provider_validate.add_argument("--manifest", type=Path, required=True)
    phy_provider_validate.add_argument("--platform", type=Path)
    phy_provider_validate.add_argument("--normalized-out", type=Path)
    phy_provider_elaborate = phy_provider_subparsers.add_parser(
        "elaborate", help="elaborate provider sources with generated FPGA shells"
    )
    phy_provider_elaborate.add_argument("--manifest", type=Path, required=True)
    phy_provider_elaborate.add_argument("--platform", type=Path, required=True)
    phy_provider_elaborate.add_argument("--phase6c-dir", type=Path, required=True)
    phy_provider_elaborate.add_argument(
        "--runtime-controller", type=Path, required=True
    )
    phy_provider_elaborate.add_argument(
        "--transport", action="append", default=[], metavar="FPGA=PATH"
    )
    elaborate_tool = phy_provider_elaborate.add_mutually_exclusive_group(
        required=True
    )
    elaborate_tool.add_argument("--yosys", type=Path)
    elaborate_tool.add_argument("--vivado", type=Path)
    phy_provider_elaborate.add_argument("--out", type=Path, required=True)
    phy_provider_materialize = phy_provider_subparsers.add_parser(
        "materialize-recipe",
        help="materialize a source-visible vendor GT recipe into a build directory",
    )
    phy_provider_materialize.add_argument(
        "--manifest", type=Path, required=True
    )
    phy_provider_materialize.add_argument("--part", required=True)
    phy_provider_materialize.add_argument("--vivado", type=Path, required=True)
    phy_provider_materialize.add_argument("--platform", type=Path)
    phy_provider_materialize.add_argument("--out", type=Path, required=True)

    runtime_sync = subparsers.add_parser(
        "runtime-sync",
        help="source-visible distributed runtime synchronization operations",
    )
    runtime_sync_subparsers = runtime_sync.add_subparsers(
        dest="runtime_sync_command", required=True
    )
    runtime_sync_validate = runtime_sync_subparsers.add_parser(
        "validate-provider",
        help="validate the runtime synchronization source inventory",
    )
    runtime_sync_validate.add_argument("--provider", type=Path, required=True)
    runtime_sync_materialize = runtime_sync_subparsers.add_parser(
        "materialize",
        help="build a deterministic synchronization tree and HDL testbench",
    )
    runtime_sync_materialize.add_argument("--platform", type=Path, required=True)
    runtime_sync_materialize.add_argument("--provider", type=Path, required=True)
    runtime_sync_materialize.add_argument("--root")
    runtime_sync_materialize.add_argument(
        "--ready-stable-cycles", type=int, default=4
    )
    runtime_sync_materialize.add_argument("--out", type=Path, required=True)

    contest_parser = subparsers.add_parser(
        "contest", help="public multi-FPGA contest format adapters"
    )
    contest_subparsers = contest_parser.add_subparsers(
        dest="contest_command", required=True
    )
    eda2024_evaluate = contest_subparsers.add_parser(
        "eda2024-evaluate",
        help="independently check a 2024 logic-replication solution",
    )
    eda2024_evaluate.add_argument("--case-dir", type=Path, required=True)
    eda2024_evaluate.add_argument("--solution", type=Path)
    eda2024_evaluate.add_argument(
        "--runtime-seconds", type=float, default=0.0
    )
    eda2024_evaluate.add_argument("--output", "-o", type=Path)
    eda2024_boarddb = contest_subparsers.add_parser(
        "eda2024-materialize-boarddb",
        help="project a public unweighted topology using explicit abstract lanes",
    )
    eda2024_boarddb.add_argument("--case-dir", type=Path, required=True)
    eda2024_boarddb.add_argument("--device-template", type=Path, required=True)
    eda2024_boarddb.add_argument("--output", "-o", type=Path, required=True)
    eda2024_boarddb.add_argument("--route-constraints-output", type=Path)
    eda2024_boarddb.add_argument("--name", required=True)
    eda2024_boarddb.add_argument("--lanes-per-edge", type=int, required=True)
    eda2024_boarddb.add_argument("--template-fpga")
    eda2024_boarddb.add_argument("--fabric-clock-mhz", type=float, default=50.0)
    eda2024_boarddb.add_argument("--latency-cycles", type=int, default=2)
    eda2024_boarddb.add_argument(
        "--link-mode",
        choices=("abstract", "parallel", "serial", "source_synchronous"),
        default="abstract",
    )
    eda2023_import = contest_subparsers.add_parser(
        "eda2023-import",
        help="normalize an official 2023 die-level routing case",
    )
    eda2023_import.add_argument("--case-dir", type=Path, required=True)
    eda2023_import.add_argument("--name", required=True)
    eda2023_import.add_argument("--out", type=Path, required=True)
    eda2023_boarddb = contest_subparsers.add_parser(
        "eda2023-materialize-boarddb",
        help="project public die Wire banks onto RTL-capable physical FPGAs",
    )
    eda2023_boarddb.add_argument("--instance", type=Path, required=True)
    eda2023_boarddb.add_argument("--device-template", type=Path, required=True)
    eda2023_boarddb.add_argument("--output", "-o", type=Path, required=True)
    eda2023_boarddb.add_argument("--name", required=True)
    eda2023_boarddb.add_argument("--template-fpga")
    eda2023_boarddb.add_argument("--lane-scale", type=int, default=1)
    eda2023_boarddb.add_argument("--fabric-clock-mhz", type=float, default=50.0)
    eda2023_boarddb.add_argument("--latency-cycles", type=int, default=2)
    eda2023_boarddb.add_argument(
        "--link-mode",
        choices=("abstract", "parallel", "serial", "source_synchronous"),
        default="abstract",
    )
    eda2023_optimize = contest_subparsers.add_parser(
        "eda2023-optimize",
        help="assign legal per-Wire TDM ratios to routed die trees",
    )
    eda2023_optimize.add_argument("--instance", type=Path, required=True)
    eda2023_optimize.add_argument("--routes", type=Path, required=True)
    eda2023_optimize.add_argument("--out", type=Path, required=True)
    eda2023_optimize.add_argument("--optimizer")
    eda2023_optimize.add_argument("--max-iterations", type=int, default=100)
    eda2023_optimize.add_argument(
        "--post-refinement-iterations", type=int, default=2000
    )
    eda2023_optimize.add_argument("--exact-domain-limit", type=int, default=2048)
    eda2023_evaluate = contest_subparsers.add_parser(
        "eda2023-evaluate",
        help="independently check routed trees and a per-Wire TDM plan",
    )
    eda2023_evaluate.add_argument("--instance", type=Path, required=True)
    eda2023_evaluate.add_argument("--routes", type=Path, required=True)
    eda2023_evaluate.add_argument("--tdm-plan", type=Path, required=True)
    eda2023_evaluate.add_argument("--output", "-o", type=Path)
    iccad2019_import = contest_subparsers.add_parser(
        "iccad2019-import",
        help="normalize an official ICCAD 2019 Problem B instance",
    )
    iccad2019_import.add_argument("--input", type=Path, required=True)
    iccad2019_import.add_argument("--name", required=True)
    iccad2019_import.add_argument("--out", type=Path, required=True)
    iccad2019_boarddb = contest_subparsers.add_parser(
        "iccad2019-materialize-boarddb",
        help="populate a Problem B FPGA graph with an RTL-capable device template",
    )
    iccad2019_boarddb.add_argument("--instance", type=Path, required=True)
    iccad2019_boarddb.add_argument("--device-template", type=Path, required=True)
    iccad2019_boarddb.add_argument("--output", "-o", type=Path, required=True)
    iccad2019_boarddb.add_argument("--name", required=True)
    iccad2019_boarddb.add_argument("--template-fpga")
    iccad2019_boarddb.add_argument("--lane-scale", type=int, default=1)
    iccad2019_boarddb.add_argument("--fabric-clock-mhz", type=float, default=50.0)
    iccad2019_boarddb.add_argument("--latency-cycles", type=int, default=2)
    iccad2019_boarddb.add_argument(
        "--link-mode",
        choices=("abstract", "parallel", "serial", "source_synchronous"),
        default="abstract",
    )
    iccad2019_optimize = contest_subparsers.add_parser(
        "iccad2019-optimize",
        help="assign exact-harmonic TDM ratios to EmuFlow routes",
    )
    iccad2019_optimize.add_argument("--instance", type=Path, required=True)
    iccad2019_optimize.add_argument("--routes", type=Path, required=True)
    iccad2019_optimize.add_argument("--output", "-o", type=Path, required=True)
    iccad2019_optimize.add_argument("--optimizer")
    iccad2019_optimize.add_argument("--max-iterations", type=int, default=500)
    iccad2019_optimize.add_argument(
        "--post-refinement-iterations", type=int, default=20000
    )
    iccad2019_evaluate = contest_subparsers.add_parser(
        "iccad2019-evaluate",
        help="independently check an official-format ICCAD 2019 solution",
    )
    iccad2019_evaluate.add_argument("--instance", type=Path, required=True)
    iccad2019_evaluate.add_argument("--solution", type=Path, required=True)
    iccad2019_evaluate.add_argument("--runtime-seconds", type=float)
    iccad2019_evaluate.add_argument("--median-runtime-seconds", type=float)
    eda2025_import = contest_subparsers.add_parser(
        "eda2025-import",
        help="normalize a 2025 EDA Elite routing benchmark",
    )
    eda2025_import.add_argument("--info", type=Path, required=True)
    eda2025_import.add_argument("--net", type=Path, required=True)
    eda2025_import.add_argument("--topology", type=Path, required=True)
    eda2025_import.add_argument("--assignment", type=Path, required=True)
    eda2025_import.add_argument("--name", required=True)
    eda2025_import.add_argument("--out", type=Path, required=True)
    eda2025_import.add_argument("--alpha-ns", type=float, default=0.7)
    eda2025_import.add_argument("--beta-ns", type=float, default=30.0)
    eda2025_import.add_argument("--ratio-quantum", type=int, default=8)
    eda2025_import.add_argument("--max-ratio", type=int, default=512)
    eda2025_import.add_argument(
        "--topology-change-fraction", type=float, default=0.3
    )
    eda2025_evaluate = contest_subparsers.add_parser(
        "eda2025-evaluate",
        help="independently score EmuFlow routes with the 2025 model",
    )
    eda2025_evaluate.add_argument("--instance", type=Path, required=True)
    eda2025_evaluate.add_argument("--routes", type=Path, required=True)
    eda2025_evaluate.add_argument("--new-topology", type=Path)
    eda2025_evaluate.add_argument("--runtime-seconds", type=float, default=0.0)
    eda2025_evaluate.add_argument("--output", "-o", type=Path)
    eda2025_evaluate.add_argument(
        "--official-out",
        type=Path,
        help="also write design.route.out and design.newtopo",
    )
    eda2025_topology = contest_subparsers.add_parser(
        "eda2025-optimize-topology",
        help="optimize channel counts and emit Phase 4 rerouting contracts",
    )
    eda2025_topology.add_argument("--instance", type=Path, required=True)
    eda2025_topology.add_argument("--routes", type=Path, required=True)
    eda2025_topology.add_argument("--out", type=Path, required=True)
    eda2025_topology.add_argument("--optimizer")
    eda2025_topology.add_argument("--max-changes", type=int)
    eda2025_topology.add_argument(
        "--topology",
        type=Path,
        help="current design.newtopo for a subsequent optimization round",
    )
    eda2025_routing = contest_subparsers.add_parser(
        "eda2025-optimize-routing",
        help="select topology candidates using real Phase 4 rerouting",
    )
    eda2025_routing.add_argument("--instance", type=Path, required=True)
    eda2025_routing.add_argument("--routes", type=Path, required=True)
    eda2025_routing.add_argument("--out", type=Path, required=True)
    eda2025_routing.add_argument("--topology", type=Path)
    eda2025_routing.add_argument("--router")
    eda2025_routing.add_argument("--topology-optimizer")
    eda2025_routing.add_argument("--max-rounds", type=int, default=4)
    eda2025_routing.add_argument(
        "--capacity-only", action="store_true", help="disable shortcut candidates"
    )
    eda2025_boarddb = contest_subparsers.add_parser(
        "eda2025-materialize-boarddb",
        help="populate a contest topology with an RTL-capable FPGA template",
    )
    eda2025_boarddb.add_argument("--instance", type=Path, required=True)
    eda2025_boarddb.add_argument("--device-template", type=Path, required=True)
    eda2025_boarddb.add_argument("--output", "-o", type=Path, required=True)
    eda2025_boarddb.add_argument("--route-constraints-output", type=Path)
    eda2025_boarddb.add_argument("--name", required=True)
    eda2025_boarddb.add_argument("--topology", type=Path)
    eda2025_boarddb.add_argument("--template-fpga")
    eda2025_boarddb.add_argument("--lane-scale", type=int, default=1)
    eda2025_boarddb.add_argument("--fabric-clock-mhz", type=float, default=50.0)
    eda2025_boarddb.add_argument("--latency-cycles", type=int, default=2)
    eda2025_boarddb.add_argument(
        "--link-mode",
        choices=("abstract", "parallel", "serial", "source_synchronous"),
        default="abstract",
    )
    eda2025_topology.add_argument(
        "--enable-shortcuts",
        action="store_true",
        help="also propose direct links; candidates still require rerouting",
    )

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

    vpr_parser = subparsers.add_parser(
        "vpr", help="open VTR/VPR per-FPGA physical backend"
    )
    vpr_subparsers = vpr_parser.add_subparsers(
        dest="vpr_command", required=True
    )
    vpr_synth = vpr_subparsers.add_parser(
        "synth",
        help="map RTL to VTR-compatible eBLIF for VPR",
    )
    vpr_synth.add_argument("sources", nargs="+", type=Path)
    vpr_synth.add_argument("--top", required=True)
    vpr_synth.add_argument("--output", "-o", type=Path, required=True)
    vpr_synth.add_argument(
        "--yosys",
        help="explicit comparison override; defaults to the in-tree build",
    )
    vpr_synth.add_argument("--log", type=Path)
    vpr_synth.add_argument(
        "--hard-blocks",
        action="store_true",
        help=(
            "map multipliers and RAMs to the public VTR flagship "
            "architecture modes"
        ),
    )
    vpr_full_open = vpr_subparsers.add_parser(
        "fpga-open",
        aliases=["full-open"],
        help=(
            "run one FPGA's checked RTL-to-routed open physical backend "
            "('full-open' is a deprecated alias)"
        ),
    )
    vpr_full_open.add_argument("sources", nargs="+", type=Path)
    vpr_full_open.add_argument("--top", required=True)
    vpr_full_open.add_argument("--out", type=Path, required=True)
    vpr_full_open.add_argument(
        "--architecture",
        type=Path,
        help="optional VTR XML; otherwise fetch the pinned flagship model",
    )
    vpr_full_open.add_argument(
        "--architecture-id", default="vtr-flagship-k6-n10-40nm"
    )
    vpr_full_open.add_argument(
        "--logic-only",
        action="store_true",
        help="disable the default flagship multiplier/RAM hard-block mapping",
    )
    vpr_full_open.add_argument("--yosys")
    vpr_full_open.add_argument("--vpr")
    vpr_full_open.add_argument("--architecture-importer")
    vpr_full_open.add_argument("--packed-importer")
    vpr_full_open.add_argument("--route-checker")
    vpr_full_open.add_argument("--openparf-install", type=Path)
    vpr_full_open.add_argument("--openparf-python", type=Path)
    vpr_full_open.add_argument("--seed", type=int, default=1)
    vpr_full_open.add_argument(
        "--route-channel-width", type=int, default=300
    )

    multi_fpga = subparsers.add_parser(
        "multi-fpga",
        help="board-independent multi-FPGA compilation",
    )
    multi_fpga_subparsers = multi_fpga.add_subparsers(
        dest="multi_fpga_command", required=True
    )
    multi_fpga_compile = multi_fpga_subparsers.add_parser(
        "compile",
        help=(
            "run generic synthesis, partitioning, system routing, TDM, "
            "and per-FPGA split generation"
        ),
    )
    multi_fpga_compile.add_argument("sources", nargs="*", type=Path)
    multi_fpga_compile.add_argument("--top")
    multi_fpga_compile.add_argument("--clock", action="append", default=[])
    multi_fpga_compile.add_argument(
        "--yosys-json",
        type=Path,
        help="use existing Yosys JSON instead of synthesizing RTL",
    )
    multi_fpga_compile.add_argument("--platform", type=Path, required=True)
    multi_fpga_compile.add_argument("--out", type=Path, required=True)
    multi_fpga_compile.add_argument(
        "--archive-out",
        type=Path,
        help="archive a successful full-flow run to this separate directory",
    )
    multi_fpga_compile.add_argument(
        "--archive-run-id",
        help="archive identity (defaults to the flow output directory name)",
    )
    multi_fpga_compile.add_argument("--archive-source-commit")
    multi_fpga_compile.add_argument(
        "--archive-max-copy-bytes", type=int, default=DEFAULT_MAX_COPY_BYTES
    )
    multi_fpga_compile.add_argument(
        "--archive-tool-version",
        action="append",
        default=[],
        metavar="NAME=VERSION",
    )
    multi_fpga_compile.add_argument(
        "--archive-cleanup",
        action="store_true",
        help="remove --out only after the new archive passes its cleanup gate",
    )
    multi_fpga_compile.add_argument("--yosys")
    multi_fpga_compile.add_argument(
        "--mapping-profile",
        choices=("vtr-hard-blocks", "generic-soft"),
        default="vtr-hard-blocks",
        help=(
            "RTL mapping profile; the default preserves public VTR "
            "multiplier/RAM hard blocks"
        ),
    )
    multi_fpga_compile.add_argument("--partition-constraints", type=Path)
    multi_fpga_compile.add_argument(
        "--partition-provider",
        choices=("repart-replication", "repart", "tritonpart", "greedy"),
        default="tritonpart",
    )
    multi_fpga_compile.add_argument("--seed", type=int, default=0)
    multi_fpga_compile.add_argument("--min-used-fpgas", type=int)
    multi_fpga_compile.add_argument("--balance-tolerance", type=float)
    multi_fpga_compile.add_argument("--openroad")
    multi_fpga_compile.add_argument("--repart")
    multi_fpga_compile.add_argument(
        "--partition-timeout-seconds", type=int, default=3600
    )
    multi_fpga_compile.add_argument(
        "--partition-seed-attempts", type=int, default=1
    )
    multi_fpga_compile.add_argument(
        "--partition-repair-min-used-fpgas",
        action="store_true",
        help=(
            "minimally move legal atomic clusters if the partitioner "
            "leaves required FPGAs empty"
        ),
    )
    multi_fpga_compile.add_argument(
        "--partition-repair-balance",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "legalize a best-effort assignment against independently "
            "checked multi-resource balance bounds (enabled by default)"
        ),
    )
    multi_fpga_compile.add_argument(
        "--timing-driven",
        action="store_true",
        help=(
            "produce TimingPathDB, derive partition weights, project cut "
            "paths, and enable timing-aware system routing/TDM"
        ),
    )
    multi_fpga_compile.add_argument(
        "--timing-backend",
        choices=("opensta", "vivado"),
        default="opensta",
        help="produce the common TimingPathDB with OpenSTA or Vivado",
    )
    multi_fpga_compile.add_argument(
        "--clock-period",
        action="append",
        default=[],
        metavar="CLOCK=PERIOD_NS",
    )
    multi_fpga_compile.add_argument(
        "--timing-model",
        type=Path,
        default=DEFAULT_TIMING_MODEL,
    )
    multi_fpga_compile.add_argument(
        "--architecture-timing-db",
        type=Path,
        help=(
            "public VTR TimingDB used to construct the pre-placement "
            "OpenSTA model; also enables --timing-driven"
        ),
    )
    multi_fpga_compile.add_argument(
        "--opensta",
        "--openroad-sta",
        dest="opensta",
        help="explicit OpenSTA executable override",
    )
    multi_fpga_compile.add_argument("--timing-vivado")
    multi_fpga_compile.add_argument(
        "--sta-max-paths", type=int, default=200000
    )
    multi_fpga_compile.add_argument(
        "--timing-criticality-scale", type=float, default=9.0
    )
    multi_fpga_compile.add_argument(
        "--timing-criticality-exponent", type=float, default=2.0
    )
    multi_fpga_compile.add_argument("--route-constraints", type=Path)
    multi_fpga_compile.add_argument(
        "--board-link-timing-db",
        type=Path,
        help=(
            "apply versioned board-link delay bounds to timing-driven "
            "system routing, TDM, and physical system timing"
        ),
    )
    multi_fpga_compile.add_argument("--timing-paths", type=Path)
    multi_fpga_compile.add_argument("--router")
    multi_fpga_compile.add_argument("--frame-slots", type=int)
    multi_fpga_compile.add_argument(
        "--optimize-frame-slots",
        action="store_true",
        help=(
            "treat --frame-slots as an upper bound and search for the "
            "minimum independently feasible route/TDM frame"
        ),
    )
    multi_fpga_compile.add_argument("--route-max-iterations", type=int)
    multi_fpga_compile.add_argument(
        "--tdm-provider",
        choices=(
            TDM_RATIO_PROVIDER,
            TDM_TIMING_DAG_RATIO_PROVIDER,
            TDM_BASELINE_PROVIDER,
        ),
    )
    multi_fpga_compile.add_argument("--ratio-optimizer")
    multi_fpga_compile.add_argument("--timing-dag-optimizer")
    multi_fpga_compile.add_argument("--slot-optimizer")
    multi_fpga_compile.add_argument(
        "--ratio-max-iterations", type=int, default=500
    )
    multi_fpga_compile.add_argument("--max-ratio", type=int)
    multi_fpga_compile.add_argument(
        "--ratio-quantum", type=int, default=8
    )
    multi_fpga_compile.add_argument(
        "--post-refinement-iterations", type=int, default=200
    )
    multi_fpga_compile.add_argument(
        "--slot-refinement-iterations", type=int, default=200
    )
    multi_fpga_compile.add_argument(
        "--cross-stage-iterations",
        type=int,
        default=0,
        help=(
            "run checked Phase 3--5 TDM-feedback optimization and continue "
            "its selected candidate through split, physical, and runtime"
        ),
    )
    multi_fpga_compile.add_argument("--cross-stage-feedback-optimizer")
    multi_fpga_compile.add_argument(
        "--cross-stage-pair-pressure-weight", type=float, default=1.0
    )
    multi_fpga_compile.add_argument("--simulation-frames", type=int, default=16)
    multi_fpga_compile.add_argument("--equivalence-cycles", type=int, default=16)
    multi_fpga_compile.add_argument(
        "--equivalence-seed", type=int, default=20260727
    )
    multi_fpga_compile.add_argument(
        "--physical",
        action="store_true",
        help=(
            "continue every Phase-6 partition through the selected physical "
            "backend and the common physical-QoR gate"
        ),
    )
    multi_fpga_compile.add_argument(
        "--physical-backend",
        choices=("open", "vivado"),
        default="open",
        help="select the provider behind the common physical/timing contract",
    )
    multi_fpga_compile.add_argument("--physical-architecture", type=Path)
    multi_fpga_compile.add_argument(
        "--physical-architecture-id", default="vtr-flagship-k6-n10-40nm"
    )
    multi_fpga_compile.add_argument("--physical-vpr")
    multi_fpga_compile.add_argument("--physical-architecture-importer")
    multi_fpga_compile.add_argument("--physical-packed-importer")
    multi_fpga_compile.add_argument("--physical-route-checker")
    multi_fpga_compile.add_argument("--physical-openparf-install", type=Path)
    multi_fpga_compile.add_argument("--physical-openparf-python", type=Path)
    multi_fpga_compile.add_argument("--physical-seed", type=int, default=1)
    multi_fpga_compile.add_argument(
        "--physical-route-channel-width", type=int, default=300
    )
    multi_fpga_compile.add_argument("--physical-vivado")
    multi_fpga_compile.add_argument(
        "--physical-vivado-max-timing-paths", type=int, default=10000
    )
    multi_fpga_compile.add_argument(
        "--physical-vivado-place-directive", default="Default"
    )
    multi_fpga_compile.add_argument(
        "--physical-vivado-route-directive", default="Default"
    )
    multi_fpga_compile.add_argument(
        "--serial-bsp-phy-provider",
        type=Path,
        help=(
            "continue the completed compile through serial hardware-BSP "
            "generation using this provider manifest"
        ),
    )
    multi_fpga_compile.add_argument(
        "--serial-bsp-runtime-sync-provider", type=Path
    )
    multi_fpga_compile.add_argument("--serial-bsp-board-overlay", type=Path)
    multi_fpga_compile.add_argument("--serial-bsp-gt-site-map", type=Path)
    multi_fpga_compile.add_argument("--serial-bsp-vivado", type=Path)
    multi_fpga_compile.add_argument("--serial-bsp-yosys", type=Path)
    multi_fpga_compile.add_argument("--serial-bsp-runtime-sync-root")
    multi_fpga_compile.add_argument(
        "--serial-bsp-ready-stable-cycles", type=int, default=4
    )
    multi_fpga_physical = multi_fpga_subparsers.add_parser(
        "physical",
        help=(
            "implement every Phase-6 partition through the selected backend "
            "and emit checked common physical timing"
        ),
    )
    multi_fpga_physical.add_argument("--split", type=Path, required=True)
    multi_fpga_physical.add_argument("--platform", type=Path, required=True)
    multi_fpga_physical.add_argument("--schedule", type=Path, required=True)
    multi_fpga_physical.add_argument("--out", type=Path, required=True)
    multi_fpga_physical.add_argument(
        "--original-ir",
        type=Path,
        help="original EmuIR used to map routed DUT timing endpoints",
    )
    multi_fpga_physical.add_argument(
        "--assignment",
        type=Path,
        help="Phase 3 assignment used to reconstruct DUT timing paths",
    )
    multi_fpga_physical.add_argument(
        "--routes",
        type=Path,
        help="Phase 4 routes used to reconstruct DUT timing paths",
    )
    multi_fpga_physical.add_argument(
        "--path-database",
        type=Path,
        help="pre-partition STA path database with endpoint identity",
    )
    multi_fpga_physical.add_argument(
        "--backend", choices=("open", "vivado"), default="open"
    )
    multi_fpga_physical.add_argument("--architecture", type=Path)
    multi_fpga_physical.add_argument(
        "--architecture-id", default="vtr-flagship-k6-n10-40nm"
    )
    multi_fpga_physical.add_argument("--yosys")
    multi_fpga_physical.add_argument("--vpr")
    multi_fpga_physical.add_argument("--architecture-importer")
    multi_fpga_physical.add_argument("--packed-importer")
    multi_fpga_physical.add_argument("--route-checker")
    multi_fpga_physical.add_argument("--openparf-install", type=Path)
    multi_fpga_physical.add_argument("--openparf-python", type=Path)
    multi_fpga_physical.add_argument("--seed", type=int, default=1)
    multi_fpga_physical.add_argument(
        "--route-channel-width", type=int, default=300
    )
    multi_fpga_physical.add_argument("--vivado")
    multi_fpga_physical.add_argument(
        "--vivado-max-timing-paths", type=int, default=10000
    )
    multi_fpga_physical.add_argument(
        "--vivado-place-directive", default="Default"
    )
    multi_fpga_physical.add_argument(
        "--vivado-route-directive", default="Default"
    )
    multi_fpga_bsp = multi_fpga_subparsers.add_parser(
        "bsp",
        help=(
            "continue a completed compile through serial Phase 6B/6C, "
            "runtime synchronization, and checked PHY elaboration"
        ),
    )
    multi_fpga_bsp.add_argument("--flow", type=Path, required=True)
    multi_fpga_bsp.add_argument("--platform", type=Path, required=True)
    multi_fpga_bsp.add_argument("--phy-provider", type=Path, required=True)
    multi_fpga_bsp.add_argument(
        "--runtime-sync-provider", type=Path, required=True
    )
    multi_fpga_bsp.add_argument("--board-overlay", type=Path)
    multi_fpga_bsp.add_argument("--gt-site-map", type=Path)
    multi_fpga_bsp.add_argument("--vivado", type=Path)
    multi_fpga_bsp.add_argument("--yosys", type=Path)
    multi_fpga_bsp.add_argument("--runtime-sync-root")
    multi_fpga_bsp.add_argument(
        "--ready-stable-cycles", type=int, default=4
    )
    multi_fpga_bsp.add_argument("--out", type=Path, required=True)
    multi_fpga_board = multi_fpga_subparsers.add_parser(
        "board-implement",
        help=(
            "place and route each Vivado DUT+transport partition together "
            "with its source-bound serial BSP"
        ),
    )
    multi_fpga_board.add_argument("--flow", type=Path, required=True)
    multi_fpga_board.add_argument("--bsp", type=Path, required=True)
    multi_fpga_board.add_argument("--platform", type=Path, required=True)
    multi_fpga_board.add_argument("--phy-provider", type=Path, required=True)
    multi_fpga_board.add_argument("--vivado", type=Path, required=True)
    multi_fpga_board.add_argument("--place-directive", default="Default")
    multi_fpga_board.add_argument("--route-directive", default="Default")
    multi_fpga_board.add_argument("--write-bitstream", action="store_true")
    multi_fpga_board.add_argument("--out", type=Path, required=True)
    multi_fpga_board_timing = multi_fpga_subparsers.add_parser(
        "board-timing",
        help=(
            "export routed board-checkpoint logic/boundary timing and "
            "rebuild unified Phase 7C timing"
        ),
    )
    multi_fpga_board_timing.add_argument("--flow", type=Path, required=True)
    multi_fpga_board_timing.add_argument("--board", type=Path, required=True)
    multi_fpga_board_timing.add_argument("--platform", type=Path, required=True)
    multi_fpga_board_timing.add_argument("--vivado", type=Path, required=True)
    multi_fpga_board_timing.add_argument(
        "--hierarchy-prefix", default="mapped_partition"
    )
    multi_fpga_board_timing.add_argument("--workers", type=int, default=3)
    multi_fpga_board_timing.add_argument("--resume", action="store_true")
    multi_fpga_board_timing.add_argument("--link-timing-db", type=Path)
    multi_fpga_board_timing.add_argument("--out", type=Path, required=True)
    vpr_run = vpr_subparsers.add_parser(
        "run",
        help="run exact VPR pack, baseline place, route, and analysis",
    )
    vpr_run.add_argument("--architecture", type=Path, required=True)
    vpr_run.add_argument("--circuit", type=Path, required=True)
    vpr_run.add_argument("--out", type=Path, required=True)
    vpr_run.add_argument(
        "--vpr",
        help="explicit comparison override; defaults to the in-tree build",
    )
    vpr_run.add_argument("--seed", type=int, default=1)
    vpr_run.add_argument("--route-channel-width", type=int, default=300)
    vpr_import_packed = vpr_subparsers.add_parser(
        "import-packed",
        help="import VPR .net packing decisions into the versioned contract",
    )
    vpr_import_packed.add_argument("--input", type=Path, required=True)
    vpr_import_packed.add_argument("--output", "-o", type=Path, required=True)
    vpr_import_packed.add_argument("--architecture", type=Path)
    vpr_import_packed.add_argument("--circuit", type=Path)
    vpr_import_packed.add_argument(
        "--importer",
        help="explicit comparison override; defaults to the in-tree build",
    )
    vpr_validate_packed = vpr_subparsers.add_parser(
        "validate-packed",
        help="independently validate a VPR packed-netlist contract",
    )
    vpr_validate_packed.add_argument("--input", type=Path, required=True)
    vpr_validate_packed.add_argument("--architecture", type=Path)
    vpr_validate_packed.add_argument("--circuit", type=Path)
    vpr_place_openparf = vpr_subparsers.add_parser(
        "place-openparf",
        help="place VPR packed clusters with root-built OpenPARF",
    )
    vpr_place_openparf.add_argument("--packed", type=Path, required=True)
    vpr_place_openparf.add_argument(
        "--architecture-db", type=Path, required=True
    )
    vpr_place_openparf.add_argument("--seed-placement", type=Path)
    vpr_place_openparf.add_argument("--out", type=Path, required=True)
    vpr_place_openparf.add_argument("--openparf-install", type=Path)
    vpr_place_openparf.add_argument("--openparf-python", type=Path)
    vpr_route_packed = vpr_subparsers.add_parser(
        "route-packed",
        help="route a packed netlist and OpenPARF VPR placement",
    )
    vpr_route_packed.add_argument("--architecture", type=Path, required=True)
    vpr_route_packed.add_argument("--circuit", type=Path, required=True)
    vpr_route_packed.add_argument("--packed-netlist", type=Path, required=True)
    vpr_route_packed.add_argument(
        "--packed-contract", type=Path, required=True
    )
    vpr_route_packed.add_argument("--placement", type=Path, required=True)
    vpr_route_packed.add_argument("--out", type=Path, required=True)
    vpr_route_packed.add_argument(
        "--vpr",
        help="explicit comparison override; defaults to the in-tree build",
    )
    vpr_route_packed.add_argument(
        "--route-channel-width", type=int, default=300
    )
    vpr_route_packed.add_argument("--route-checker")
    vpr_validate_route = vpr_subparsers.add_parser(
        "validate-route",
        help="independently check VPR route and RR-graph artifacts",
    )
    vpr_validate_route.add_argument("--route", type=Path, required=True)
    vpr_validate_route.add_argument("--rr-graph", type=Path, required=True)
    vpr_validate_route.add_argument(
        "--packed-contract", type=Path, required=True
    )
    vpr_validate_route.add_argument("--placement", type=Path, required=True)
    vpr_validate_route.add_argument("--output", "-o", type=Path, required=True)
    vpr_validate_route.add_argument("--checker")

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
        "arch", help="provider-neutral ArchitectureDB operations"
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
    arch_import_fpgaif = arch_subparsers.add_parser(
        "import-fpga-interchange",
        help="import open FPGA Interchange DeviceResources",
    )
    arch_import_fpgaif.add_argument("input", type=Path)
    arch_import_fpgaif.add_argument("--part", required=True)
    arch_import_fpgaif.add_argument(
        "--generator",
        required=True,
        help="declared producer and version of the DeviceResources input",
    )
    arch_import_fpgaif.add_argument(
        "--output", "-o", type=Path, required=True
    )
    arch_import_fpgaif.add_argument(
        "--native",
        help="explicit comparison override; defaults to the in-tree build",
    )
    arch_import_fpgaif.add_argument("--log", type=Path)
    arch_import_vtr = arch_subparsers.add_parser(
        "import-vtr",
        help="import an open VTR academic architecture XML",
    )
    arch_import_vtr.add_argument("input", type=Path)
    arch_import_vtr.add_argument("--architecture-id", required=True)
    arch_import_vtr.add_argument("--width", type=int)
    arch_import_vtr.add_argument("--height", type=int)
    arch_import_vtr.add_argument(
        "--reference-placement",
        type=Path,
        help="derive exact auto-layout dimensions from a VPR .place file",
    )
    arch_import_vtr.add_argument(
        "--architecture-output", type=Path, required=True
    )
    arch_import_vtr.add_argument(
        "--timing-output", type=Path, required=True
    )
    arch_import_vtr.add_argument("--source-url")
    arch_import_vtr.add_argument(
        "--native",
        help="explicit comparison override; defaults to the in-tree build",
    )
    arch_fetch_vtr = arch_subparsers.add_parser(
        "fetch-default-vtr",
        help="fetch and verify the pinned open VTR flagship architecture",
    )
    arch_fetch_vtr.add_argument("--output", "-o", type=Path, required=True)
    arch_validate_vtr = arch_subparsers.add_parser(
        "validate-vtr",
        help="validate a VTR-sourced ArchitectureDB",
    )
    arch_validate_vtr.add_argument("path", type=Path)
    arch_validate_vtr_timing = arch_subparsers.add_parser(
        "validate-vtr-timing",
        help="validate a VTR academic TimingDB",
    )
    arch_validate_vtr_timing.add_argument("path", type=Path)
    arch_validate_fpgaif = arch_subparsers.add_parser(
        "validate-fpga-interchange",
        help="independently validate FPGA Interchange ArchitectureDB metadata",
    )
    arch_validate_fpgaif.add_argument("path", type=Path)
    arch_capacity_fpgaif = arch_subparsers.add_parser(
        "check-capacity",
        help="check EmuIR primitive support and BEL capacity",
    )
    arch_capacity_fpgaif.add_argument("--arch", type=Path, required=True)
    arch_capacity_fpgaif.add_argument("--ir", type=Path, required=True)
    arch_merge_regions = arch_subparsers.add_parser(
        "merge-physical-regions",
        help="merge a source-qualified physical-region sidecar",
    )
    arch_merge_regions.add_argument("--arch", type=Path, required=True)
    arch_merge_regions.add_argument("--sidecar", type=Path, required=True)
    arch_merge_regions.add_argument("--output", "-o", type=Path, required=True)
    arch_validate_regions = arch_subparsers.add_parser(
        "validate-physical-regions",
        help="validate merged SLR, clock-region, and I/O-bank metadata",
    )
    arch_validate_regions.add_argument("path", type=Path)

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
            "evaluate consecutive deterministic seeds and select the "
            "lowest independently legal weighted-cut objective"
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
    sta_net_map = sta_subparsers.add_parser(
        "emit-vivado-net-map",
        help="map every stable EmuIR net to its mapped-Verilog name",
    )
    sta_net_map.add_argument("--ir", type=Path, required=True)
    sta_net_map.add_argument("--output", "-o", type=Path, required=True)
    sta_database_import = sta_subparsers.add_parser(
        "import-vivado-path-database",
        help="import export_timing_path_database.tcl output",
    )
    sta_database_import.add_argument("--input", type=Path, required=True)
    sta_database_import.add_argument("--ir", type=Path, required=True)
    sta_database_import.add_argument(
        "--output", "-o", type=Path, required=True
    )
    sta_database_project = sta_subparsers.add_parser(
        "project-path-database",
        help="project partition-independent paths onto candidate cut nets",
    )
    sta_database_project.add_argument(
        "--database", type=Path, required=True
    )
    sta_database_project.add_argument(
        "--assignment", type=Path, required=True
    )
    sta_database_project.add_argument(
        "--output", "-o", type=Path, required=True
    )
    sta_database_validate = sta_subparsers.add_parser(
        "validate-path-database",
        help="independently validate a partition-independent path database",
    )
    sta_database_validate.add_argument(
        "--database", type=Path, required=True
    )
    sta_database_validate.add_argument("--ir", type=Path, required=True)
    sta_weights = sta_subparsers.add_parser(
        "derive-partition-net-weights",
        help="derive timing-driven hyperedge weights from OpenSTA paths",
    )
    sta_weights.add_argument("--database", type=Path, required=True)
    sta_weights.add_argument("--ir", type=Path, required=True)
    sta_weights.add_argument("--output", "-o", type=Path, required=True)
    sta_weights.add_argument("--criticality-scale", type=float, default=9.0)
    sta_weights.add_argument(
        "--criticality-exponent", type=float, default=2.0
    )
    sta_opensta = sta_subparsers.add_parser(
        "run-opensta",
        help="build a partition-independent path database with in-tree OpenSTA",
    )
    sta_opensta.add_argument("--ir", type=Path, required=True)
    sta_opensta.add_argument("--output", "-o", type=Path, required=True)
    sta_opensta.add_argument(
        "--clock-period",
        action="append",
        default=[],
        metavar="CLOCK=PERIOD_NS",
    )
    sta_opensta.add_argument(
        "--timing-model",
        type=Path,
        default=DEFAULT_TIMING_MODEL,
    )
    sta_opensta.add_argument(
        "--architecture-timing-db",
        type=Path,
        help=(
            "public VTR TimingDB; generates a design-specialized "
            "pre-placement OpenSTA model"
        ),
    )
    sta_opensta.add_argument(
        "--opensta",
        "--openroad",
        dest="opensta",
        help="explicit comparison override; defaults to the in-tree build",
    )
    sta_opensta.add_argument("--max-paths", type=int, default=200000)
    sta_opensta.add_argument("--log", type=Path)

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
            NATIVE_ROUTER_PROVIDER,
            TLR_PROVIDER,
            ROUTE_TDM_PROVIDER,
        ],
        default=None,
        help=(
            f"defaults to {ROUTE_TDM_PROVIDER} when --timing-paths is "
            f"supplied, otherwise {NATIVE_ROUTER_PROVIDER}"
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
        choices=(
            TDM_RATIO_PROVIDER,
            TDM_TIMING_DAG_RATIO_PROVIDER,
            TDM_BASELINE_PROVIDER,
        ),
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
    phase5.add_argument(
        "--timing-dag-optimizer",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "emuflow_tdm_timing_dag_optimizer build"
        ),
    )
    phase5.add_argument("--ratio-max-iterations", type=int, default=500)
    phase5.add_argument(
        "--slot-optimizer",
        help=(
            "explicit comparison override; defaults to the in-tree "
            "emuflow_tdm_slot_optimizer build"
        ),
    )
    phase5.add_argument("--max-ratio", type=int)
    phase5.add_argument("--ratio-quantum", type=int, default=8)
    phase5.add_argument(
        "--post-refinement-iterations", type=int, default=200
    )
    phase5.add_argument(
        "--slot-refinement-iterations", type=int, default=200
    )
    phase5.add_argument("--ratio-convergence", type=float, default=1.0e-9)

    partition_feedback = subparsers.add_parser(
        "partition-feedback",
        help="derive channel-usage partition weights from routed TDM results",
    )
    partition_feedback.add_argument("--routes", type=Path, required=True)
    partition_feedback.add_argument("--ratio-plan", type=Path, required=True)
    partition_feedback.add_argument("--platform", type=Path, required=True)
    partition_feedback.add_argument("--output", "-o", type=Path, required=True)
    partition_feedback.add_argument("--optimizer")
    partition_feedback.add_argument(
        "--pair-pressure-weight", type=float, default=1.0
    )

    cross_stage = subparsers.add_parser(
        "cross-stage",
        help="checked Phase 3--5 feedback optimization operations",
    )
    cross_stage_subparsers = cross_stage.add_subparsers(
        dest="cross_stage_command", required=True
    )
    cross_stage_evaluate = cross_stage_subparsers.add_parser(
        "evaluate", help="score one partition/route/schedule candidate"
    )
    cross_stage_evaluate.add_argument(
        "--database", type=Path, required=True
    )
    cross_stage_evaluate.add_argument(
        "--assignment", type=Path, required=True
    )
    cross_stage_evaluate.add_argument("--routes", type=Path, required=True)
    cross_stage_evaluate.add_argument("--schedule", type=Path, required=True)
    cross_stage_evaluate.add_argument(
        "--ratio-plan", type=Path, required=True
    )
    cross_stage_evaluate.add_argument(
        "--platform", type=Path, required=True
    )
    cross_stage_evaluate.add_argument(
        "--output", "-o", type=Path, required=True
    )
    cross_stage_validate = cross_stage_subparsers.add_parser(
        "validate-candidate",
        help="independently reconstruct one candidate score",
    )
    cross_stage_validate.add_argument("candidate", type=Path)
    cross_stage_validate.add_argument(
        "--database", type=Path, required=True
    )
    cross_stage_validate.add_argument(
        "--assignment", type=Path, required=True
    )
    cross_stage_validate.add_argument(
        "--routes", type=Path, required=True
    )
    cross_stage_validate.add_argument(
        "--schedule", type=Path, required=True
    )
    cross_stage_validate.add_argument(
        "--ratio-plan", type=Path, required=True
    )
    cross_stage_validate.add_argument(
        "--platform", type=Path, required=True
    )
    cross_stage_report_validate = cross_stage_subparsers.add_parser(
        "validate-report",
        help="independently reconstruct all successful candidates",
    )
    cross_stage_report_validate.add_argument("report", type=Path)
    cross_stage_report_validate.add_argument(
        "--ir", type=Path, required=True
    )
    cross_stage_report_validate.add_argument(
        "--database", type=Path, required=True
    )
    cross_stage_report_validate.add_argument(
        "--platform", type=Path, required=True
    )
    cross_stage_optimize = cross_stage_subparsers.add_parser(
        "optimize",
        help="iterate TDM feedback through partition, routing, and scheduling",
    )
    cross_stage_optimize.add_argument("--ir", type=Path, required=True)
    cross_stage_optimize.add_argument(
        "--platform", type=Path, required=True
    )
    cross_stage_optimize.add_argument(
        "--database", type=Path, required=True
    )
    cross_stage_optimize.add_argument(
        "--initial-assignment", type=Path, required=True
    )
    cross_stage_optimize.add_argument("--out", type=Path, required=True)
    cross_stage_optimize.add_argument(
        "--phase3-constraints", type=Path
    )
    cross_stage_optimize.add_argument(
        "--route-constraints", type=Path
    )
    cross_stage_optimize.add_argument(
        "--board-link-timing-db",
        type=Path,
        help=(
            "apply direction-exact BoardLinkTimingDB bounds to every "
            "routing, TDM, and feedback candidate"
        ),
    )
    cross_stage_optimize.add_argument(
        "--phase3-provider",
        choices=("repart-replication", "repart", "tritonpart"),
        default="repart-replication",
    )
    cross_stage_optimize.add_argument(
        "--max-outer-iterations", type=int, default=1
    )
    cross_stage_optimize.add_argument("--seed", type=int, default=0)
    cross_stage_optimize.add_argument("--min-used-fpgas", type=int)
    cross_stage_optimize.add_argument(
        "--balance-tolerance", type=float
    )
    cross_stage_optimize.add_argument("--openroad")
    cross_stage_optimize.add_argument("--repart")
    cross_stage_optimize.add_argument(
        "--partition-timeout-seconds", type=int, default=3600
    )
    cross_stage_optimize.add_argument(
        "--partition-seed-attempts", type=int, default=1
    )
    cross_stage_optimize.add_argument(
        "--partition-repair-min-used-fpgas", action="store_true"
    )
    cross_stage_optimize.add_argument(
        "--partition-repair-balance", action="store_true"
    )
    cross_stage_optimize.add_argument("--router")
    cross_stage_optimize.add_argument("--frame-slots", type=int)
    cross_stage_optimize.add_argument(
        "--optimize-frame-slots",
        action="store_true",
        help=(
            "treat --frame-slots as an upper bound and minimize the exact "
            "feasible frame for every partition candidate"
        ),
    )
    cross_stage_optimize.add_argument(
        "--route-max-iterations", type=int
    )
    cross_stage_optimize.add_argument(
        "--tdm-provider",
        choices=(
            TDM_RATIO_PROVIDER,
            TDM_TIMING_DAG_RATIO_PROVIDER,
            TDM_BASELINE_PROVIDER,
        ),
    )
    cross_stage_optimize.add_argument("--ratio-optimizer")
    cross_stage_optimize.add_argument("--timing-dag-optimizer")
    cross_stage_optimize.add_argument("--slot-optimizer")
    cross_stage_optimize.add_argument("--feedback-optimizer")
    cross_stage_optimize.add_argument(
        "--simulation-frames", type=int, default=4
    )
    cross_stage_optimize.add_argument(
        "--ratio-max-iterations", type=int, default=500
    )
    cross_stage_optimize.add_argument("--max-ratio", type=int)
    cross_stage_optimize.add_argument(
        "--ratio-quantum", type=int, default=8
    )
    cross_stage_optimize.add_argument(
        "--post-refinement-iterations", type=int, default=200
    )
    cross_stage_optimize.add_argument(
        "--slot-refinement-iterations", type=int, default=200
    )
    cross_stage_optimize.add_argument(
        "--ratio-convergence", type=float, default=1.0e-9
    )
    cross_stage_optimize.add_argument(
        "--pair-pressure-weight", type=float, default=1.0
    )
    cross_stage_optimize.add_argument(
        "--feedback-step",
        type=float,
        action="append",
        default=[],
        help=(
            "strictly decreasing proximal line-search step in (0,1]; "
            "repeat to override the default 1,0.5,0.25,0.125 sequence"
        ),
    )

    pin_plan_parser = subparsers.add_parser(
        "pin-plan",
        help="placement-aware TDM grouping and virtual pin planning",
    )
    pin_plan_subparsers = pin_plan_parser.add_subparsers(
        dest="pin_plan_command", required=True
    )
    pin_plan_build = pin_plan_subparsers.add_parser(
        "build", help="build a plan from OpenPARF lookahead placements"
    )
    pin_plan_build.add_argument("--ir", type=Path, required=True)
    pin_plan_build.add_argument("--schedule", type=Path, required=True)
    pin_plan_build.add_argument("--platform", type=Path, required=True)
    pin_plan_build.add_argument(
        "--placement",
        action="append",
        default=[],
        required=True,
        metavar="FPGA=PATH",
    )
    pin_plan_build.add_argument("--positions-out", type=Path, required=True)
    pin_plan_build.add_argument("--output", "-o", type=Path, required=True)
    pin_plan_build.add_argument("--planner")
    pin_plan_build.add_argument("--region-count", type=int, default=3)
    pin_plan_build.add_argument(
        "--refinement-iterations", type=int, default=100
    )
    pin_plan_build.add_argument("--crossing-weight", type=float, default=1.0)
    pin_plan_build.add_argument("--position-weight", type=float, default=1.0)
    pin_plan_validate = pin_plan_subparsers.add_parser(
        "validate", help="independently validate a pin plan"
    )
    pin_plan_validate.add_argument("plan", type=Path)
    pin_plan_validate.add_argument("--schedule", type=Path, required=True)
    pin_plan_validate.add_argument("--platform", type=Path, required=True)
    pin_plan_validate.add_argument("--positions", type=Path, required=True)

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
    split_validate.add_argument("--pin-plan", type=Path)
    split_validate.add_argument("--position-hints", type=Path)

    phase6 = subparsers.add_parser(
        "phase6",
        help="split EmuIR and bind cut signals to logical TDM lanes",
    )
    phase6.add_argument("--ir", type=Path, required=True)
    phase6.add_argument("--assignment", type=Path, required=True)
    phase6.add_argument("--schedule", type=Path, required=True)
    phase6.add_argument("--platform", type=Path, required=True)
    phase6.add_argument("--out", type=Path, required=True)
    phase6.add_argument("--pin-plan", type=Path)
    phase6.add_argument("--position-hints", type=Path)
    phase6.add_argument("--equivalence-cycles", type=int, default=16)
    phase6.add_argument("--equivalence-seed", type=int, default=20260727)

    phase6b = subparsers.add_parser(
        "phase6b",
        help="bind virtual link anchors to electrical BSP package pins",
    )
    phase6b.add_argument("--schedule", type=Path, required=True)
    phase6b.add_argument("--platform", type=Path, required=True)
    phase6b.add_argument(
        "--position-hints",
        type=Path,
        help="required with --pin-plan or for a parallel-I/O BSP",
    )
    phase6b.add_argument(
        "--pin-plan",
        type=Path,
        help="required with --position-hints or for a parallel-I/O BSP",
    )
    phase6b.add_argument(
        "--anchor", action="append", default=[], metavar="FPGA=PATH"
    )
    phase6b.add_argument(
        "--bsp",
        type=Path,
        help=(
            "parallel-I/O hardware BSP; omit for source-backed serial "
            "endpoint bindings embedded in BoardDB"
        ),
    )
    phase6b.add_argument("--solver")
    phase6b.add_argument("--iostandard", default="LVCMOS18")
    phase6b.add_argument("--placement-weight", type=float, default=1.0)
    phase6b.add_argument("--skew-weight", type=float, default=1.0)
    phase6b.add_argument("--out", type=Path, required=True)

    phase6c = subparsers.add_parser(
        "phase6c",
        help="generate serial-PHY wrapper RTL and its unresolved provider contract",
    )
    phase6c.add_argument("--platform", type=Path, required=True)
    phase6c.add_argument("--binding", type=Path, required=True)
    phase6c.add_argument(
        "--transport", action="append", default=[], metavar="FPGA=PATH"
    )
    phase6c.add_argument(
        "--phy-provider",
        type=Path,
        help="source-visible or materialized vendor serial PHY provider manifest",
    )
    phase6c.add_argument(
        "--board-overlay",
        type=Path,
        help="validated board-specific GT site, reference-clock, and reset bindings",
    )
    phase6c.add_argument(
        "--gt-site-map",
        type=Path,
        help="Vivado-derived package-pin to GT channel site map",
    )
    phase6c.add_argument(
        "--runtime-sync-topology",
        type=Path,
        help="validated rooted-tree runtime synchronization topology",
    )
    phase6c.add_argument(
        "--runtime-sync-provider",
        type=Path,
        help="source-visible runtime synchronization provider manifest",
    )
    phase6c.add_argument("--out", type=Path, required=True)

    package_pin = subparsers.add_parser(
        "package-pin",
        help="physical package-pin binding artifact operations",
    )
    package_pin_subparsers = package_pin.add_subparsers(
        dest="package_pin_command", required=True
    )
    package_pin_validate = package_pin_subparsers.add_parser(
        "validate",
        help="independently validate a package-pin binding",
    )
    package_pin_validate.add_argument("binding", type=Path)
    package_pin_validate.add_argument("--schedule", type=Path, required=True)
    package_pin_validate.add_argument("--platform", type=Path, required=True)
    package_pin_validate.add_argument("--position-hints", type=Path)
    package_pin_validate.add_argument("--pin-plan", type=Path)
    package_pin_validate.add_argument(
        "--anchor", action="append", default=[], metavar="FPGA=PATH"
    )
    package_pin_validate.add_argument("--bsp", type=Path)

    lower = subparsers.add_parser(
        "lower-placement-ir",
        help="merge one partition with its synthesized transport EmuIR",
    )
    lower.add_argument("--netlist", type=Path, required=True)
    lower.add_argument("--transport", type=Path, required=True)
    lower.add_argument("--transport-ir", type=Path, required=True)
    lower.add_argument("--output", "-o", type=Path, required=True)
    lower.add_argument("--report", type=Path)
    lower.add_argument(
        "--boundary-identities",
        type=Path,
        help="provider-neutral physical boundary identity database",
    )

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
    phase7c.add_argument(
        "--routes",
        type=Path,
        help="Phase 4 routes.json required for unified physical timing",
    )
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
    if args.command == "archive":
        if args.archive_command == "create":
            report = create_validation_archive(
                args.flow,
                args.out,
                run_id=args.run_id,
                source_commit=args.source_commit,
                max_copy_bytes=args.max_copy_bytes,
                tool_versions=_keyed_values(
                    args.tool_version, "--tool-version"
                ),
            )
        elif args.archive_command == "validate":
            report = validate_validation_archive(args.archive)
        else:
            report = cleanup_validation_source(args.archive, args.flow)
        _print_json(report)
        return 0

    if args.command == "platform":
        if args.platform_command == "arm-mps4-materialize":
            report = materialize_arm_mps4_boarddb(
                output_path=args.output,
                name=args.name,
                fabric_clock_mhz=args.fabric_clock_mhz,
                payload_bits_per_lane_per_cycle=(
                    args.payload_bits_per_lane_per_cycle
                ),
                latency_cycles=args.latency_cycles,
                utilization_limit=args.utilization_limit,
            )
            _print_json(report)
            return 0
        if args.platform_command == "overlay-validate":
            report = validate_board_support_overlay_file(
                platform_path=args.platform,
                overlay_path=args.overlay,
                normalized_out=args.normalized_out,
            )
            _print_json(report)
            return 0
        if args.platform_command == "vivado-derive-gt-sites":
            report = derive_vivado_pin_sites(
                platform_path=args.platform,
                vivado_executable=args.vivado,
                output_dir=args.out,
            )
            _print_json(report)
            return 0
        if args.platform_command == "link-timing-model":
            platform = Platform.load(args.platform)
            database = build_board_link_timing_model(platform)
            write_json(args.output, database)
            report = validate_board_link_timing(database, platform)
            report["output"] = str(args.output)
            _print_json(report)
            return 0
        if args.platform_command == "link-timing-validate":
            platform = Platform.load(args.platform)
            database = read_json(args.input)
            _print_json(validate_board_link_timing(database, platform))
            return 0
        platform = Platform.load(args.path)
        if args.normalized_out is not None:
            write_json(args.normalized_out, platform.to_dict())
        _print_json(platform.summary())
        return 0

    if args.command == "phy-provider":
        if args.phy_provider_command == "materialize-recipe":
            report = materialize_serial_phy_recipe(
                manifest_path=args.manifest,
                part=args.part,
                vivado_executable=args.vivado,
                output_dir=args.out,
                platform_path=args.platform,
            )
            _print_json(report)
            return 0
        if args.phy_provider_command == "elaborate":
            report = run_serial_phy_elaboration(
                platform_path=args.platform,
                provider_manifest_path=args.manifest,
                phase6c_dir=args.phase6c_dir,
                runtime_controller_path=args.runtime_controller,
                transport_rtl_paths=_keyed_paths(
                    args.transport, "--transport"
                ),
                yosys_executable=args.yosys,
                vivado_executable=args.vivado,
                output_dir=args.out,
            )
            _print_json(report)
            return 0
        report = validate_serial_phy_provider_file(
            manifest_path=args.manifest,
            platform_path=args.platform,
            normalized_out=args.normalized_out,
        )
        _print_json(report)
        return 0

    if args.command == "runtime-sync":
        if args.runtime_sync_command == "validate-provider":
            report = validate_runtime_sync_provider(
                read_json(args.provider), args.provider
            )
            report = {key: value for key, value in report.items() if key != "normalized"}
        else:
            report = run_runtime_sync_materialization(
                platform_path=args.platform,
                provider_path=args.provider,
                output_dir=args.out,
                root=args.root,
                ready_stable_cycles=args.ready_stable_cycles,
            )
        _print_json(report)
        return 0

    if args.command == "contest":
        if args.contest_command == "eda2023-import":
            report = import_eda2023_case(
                case_dir=args.case_dir,
                output_dir=args.out,
                name=args.name,
            )
        elif args.contest_command == "eda2023-materialize-boarddb":
            report = materialize_eda2023_rtl_boarddb(
                instance_path=args.instance,
                device_template_path=args.device_template,
                output_path=args.output,
                name=args.name,
                template_fpga_id=args.template_fpga,
                lane_scale=args.lane_scale,
                fabric_clock_mhz=args.fabric_clock_mhz,
                latency_cycles=args.latency_cycles,
                link_mode=args.link_mode,
                route_constraints_path=args.route_constraints_output,
            )
        elif args.contest_command == "eda2023-optimize":
            report = optimize_eda2023_tdm(
                instance_path=args.instance,
                routes_path=args.routes,
                output_dir=args.out,
                optimizer=args.optimizer,
                max_iterations=args.max_iterations,
                post_refinement_iterations=args.post_refinement_iterations,
                exact_domain_limit=args.exact_domain_limit,
            )
        elif args.contest_command == "eda2023-evaluate":
            report = evaluate_eda2023_solution(
                instance_path=args.instance,
                routes_path=args.routes,
                tdm_plan_path=args.tdm_plan,
            )
            if args.output is not None:
                write_json(args.output, report)
        elif args.contest_command == "eda2024-evaluate":
            solution = args.solution or args.case_dir / "design.fpga.out"
            report = evaluate_eda2024_solution(
                info_path=args.case_dir / "design.info",
                area_path=args.case_dir / "design.are",
                net_path=args.case_dir / "design.net",
                topology_path=args.case_dir / "design.topo",
                solution_path=solution,
                runtime_seconds=args.runtime_seconds,
                output_path=args.output,
            )
        elif args.contest_command == "eda2024-materialize-boarddb":
            report = materialize_eda2024_rtl_boarddb(
                case_dir=args.case_dir,
                device_template_path=args.device_template,
                output_path=args.output,
                name=args.name,
                lanes_per_edge=args.lanes_per_edge,
                template_fpga_id=args.template_fpga,
                fabric_clock_mhz=args.fabric_clock_mhz,
                latency_cycles=args.latency_cycles,
                link_mode=args.link_mode,
                route_constraints_path=args.route_constraints_output,
            )
        elif args.contest_command == "iccad2019-import":
            report = import_iccad2019_instance(
                input_path=args.input,
                output_dir=args.out,
                name=args.name,
            )
        elif args.contest_command == "iccad2019-materialize-boarddb":
            report = materialize_iccad2019_rtl_boarddb(
                instance_path=args.instance,
                device_template_path=args.device_template,
                output_path=args.output,
                name=args.name,
                template_fpga_id=args.template_fpga,
                lane_scale=args.lane_scale,
                fabric_clock_mhz=args.fabric_clock_mhz,
                latency_cycles=args.latency_cycles,
                link_mode=args.link_mode,
            )
        elif args.contest_command == "iccad2019-optimize":
            report = optimize_iccad2019_ratios(
                instance_path=args.instance,
                routes_path=args.routes,
                output_path=args.output,
                optimizer=args.optimizer,
                max_iterations=args.max_iterations,
                post_refinement_iterations=args.post_refinement_iterations,
            )
        elif args.contest_command == "iccad2019-evaluate":
            report = evaluate_iccad2019_solution(
                instance_path=args.instance,
                solution_path=args.solution,
                runtime_seconds=args.runtime_seconds,
                median_runtime_seconds=args.median_runtime_seconds,
            )
        elif args.contest_command == "eda2025-import":
            report = import_eda2025_instance(
                info_path=args.info,
                net_path=args.net,
                topology_path=args.topology,
                assignment_path=args.assignment,
                output_dir=args.out,
                name=args.name,
                alpha_ns=args.alpha_ns,
                beta_ns=args.beta_ns,
                ratio_quantum=args.ratio_quantum,
                max_ratio=args.max_ratio,
                topology_change_fraction=args.topology_change_fraction,
            )
        elif args.contest_command == "eda2025-evaluate":
            report = evaluate_eda2025_routes(
                instance_path=args.instance,
                routes_path=args.routes,
                output_path=args.output,
                new_topology_path=args.new_topology,
                runtime_seconds=args.runtime_seconds,
                official_output_dir=args.official_out,
            )
        elif args.contest_command == "eda2025-optimize-topology":
            report = optimize_eda2025_topology(
                instance_path=args.instance,
                routes_path=args.routes,
                output_dir=args.out,
                executable=args.optimizer,
                max_changes=args.max_changes,
                current_topology_path=args.topology,
                enable_shortcuts=args.enable_shortcuts,
            )
        elif args.contest_command == "eda2025-optimize-routing":
            report = optimize_eda2025_routing(
                instance_path=args.instance,
                routes_path=args.routes,
                output_dir=args.out,
                router=args.router,
                topology_optimizer=args.topology_optimizer,
                current_topology_path=args.topology,
                enable_shortcut_portfolio=not args.capacity_only,
                max_rounds=args.max_rounds,
            )
        elif args.contest_command == "eda2025-materialize-boarddb":
            report = materialize_eda2025_rtl_boarddb(
                instance_path=args.instance,
                device_template_path=args.device_template,
                output_path=args.output,
                name=args.name,
                topology_path=args.topology,
                template_fpga_id=args.template_fpga,
                lane_scale=args.lane_scale,
                fabric_clock_mhz=args.fabric_clock_mhz,
                latency_cycles=args.latency_cycles,
                link_mode=args.link_mode,
                route_constraints_path=args.route_constraints_output,
            )
        else:
            raise AssertionError(f"unhandled contest command {args.contest_command!r}")
        _print_json(report)
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

    if args.command == "vpr":
        if args.vpr_command in {"fpga-open", "full-open"}:
            report = run_open_physical_flow(
                sources=args.sources,
                top=args.top,
                output_dir=args.out,
                architecture=args.architecture,
                architecture_id=args.architecture_id,
                hard_blocks=not args.logic_only,
                yosys=args.yosys,
                vpr=args.vpr,
                architecture_importer=args.architecture_importer,
                packed_importer=args.packed_importer,
                route_checker=args.route_checker,
                openparf_install=args.openparf_install,
                openparf_python=args.openparf_python,
                seed=args.seed,
                route_channel_width=args.route_channel_width,
            )
        elif args.vpr_command == "synth":
            report = run_vtr_yosys(
                sources=args.sources,
                top=args.top,
                output=args.output,
                executable=args.yosys,
                log_path=args.log,
                hard_blocks=args.hard_blocks,
            )
        elif args.vpr_command == "run":
            report = run_vpr(
                architecture=args.architecture,
                circuit=args.circuit,
                output_dir=args.out,
                executable=args.vpr,
                seed=args.seed,
                route_channel_width=args.route_channel_width,
            )
        elif args.vpr_command == "import-packed":
            report = run_packed_netlist_import(
                packed_netlist_path=args.input,
                output_path=args.output,
                architecture_path=args.architecture,
                circuit_path=args.circuit,
                executable=args.importer,
            )
        elif args.vpr_command == "validate-packed":
            report = validate_packed_netlist_file(
                args.input,
                architecture_path=args.architecture,
                circuit_path=args.circuit,
            )
        elif args.vpr_command == "place-openparf":
            report = run_packed_openparf_placement(
                args.packed,
                args.architecture_db,
                args.out,
                seed_placement_path=args.seed_placement,
                openparf_install=args.openparf_install,
                openparf_python=args.openparf_python,
            )
        elif args.vpr_command == "route-packed":
            report = run_vpr_route_packed(
                architecture=args.architecture,
                circuit=args.circuit,
                packed_netlist=args.packed_netlist,
                packed_contract=args.packed_contract,
                placement=args.placement,
                output_dir=args.out,
                executable=args.vpr,
                route_checker=args.route_checker,
                route_channel_width=args.route_channel_width,
            )
        else:
            report = validate_vpr_route_artifacts(
                args.route,
                args.rr_graph,
                args.packed_contract,
                args.placement,
                args.output,
                executable=args.checker,
            )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

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
            report = architecture.summary()
        elif args.arch_command == "import-fpga-interchange":
            report = run_fpga_interchange_architecture_import(
                input_path=args.input,
                part=args.part,
                generator=args.generator,
                output_path=args.output,
                executable=args.native,
                log_path=args.log,
            )
        elif args.arch_command == "import-vtr":
            if args.reference_placement is not None:
                if args.width is not None or args.height is not None:
                    raise EmuFlowError(
                        "--reference-placement cannot be combined with "
                        "--width or --height"
                    )
                width, height = read_vpr_placement_dimensions(
                    args.reference_placement
                )
            elif args.width is None or args.height is None:
                raise EmuFlowError(
                    "import-vtr requires either --reference-placement or "
                    "both --width and --height"
                )
            else:
                width, height = args.width, args.height
            report = run_vtr_architecture_import(
                input_path=args.input,
                architecture_output_path=args.architecture_output,
                timing_output_path=args.timing_output,
                architecture_id=args.architecture_id,
                width=width,
                height=height,
                source_url=args.source_url,
                executable=args.native,
            )
        elif args.arch_command == "fetch-default-vtr":
            report = fetch_pinned_vtr_architecture(args.output)
        elif args.arch_command == "validate-vtr":
            report = validate_vtr_architecture_db(
                ArchitectureDB.load(args.path)
            )
        elif args.arch_command == "validate-vtr-timing":
            report = validate_vtr_timing_db_file(args.path)
        elif args.arch_command == "validate-fpga-interchange":
            architecture = ArchitectureDB.load(args.path)
            report = validate_fpga_interchange_architecture(architecture)
        elif args.arch_command == "check-capacity":
            architecture = ArchitectureDB.load(args.arch)
            report = check_ir_architecture_capacity(
                architecture, EmuIR.load(args.ir)
            )
        elif args.arch_command == "merge-physical-regions":
            report = run_physical_region_merge(
                architecture_path=args.arch,
                sidecar_path=args.sidecar,
                output_path=args.output,
            )
        elif args.arch_command == "validate-physical-regions":
            report = validate_fpga_interchange_architecture_regions(
                ArchitectureDB.load(args.path)
            )
        else:
            architecture = ArchitectureDB.load(args.path)
            report = architecture.summary()
        _print_json(report)
        return 0 if report.get("status", "pass") == "pass" else 2

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

    if args.command == "multi-fpga":
        if args.multi_fpga_command == "board-timing":
            report = run_vivado_board_timing(
                flow_root=args.flow,
                board_root=args.board,
                platform_path=args.platform,
                vivado_executable=args.vivado,
                output_dir=args.out,
                hierarchy_prefix=args.hierarchy_prefix,
                workers=args.workers,
                resume=args.resume,
                link_timing_path=args.link_timing_db,
            )
            _print_json(report["summary"])
            return 0
        if args.multi_fpga_command == "board-implement":
            report = run_vivado_board_flow(
                flow_root=args.flow,
                bsp_root=args.bsp,
                platform_path=args.platform,
                phy_provider_path=args.phy_provider,
                vivado_executable=args.vivado,
                output_dir=args.out,
                place_directive=args.place_directive,
                route_directive=args.route_directive,
                write_bitstream=args.write_bitstream,
            )
            _print_json(report["summary"])
            return 0
        if args.multi_fpga_command == "bsp":
            report = run_multi_fpga_bsp_flow(
                flow_root=args.flow,
                platform_path=args.platform,
                phy_provider_path=args.phy_provider,
                runtime_sync_provider_path=args.runtime_sync_provider,
                output_dir=args.out,
                board_overlay_path=args.board_overlay,
                gt_site_map_path=args.gt_site_map,
                vivado_executable=args.vivado,
                yosys_executable=args.yosys,
                runtime_sync_root=args.runtime_sync_root,
                ready_stable_cycles=args.ready_stable_cycles,
            )
            _print_json(report["summary"])
            return 0
        if args.multi_fpga_command == "physical":
            report = run_multi_fpga_physical_flow(
                split_root=args.split,
                platform_path=args.platform,
                schedule_path=args.schedule,
                output_dir=args.out,
                backend=args.backend,
                architecture=args.architecture,
                architecture_id=args.architecture_id,
                yosys=args.yosys,
                vpr=args.vpr,
                architecture_importer=args.architecture_importer,
                packed_importer=args.packed_importer,
                route_checker=args.route_checker,
                openparf_install=args.openparf_install,
                openparf_python=args.openparf_python,
                seed=args.seed,
                route_channel_width=args.route_channel_width,
                vivado=args.vivado,
                vivado_max_timing_paths=args.vivado_max_timing_paths,
                vivado_place_directive=args.vivado_place_directive,
                vivado_route_directive=args.vivado_route_directive,
                original_ir_path=args.original_ir,
                assignment_path=args.assignment,
                routes_path=args.routes,
                path_database_path=args.path_database,
            )
            _print_json(report["summary"])
            return 0
        if args.archive_cleanup and args.archive_out is None:
            raise EmuFlowError("--archive-cleanup requires --archive-out")
        report = run_multi_fpga_flow(
            platform_path=args.platform,
            output_dir=args.out,
            sources=args.sources,
            top=args.top,
            clocks=args.clock,
            yosys_json=args.yosys_json,
            yosys=args.yosys,
            mapping_profile=args.mapping_profile,
            partition_constraints=args.partition_constraints,
            partition_provider=args.partition_provider,
            seed=args.seed,
            min_used_fpgas=args.min_used_fpgas,
            balance_tolerance=args.balance_tolerance,
            openroad=args.openroad,
            repart=args.repart,
            partition_timeout_seconds=args.partition_timeout_seconds,
            partition_seed_attempts=args.partition_seed_attempts,
            partition_repair_min_used_fpgas=(
                args.partition_repair_min_used_fpgas
            ),
            partition_repair_balance=args.partition_repair_balance,
            timing_driven=args.timing_driven,
            timing_backend=args.timing_backend,
            clock_periods=(
                parse_clock_definitions(args.clock_period)
                if args.clock_period
                else None
            ),
            timing_model=args.timing_model,
            architecture_timing_db=args.architecture_timing_db,
            opensta=args.opensta,
            timing_vivado=args.timing_vivado,
            sta_max_paths=args.sta_max_paths,
            timing_criticality_scale=args.timing_criticality_scale,
            timing_criticality_exponent=(
                args.timing_criticality_exponent
            ),
            route_constraints=args.route_constraints,
            board_link_timing_db=args.board_link_timing_db,
            timing_paths=args.timing_paths,
            router=args.router,
            frame_slots=args.frame_slots,
            optimize_frame_slots=args.optimize_frame_slots,
            route_max_iterations=args.route_max_iterations,
            tdm_provider=args.tdm_provider,
            ratio_optimizer=args.ratio_optimizer,
            timing_dag_optimizer=args.timing_dag_optimizer,
            slot_optimizer=args.slot_optimizer,
            ratio_max_iterations=args.ratio_max_iterations,
            max_ratio=args.max_ratio,
            ratio_quantum=args.ratio_quantum,
            post_refinement_iterations=(
                args.post_refinement_iterations
            ),
            slot_refinement_iterations=args.slot_refinement_iterations,
            cross_stage_iterations=args.cross_stage_iterations,
            cross_stage_feedback_optimizer=(
                args.cross_stage_feedback_optimizer
            ),
            cross_stage_pair_pressure_weight=(
                args.cross_stage_pair_pressure_weight
            ),
            simulation_frames=args.simulation_frames,
            equivalence_cycles=args.equivalence_cycles,
            equivalence_seed=args.equivalence_seed,
            physical=args.physical,
            physical_backend=args.physical_backend,
            physical_architecture=args.physical_architecture,
            physical_architecture_id=args.physical_architecture_id,
            physical_vpr=args.physical_vpr,
            physical_architecture_importer=(
                args.physical_architecture_importer
            ),
            physical_packed_importer=args.physical_packed_importer,
            physical_route_checker=args.physical_route_checker,
            physical_openparf_install=args.physical_openparf_install,
            physical_openparf_python=args.physical_openparf_python,
            physical_seed=args.physical_seed,
            physical_route_channel_width=(
                args.physical_route_channel_width
            ),
            physical_vivado=args.physical_vivado,
            physical_vivado_max_timing_paths=(
                args.physical_vivado_max_timing_paths
            ),
            physical_vivado_place_directive=(
                args.physical_vivado_place_directive
            ),
            physical_vivado_route_directive=(
                args.physical_vivado_route_directive
            ),
            serial_bsp_phy_provider=args.serial_bsp_phy_provider,
            serial_bsp_runtime_sync_provider=(
                args.serial_bsp_runtime_sync_provider
            ),
            serial_bsp_board_overlay=args.serial_bsp_board_overlay,
            serial_bsp_gt_site_map=args.serial_bsp_gt_site_map,
            serial_bsp_vivado=args.serial_bsp_vivado,
            serial_bsp_yosys=args.serial_bsp_yosys,
            serial_bsp_runtime_sync_root=(
                args.serial_bsp_runtime_sync_root
            ),
            serial_bsp_ready_stable_cycles=(
                args.serial_bsp_ready_stable_cycles
            ),
        )
        if args.archive_out is not None:
            archive_report = create_validation_archive(
                args.out,
                args.archive_out,
                run_id=args.archive_run_id or args.out.resolve().name,
                source_commit=args.archive_source_commit,
                max_copy_bytes=args.archive_max_copy_bytes,
                tool_versions=_keyed_values(
                    args.archive_tool_version, "--archive-tool-version"
                ),
                run_configuration=_jsonable_cli_configuration(args),
            )
            result: Dict[str, Any] = {
                "flow": report,
                "archive": archive_report,
            }
            if args.archive_cleanup:
                result["cleanup"] = cleanup_validation_source(
                    args.archive_out, args.out
                )
            _print_json(result)
        else:
            _print_json(report)
        return 0 if report["status"] == "pass" else 2

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
        elif args.sta_command == "import-vivado-tsv":
            report = import_vivado_sta_tsv(
                args.input, args.assignment, args.output
            )
        elif args.sta_command == "emit-vivado-net-map":
            report = write_vivado_net_map(args.ir, args.output)
        elif args.sta_command == "import-vivado-path-database":
            report = import_vivado_path_database_tsv(
                args.input, args.ir, args.output
            )
        elif args.sta_command == "project-path-database":
            report = project_sta_path_database(
                args.database, args.assignment, args.output
            )
        elif args.sta_command == "validate-path-database":
            report = validate_sta_path_database(args.database, args.ir)
        elif args.sta_command == "derive-partition-net-weights":
            report = derive_partition_net_weights(
                args.database,
                args.ir,
                args.output,
                criticality_scale=args.criticality_scale,
                criticality_exponent=args.criticality_exponent,
            )
        else:
            clock_definitions = parse_clock_definitions(
                args.clock_period
            )
            report = run_opensta_path_database(
                ir_path=args.ir,
                output_path=args.output,
                clocks=clock_definitions or None,
                timing_model_path=args.timing_model,
                architecture_timing_db_path=args.architecture_timing_db,
                executable=args.opensta,
                max_paths=args.max_paths,
                log_path=args.log,
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
            timing_dag_optimizer=args.timing_dag_optimizer,
            slot_optimizer=args.slot_optimizer,
            ratio_max_iterations=args.ratio_max_iterations,
            max_ratio=args.max_ratio,
            ratio_quantum=args.ratio_quantum,
            post_refinement_iterations=args.post_refinement_iterations,
            slot_refinement_iterations=args.slot_refinement_iterations,
            convergence=args.ratio_convergence,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "partition-feedback":
        report = run_partition_feedback(
            routes_path=args.routes,
            ratio_plan_path=args.ratio_plan,
            platform_path=args.platform,
            output_path=args.output,
            executable=args.optimizer,
            pair_pressure_weight=args.pair_pressure_weight,
        )
        _print_json(report)
        return 0

    if args.command == "cross-stage":
        if args.cross_stage_command == "evaluate":
            report = evaluate_cross_stage_candidate(
                args.database,
                args.assignment,
                args.routes,
                args.schedule,
                args.ratio_plan,
                args.platform,
                args.output,
            )
        elif args.cross_stage_command == "validate-candidate":
            report = validate_cross_stage_candidate(
                args.candidate,
                args.database,
                args.assignment,
                args.routes,
                args.schedule,
                args.ratio_plan,
                args.platform,
            )
        elif args.cross_stage_command == "validate-report":
            report = validate_cross_stage_report(
                args.report,
                args.ir,
                args.database,
                args.platform,
            )
        else:
            report = run_cross_stage_optimization(
                ir_path=args.ir,
                platform_path=args.platform,
                database_path=args.database,
                initial_assignment_path=args.initial_assignment,
                output_dir=args.out,
                phase3_constraints_path=args.phase3_constraints,
                route_constraints_path=args.route_constraints,
                board_link_timing_path=args.board_link_timing_db,
                phase3_provider=args.phase3_provider,
                max_outer_iterations=args.max_outer_iterations,
                seed=args.seed,
                min_used_fpgas=args.min_used_fpgas,
                balance_tolerance=args.balance_tolerance,
                openroad=args.openroad,
                repart=args.repart,
                partition_timeout_seconds=(
                    args.partition_timeout_seconds
                ),
                partition_seed_attempts=args.partition_seed_attempts,
                partition_repair_min_used_fpgas=(
                    args.partition_repair_min_used_fpgas
                ),
                partition_repair_balance=(
                    args.partition_repair_balance
                ),
                router=args.router,
                frame_slots=args.frame_slots,
                optimize_frame_slots=args.optimize_frame_slots,
                route_max_iterations=args.route_max_iterations,
                tdm_provider=args.tdm_provider,
                ratio_optimizer=args.ratio_optimizer,
                timing_dag_optimizer=args.timing_dag_optimizer,
                slot_optimizer=args.slot_optimizer,
                feedback_optimizer=args.feedback_optimizer,
                simulation_frames=args.simulation_frames,
                ratio_max_iterations=args.ratio_max_iterations,
                max_ratio=args.max_ratio,
                ratio_quantum=args.ratio_quantum,
                post_refinement_iterations=(
                    args.post_refinement_iterations
                ),
                slot_refinement_iterations=(
                    args.slot_refinement_iterations
                ),
                ratio_convergence=args.ratio_convergence,
                pair_pressure_weight=args.pair_pressure_weight,
                feedback_steps=(
                    tuple(args.feedback_step)
                    if args.feedback_step
                    else None
                ),
            )
        _print_json(report)
        return 0

    if args.command == "pin-plan":
        schedule = read_json(args.schedule)
        platform = Platform.load(args.platform)
        if args.pin_plan_command == "build":
            ir = EmuIR.load(args.ir)
            placements = {
                fpga: read_json(path)
                for fpga, path in _keyed_paths(
                    args.placement, "--placement"
                ).items()
            }
            positions = build_signal_position_hints(
                ir.value,
                schedule,
                placements,
                region_count=args.region_count,
            )
            plan = build_pin_plan(
                schedule,
                platform,
                positions,
                executable=args.planner,
                refinement_iterations=args.refinement_iterations,
                crossing_weight=args.crossing_weight,
                position_weight=args.position_weight,
            )
            write_json(args.positions_out, positions)
            write_json(args.output, plan)
            _print_json(
                {
                    "status": "pass",
                    "positions": positions["metrics"],
                    "plan": plan["metrics"],
                }
            )
        else:
            report = validate_pin_plan(
                schedule,
                platform,
                read_json(args.positions),
                read_json(args.plan),
            )
            _print_json(report)
        return 0

    if args.command == "split":
        report = validate_phase6(
            ir_path=args.ir,
            assignment_path=args.assignment,
            schedule_path=args.schedule,
            platform_path=args.platform,
            manifest_path=args.manifest,
            pin_plan_path=args.pin_plan,
            position_hints_path=args.position_hints,
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
            pin_plan_path=args.pin_plan,
            position_hints_path=args.position_hints,
            equivalence_cycles=args.equivalence_cycles,
            equivalence_seed=args.equivalence_seed,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "phase6b":
        report = run_phase6b(
            schedule_path=args.schedule,
            platform_path=args.platform,
            positions_path=args.position_hints,
            pin_plan_path=args.pin_plan,
            anchor_paths=_keyed_paths(args.anchor, "--anchor"),
            bsp_path=args.bsp,
            output_dir=args.out,
            executable=args.solver,
            iostandard=args.iostandard,
            placement_weight=args.placement_weight,
            skew_weight=args.skew_weight,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "phase6c":
        report = run_phase6c(
            platform_path=args.platform,
            binding_path=args.binding,
            output_dir=args.out,
            transport_paths=(
                _keyed_paths(args.transport, "--transport")
                if args.transport
                else None
            ),
            board_overlay_path=args.board_overlay,
            phy_provider_path=args.phy_provider,
            gt_site_map_path=args.gt_site_map,
            runtime_sync_topology_path=args.runtime_sync_topology,
            runtime_sync_provider_path=args.runtime_sync_provider,
        )
        _print_json(report)
        return 0 if report["status"] == "pass" else 2

    if args.command == "package-pin":
        platform = Platform.load(args.platform)
        schedule = read_json(args.schedule)
        positions = (
            read_json(args.position_hints)
            if args.position_hints is not None
            else None
        )
        plan = read_json(args.pin_plan) if args.pin_plan is not None else None
        anchors = {
            fpga: read_json(path)
            for fpga, path in _keyed_paths(
                args.anchor, "--anchor"
            ).items()
        }
        binding = read_json(args.binding)
        if binding.get("provider") == SERIAL_TRANSCEIVER_PROVIDER:
            if args.bsp is not None:
                parser.error(
                    "--bsp must be omitted for a BoardDB serial binding"
                )
            report = validate_serial_transceiver_binding(
                schedule, platform, positions, plan, anchors, binding
            )
        else:
            if args.bsp is None or positions is None or plan is None:
                parser.error(
                    "--bsp, --position-hints, and --pin-plan are required "
                    "for a parallel package-pin binding"
                )
            report = validate_package_pin_binding(
                schedule,
                platform,
                positions,
                plan,
                anchors,
                read_json(args.bsp),
                binding,
            )
        _print_json(report)
        return 0

    if args.command == "lower-placement-ir":
        report = run_placement_ir_lowering(
            netlist_path=args.netlist,
            transport_path=args.transport,
            transport_ir_path=args.transport_ir,
            output_path=args.output,
            report_path=args.report,
            boundary_identity_path=args.boundary_identities,
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
            routes_path=args.routes,
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
