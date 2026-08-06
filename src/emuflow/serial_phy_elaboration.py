"""Tool-backed elaboration gate for generated shells and serial PHY sources."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .platform import Platform
from .serial_phy_provider import validate_serial_phy_provider


SERIAL_PHY_ELABORATION_SCHEMA = "emuflow.serial-phy-elaboration/v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yosys_quote(path: Path) -> str:
    value = str(path)
    if any(character in value for character in " \t\n\"\\"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _sv_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"n_{name}" if not name or name[0].isdigit() else name


def build_yosys_elaboration_script(
    sources: Sequence[Path], top: str
) -> str:
    if not sources:
        raise ValidationError("serial PHY elaboration source list is empty")
    if not isinstance(top, str) or not top:
        raise ValidationError("serial PHY elaboration top is invalid")
    return "; ".join(
        [
            "read_verilog -sv " + " ".join(_yosys_quote(path) for path in sources),
            f"hierarchy -check -top {top}",
            "check -assert",
            "stat",
        ]
    )


def _tcl_quote(value: str) -> str:
    return "{" + value.replace("\\", "/").replace("}", "\\}") + "}"


def build_vivado_elaboration_tcl(
    sources: Sequence[Path],
    top: str,
    part: str,
    utilization_report: Path,
    primitive_contract: Optional[Mapping[str, str]] = None,
    expected_channel_primitives: int = 0,
    expected_reference_clock_primitives: int = 0,
    constraint_sources: Sequence[Path] = (),
    expected_channel_locs: Sequence[str] = (),
    expected_common_primitives: int = 0,
    expected_common_locs: Sequence[str] = (),
    expected_channel_cells: Sequence[str] = (),
    expected_common_cells: Sequence[str] = (),
    expected_runtime_sync_primitives: int = 0,
) -> str:
    if not sources:
        raise ValidationError("serial PHY elaboration source list is empty")
    if not top or not part:
        raise ValidationError("Vivado elaboration top/part is invalid")
    if constraint_sources and primitive_contract is None:
        raise ValidationError("GT constraints require a primitive contract")
    if expected_channel_locs and (
        primitive_contract is None
        or len(expected_channel_locs) != expected_channel_primitives
    ):
        raise ValidationError("expected GT LOC inventory is inconsistent")
    if expected_common_locs and (
        primitive_contract is None
        or len(expected_common_locs) != expected_common_primitives
        or not primitive_contract.get("common_primitive")
    ):
        raise ValidationError("expected GT common LOC inventory is inconsistent")
    if (
        constraint_sources
        and len(expected_channel_cells) != expected_channel_primitives
    ):
        raise ValidationError("expected GT channel hierarchy inventory is inconsistent")
    if (
        constraint_sources
        and len(expected_common_cells) != expected_common_primitives
    ):
        raise ValidationError("expected GT common hierarchy inventory is inconsistent")
    source_list = " ".join(_tcl_quote(str(path)) for path in sources)
    lines = [
            "create_project -in_memory -part " + _tcl_quote(part) + " emuflow_phy",
            "foreach source [list " + source_list + "] {",
            "  read_verilog -sv $source",
            "}",
            "set_property top " + _tcl_quote(top) + " [current_fileset]",
            "synth_design -mode out_of_context -flatten_hierarchy none -top "
            + _tcl_quote(top)
            + " -part "
            + _tcl_quote(part),
            "set black_boxes [get_cells -quiet -hier -filter {IS_BLACKBOX == 1}]",
            "if {[llength $black_boxes] != 0} {",
            "  puts stderr \"EMUFLOW_PHY_ELAB black_boxes=$black_boxes\"",
            "  exit 3",
            "}",
            "set runtime_sync_primitives [get_cells -quiet -hier -filter "
            + _tcl_quote("NAME == runtime_sync || NAME =~ */runtime_sync")
            + "]",
    ]
    if primitive_contract is not None:
        channel_primitive = primitive_contract["channel_primitive"]
        refclk_primitive = primitive_contract["reference_clock_primitive"]
        lines.extend(
            [
                "set channel_primitives [get_cells -quiet -hier -filter "
                + _tcl_quote(f"REF_NAME == {channel_primitive}")
                + "]",
                "set refclk_primitives [get_cells -quiet -hier -filter "
                + _tcl_quote(f"REF_NAME == {refclk_primitive}")
                + "]",
            ]
        )
        common_primitive = primitive_contract.get("common_primitive")
        if common_primitive:
            lines.append(
                "set common_primitives [get_cells -quiet -hier -filter "
                + _tcl_quote(f"REF_NAME == {common_primitive}")
                + "]"
            )
        else:
            lines.append("set common_primitives [list]")
    else:
        lines.extend(
            [
                "set channel_primitives [list]",
                "set refclk_primitives [list]",
                "set common_primitives [list]",
            ]
        )
    lines.extend(
        [
            "set report_handle [open "
            + _tcl_quote(str(utilization_report))
            + " w]",
            "puts $report_handle \"top=" + top + "\"",
            "puts $report_handle \"part=" + part + "\"",
            "puts $report_handle \"cells=[llength [get_cells -hier]]\"",
            "puts $report_handle \"black_boxes=[llength $black_boxes]\"",
            "puts $report_handle \"channel_primitives=[llength $channel_primitives]\"",
            "puts $report_handle \"channel_cells=$channel_primitives\"",
            "puts $report_handle \"reference_clock_primitives=[llength $refclk_primitives]\"",
            "puts $report_handle \"reference_clock_cells=$refclk_primitives\"",
            "puts $report_handle \"common_primitives=[llength $common_primitives]\"",
            "puts $report_handle \"common_cells=$common_primitives\"",
            "puts $report_handle \"runtime_sync_primitives=[llength $runtime_sync_primitives]\"",
            "puts $report_handle \"runtime_sync_cells=$runtime_sync_primitives\"",
            "close $report_handle",
            "if {[llength $runtime_sync_primitives] != "
            + str(expected_runtime_sync_primitives)
            + "} {",
            "  puts stderr \"EMUFLOW_PHY_ELAB runtime_sync_primitive_count="
            "[llength $runtime_sync_primitives] expected="
            + str(expected_runtime_sync_primitives)
            + "\"",
            "  exit 11",
            "}",
        ]
    )
    if primitive_contract is not None:
        lines.extend(
            [
                "if {[llength $channel_primitives] != "
                + str(expected_channel_primitives)
                + "} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB channel_primitive_count="
                "[llength $channel_primitives] expected="
                + str(expected_channel_primitives)
                + "\"",
                "  exit 4",
                "}",
                "if {[llength $refclk_primitives] != "
                + str(expected_reference_clock_primitives)
                + "} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB refclk_primitive_count="
                "[llength $refclk_primitives] expected="
                + str(expected_reference_clock_primitives)
                + "\"",
                "  exit 5",
                "}",
                "if {[llength $common_primitives] != "
                + str(expected_common_primitives)
                + "} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB common_primitive_count="
                "[llength $common_primitives] expected="
                + str(expected_common_primitives)
                + "\"",
                "  exit 7",
                "}",
            ]
        )
    if constraint_sources:
        constraint_list = " ".join(
            _tcl_quote(str(path)) for path in constraint_sources
        )
        expected_locs = " ".join(
            _tcl_quote(location) for location in expected_channel_locs
        )
        expected_common = " ".join(
            _tcl_quote(location) for location in expected_common_locs
        )
        expected_channel_hierarchy = " ".join(
            _tcl_quote(cell) for cell in expected_channel_cells
        )
        expected_common_hierarchy = " ".join(
            _tcl_quote(cell) for cell in expected_common_cells
        )
        lines.extend(
            [
                "set actual_channel_cells [lsort $channel_primitives]",
                "set expected_channel_cells [lsort [list "
                + expected_channel_hierarchy
                + "]]",
                "set actual_common_cells [lsort $common_primitives]",
                "set expected_common_cells [lsort [list "
                + expected_common_hierarchy
                + "]]",
                "if {$actual_channel_cells ne $expected_channel_cells} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB channel_cells=$actual_channel_cells "
                "expected=$expected_channel_cells\"",
                "  exit 9",
                "}",
                "if {$actual_common_cells ne $expected_common_cells} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB common_cells=$actual_common_cells "
                "expected=$expected_common_cells\"",
                "  exit 10",
                "}",
                "foreach constraint [list " + constraint_list + "] {",
                "  read_xdc $constraint",
                "}",
                "set actual_channel_locs [list]",
                "foreach channel_cell $channel_primitives {",
                "  lappend actual_channel_locs [get_property LOC $channel_cell]",
                "}",
                "set actual_channel_locs [lsort $actual_channel_locs]",
                "set expected_channel_locs [lsort [list " + expected_locs + "]]",
                "set report_handle [open "
                + _tcl_quote(str(utilization_report))
                + " a]",
                "puts $report_handle \"channel_locs=$actual_channel_locs\"",
                "puts $report_handle \"expected_channel_locs=$expected_channel_locs\"",
                "close $report_handle",
                "if {$actual_channel_locs ne $expected_channel_locs} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB channel_locs=$actual_channel_locs "
                "expected=$expected_channel_locs\"",
                "  exit 6",
                "}",
                "set actual_common_locs [list]",
                "foreach common_cell $common_primitives {",
                "  lappend actual_common_locs [get_property LOC $common_cell]",
                "}",
                "set actual_common_locs [lsort $actual_common_locs]",
                "set expected_common_locs [lsort [list " + expected_common + "]]",
                "set report_handle [open "
                + _tcl_quote(str(utilization_report))
                + " a]",
                "puts $report_handle \"common_locs=$actual_common_locs\"",
                "puts $report_handle \"expected_common_locs=$expected_common_locs\"",
                "close $report_handle",
                "if {$actual_common_locs ne $expected_common_locs} {",
                "  puts stderr \"EMUFLOW_PHY_ELAB common_locs=$actual_common_locs "
                "expected=$expected_common_locs\"",
                "  exit 8",
                "}",
            ]
        )
    lines.extend(
        [
            "puts \"EMUFLOW_PHY_ELAB status=pass top=" + top + " part=" + part + "\"",
            "exit 0",
            "",
        ]
    )
    return "\n".join(lines)


def run_serial_phy_elaboration(
    *,
    platform_path: Path,
    provider_manifest_path: Path,
    phase6c_dir: Path,
    runtime_controller_path: Path,
    transport_rtl_paths: Mapping[str, Path],
    yosys_executable: Optional[Path],
    vivado_executable: Optional[Path],
    output_dir: Path,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    provider_raw = read_json(provider_manifest_path)
    provider_result = validate_serial_phy_provider(
        provider_raw, provider_manifest_path, platform
    )
    provider = provider_result["normalized"]
    manifest_path = phase6c_dir / "serial_wrapper_manifest.json"
    report_path = phase6c_dir / "phase6c_report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise ValidationError("Phase 6C manifest/report is missing")
    manifest = read_json(manifest_path)
    phase6c_report = read_json(report_path)
    if (
        manifest.get("schema") != "emuflow.serial-wrapper/v1"
        or manifest.get("status") != "provider_source_bound"
        or phase6c_report.get("schema") != "emuflow.phase6c-report/v1"
        or phase6c_report.get("status") != "pass"
        or manifest.get("platform") != platform.name
        or phase6c_report.get("platform") != platform.name
        or manifest.get("design") != phase6c_report.get("design")
    ):
        raise ValidationError("Phase 6C provider-bound identity is invalid")
    expected_provider_hash = hashlib.sha256(
        provider_manifest_path.read_bytes()
    ).hexdigest()
    if manifest.get("phy_provider_manifest_sha256") != expected_provider_hash:
        raise ValidationError("Phase 6C/provider manifest hash mismatch")
    bound_provider = manifest.get("phy_contract", {}).get("provider", {})
    if (
        bound_provider.get("status") != "source_inventory_bound"
        or bound_provider.get("id") != provider["id"]
        or bound_provider.get("sources") != provider["sources"]
    ):
        raise ValidationError("Phase 6C provider source inventory mismatch")
    fpga_records = manifest.get("fpgas")
    if not isinstance(fpga_records, list):
        raise ValidationError("Phase 6C FPGA wrapper inventory is invalid")
    fpga_ids = {fpga.id for fpga in platform.fpgas}
    if (
        {record.get("fpga") for record in fpga_records} != fpga_ids
        or set(transport_rtl_paths) != fpga_ids
    ):
        raise ValidationError(
            "provider elaboration must cover every FPGA transport exactly once"
        )
    if not runtime_controller_path.is_file():
        raise ValidationError("runtime controller RTL is missing")
    if (yosys_executable is None) == (vivado_executable is None):
        raise ValidationError("select exactly one elaboration tool")
    executable = (
        yosys_executable if yosys_executable is not None else vivado_executable
    )
    assert executable is not None
    if not executable.is_file():
        raise ValidationError("elaboration executable is missing")

    source_root = (provider_manifest_path.parent / provider["source_root"]).resolve()
    provider_hdl = [
        (source_root / source["path"]).resolve()
        for source in provider["sources"]
        if source["language"] in {"systemverilog", "verilog"}
    ]
    if not provider_hdl:
        raise ValidationError("provider has no HDL source for elaboration")
    runtime_sync_hdl = []
    runtime_sync_record = manifest.get("runtime_sync", {"status": "not_provided"})
    if runtime_sync_record.get("status") == "source_inventory_bound":
        runtime_sync_names = phase6c_report.get("artifacts", {}).get(
            "runtime_sync_rtl"
        )
        if (
            not isinstance(runtime_sync_names, list)
            or not runtime_sync_names
            or any(not isinstance(name, str) for name in runtime_sync_names)
        ):
            raise ValidationError("Phase 6C runtime synchronization RTL is missing")
        for runtime_sync_name in runtime_sync_names:
            relative = Path(runtime_sync_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValidationError("Phase 6C runtime synchronization path is unsafe")
            runtime_sync_path = phase6c_dir / relative
            if not runtime_sync_path.is_file():
                raise ValidationError(
                    "Phase 6C runtime synchronization source is missing"
                )
            runtime_sync_hdl.append(runtime_sync_path)
    open_pcs_hdl = []
    if provider.get("schema") == "emuflow.serial-phy-provider/v3":
        open_pcs_names = phase6c_report.get("artifacts", {}).get("open_pcs_rtl")
        if (
            not isinstance(open_pcs_names, list)
            or not open_pcs_names
            or any(not isinstance(name, str) for name in open_pcs_names)
        ):
            raise ValidationError("Phase 6C open PCS RTL closure is missing")
        for open_pcs_name in open_pcs_names:
            relative = Path(open_pcs_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValidationError("Phase 6C open PCS path is unsafe")
            open_pcs_path = phase6c_dir / relative
            if not open_pcs_path.is_file():
                raise ValidationError("Phase 6C open PCS source is missing")
            open_pcs_hdl.append(open_pcs_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    tool_name = "yosys" if yosys_executable is not None else "vivado"
    version_flag = "-V" if tool_name == "yosys" else "-version"
    version = subprocess.run(
        [str(executable), version_flag],
        check=False,
        capture_output=True,
        text=True,
    )
    if version.returncode != 0:
        raise EmuFlowError(f"failed to query {tool_name} version")
    parts = {fpga.id: fpga.part for fpga in platform.fpgas}
    fpga_results = []
    for record in sorted(fpga_records, key=lambda item: item["fpga"]):
        fpga = record["fpga"]
        wrapper_path = phase6c_dir / record["rtl"]
        shell_name = record.get("integration_shell")
        transport_path = transport_rtl_paths[fpga]
        if (
            not isinstance(shell_name, str)
            or not wrapper_path.is_file()
            or not (phase6c_dir / shell_name).is_file()
            or not transport_path.is_file()
        ):
            raise ValidationError(f"{fpga}: elaboration RTL input is missing")
        shell_path = phase6c_dir / shell_name
        sources = [
            *provider_hdl,
            *runtime_sync_hdl,
            *open_pcs_hdl,
            runtime_controller_path,
            transport_path,
            wrapper_path,
            shell_path,
        ]
        top = f"emuflow_partition_shell_{_sv_name(fpga)}"
        generated_files = []
        if tool_name == "yosys":
            script = build_yosys_elaboration_script(sources, top)
            command = [str(executable), "-q", "-p", script]
        else:
            utilization_path = output_dir / f"{fpga}.vivado.elaboration.rpt"
            script_path = output_dir / f"{fpga}.vivado.tcl"
            primitive_contract = (
                provider["implementation"]
                if provider["implementation"]["kind"]
                == "amd_ultrascale_plus_gty"
                else None
            )
            constraint_sources = []
            expected_channel_locs = []
            expected_common_locs = []
            expected_channel_cells = []
            expected_common_cells = []
            if primitive_contract is not None:
                gt_site_artifacts = phase6c_report.get("artifacts", {}).get(
                    "gt_site_xdc", {}
                )
                gt_site_name = gt_site_artifacts.get(fpga)
                if (
                    record.get("gt_site_constraints_status")
                    != "trusted_emittable"
                    or not isinstance(gt_site_name, str)
                ):
                    raise ValidationError(
                        f"{fpga}: source-backed GT site constraints are missing"
                    )
                gt_site_path = phase6c_dir / gt_site_name
                if not gt_site_path.is_file():
                    raise ValidationError(f"{fpga}: GT site XDC is missing")
                constraint_sources.append(gt_site_path)
                expected_channel_locs = [
                    site["transceiver_site"] for site in record["sites"]
                ]
                if provider.get("schema") in {
                    "emuflow.serial-phy-provider/v2",
                    "emuflow.serial-phy-provider/v3",
                }:
                    expected_common_locs = [
                        quad["common_site"]
                        for quad in record["transceiver_quads"]
                    ]
                    for quad_index, quad in enumerate(
                        record["transceiver_quads"]
                    ):
                        expected_common_cells.append(
                            f"serial_wrapper/quad_{quad_index}_phy/"
                            f"{primitive_contract['common_instance']}"
                        )
                        for channel in quad["channels"]:
                            hierarchy = primitive_contract[
                                "channel_instance_template"
                            ].format(channel=channel["channel_index"])
                            expected_channel_cells.append(
                                f"serial_wrapper/quad_{quad_index}_phy/{hierarchy}"
                            )
                else:
                    expected_channel_cells = [
                        f"serial_wrapper/site_{index}_phy/"
                        f"{primitive_contract['channel_instance']}"
                        for index, _site in enumerate(record["sites"])
                    ]
            script_path.write_text(
                build_vivado_elaboration_tcl(
                    sources,
                    top,
                    parts[fpga],
                    utilization_path,
                    primitive_contract=primitive_contract,
                    expected_channel_primitives=record[
                        "active_transceiver_sites"
                    ],
                    expected_reference_clock_primitives=len(
                        record["board_services"]["clock_reset_domains"]
                    ),
                    constraint_sources=constraint_sources,
                    expected_channel_locs=expected_channel_locs,
                    expected_common_primitives=len(expected_common_locs),
                    expected_common_locs=expected_common_locs,
                    expected_channel_cells=expected_channel_cells,
                    expected_common_cells=expected_common_cells,
                    expected_runtime_sync_primitives=(
                        1 if record.get("runtime_sync") is not None else 0
                    ),
                ),
                encoding="utf-8",
            )
            generated_files.extend((script_path, utilization_path))
            command = [
                str(executable),
                "-mode",
                "batch",
                "-nojournal",
                "-nolog",
                "-source",
                str(script_path),
            ]
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True
        )
        log_path = output_dir / f"{fpga}.{tool_name}.log"
        log_path.write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise EmuFlowError(
                f"{fpga}: {tool_name} provider elaboration failed: {detail[-1000:]}"
            )
        if any(not path.is_file() for path in generated_files):
            raise EmuFlowError(f"{fpga}: {tool_name} omitted an expected report")
        fpga_results.append(
            {
                "fpga": fpga,
                "top": top,
                "status": "pass",
                "sources": [
                    {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
                    for path in sources
                ],
                "constraints": [
                    {
                        "path": str(path),
                        "sha256": _sha256(path),
                    }
                    for path in (
                        constraint_sources if tool_name == "vivado" else []
                    )
                ],
                "log": log_path.name,
                "log_sha256": _sha256(log_path),
                "generated": [
                    {
                        "path": path.name,
                        "sha256": _sha256(path),
                    }
                    for path in generated_files
                ],
            }
        )
    report = {
        "schema": SERIAL_PHY_ELABORATION_SCHEMA,
        "status": "pass",
        "qualification": (
            "open_rtl_elaboration_only"
            if tool_name == "yosys"
            else "vivado_ooc_synthesis_structure_validation"
        ),
        "design": manifest["design"],
        "platform": platform.name,
        "provider": provider["id"],
        "provider_qualification": provider["qualification"],
        "tool": {
            "name": tool_name,
            "version": (version.stdout + version.stderr).strip(),
            "executable": str(executable),
        },
        "phase6c_manifest_sha256": _sha256(manifest_path),
        "provider_manifest_sha256": expected_provider_hash,
        "runtime_controller_sha256": _sha256(runtime_controller_path),
        "fpgas": fpga_results,
        "validation": {
            "fpgas": len(fpga_results),
            "elaboration_failures": 0,
            **({"synthesis_failures": 0} if tool_name == "vivado" else {}),
            "hardware_release_authorized": False,
        },
    }
    write_json(output_dir / "serial_phy_elaboration_report.json", report)
    return report
