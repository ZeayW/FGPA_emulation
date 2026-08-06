"""Validate and materialize source-visible vendor PHY generation recipes."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .platform import Platform


SERIAL_PHY_RECIPE_SCHEMA = "emuflow.serial-phy-recipe/v1"
SERIAL_PHY_RECIPE_REPORT_SCHEMA = "emuflow.serial-phy-recipe-report/v1"


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}: expected a non-empty string")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tcl_quote(value: str) -> str:
    return "{" + value.replace("\\", "/").replace("}", "\\}") + "}"


def validate_serial_phy_recipe(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    platform: Optional[Platform] = None,
) -> Dict[str, Any]:
    if manifest.get("schema") != SERIAL_PHY_RECIPE_SCHEMA:
        raise ValidationError(
            f"serial PHY recipe schema must be {SERIAL_PHY_RECIPE_SCHEMA!r}"
        )
    recipe_id = _string(manifest.get("id"), "recipe.id")
    if manifest.get("qualification") != "vendor_generated_hardware":
        raise ValidationError(
            "serial PHY recipe qualification must be vendor_generated_hardware"
        )
    if manifest.get("generator") != "vivado_gtwizard_ultrascale":
        raise ValidationError("serial PHY recipe generator is unsupported")
    raw_parts = manifest.get("supported_parts")
    if (
        not isinstance(raw_parts, list)
        or not raw_parts
        or any(not isinstance(part, str) or not part for part in raw_parts)
        or len(raw_parts) != len(set(raw_parts))
    ):
        raise ValidationError("serial PHY recipe supported_parts are invalid")
    raw_recipe = manifest.get("recipe")
    if not isinstance(raw_recipe, dict):
        raise ValidationError("serial PHY recipe source record is missing")
    relative = _string(raw_recipe.get("path"), "recipe.path")
    expected_digest = _string(raw_recipe.get("sha256"), "recipe.sha256")
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise ValidationError("recipe.sha256 is invalid")
    root = manifest_path.parent.resolve()
    recipe_path = (root / relative).resolve()
    if root not in recipe_path.parents or not recipe_path.is_file():
        raise ValidationError("serial PHY recipe source escapes or is missing")
    if recipe_path.suffix.lower() != ".tcl":
        raise ValidationError("serial PHY recipe must be editable Tcl")
    actual_digest = _sha256(recipe_path)
    if actual_digest != expected_digest:
        raise ValidationError("serial PHY recipe source SHA-256 mismatch")
    text = recipe_path.read_text(encoding="utf-8")
    if "create_ip" not in text or "gtwizard_ultrascale" not in text:
        raise ValidationError("serial PHY recipe does not create GT Wizard IP")

    raw_ips = manifest.get("expected_ips")
    if (
        not isinstance(raw_ips, list)
        or not raw_ips
        or any(
            not isinstance(ip, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ip) is None
            for ip in raw_ips
        )
        or len(raw_ips) != len(set(raw_ips))
    ):
        raise ValidationError("serial PHY recipe expected_ips are invalid")
    primitives = manifest.get("expected_primitives")
    if not isinstance(primitives, dict):
        raise ValidationError("serial PHY recipe primitive contract is missing")
    channel = _string(primitives.get("channel"), "expected_primitives.channel")
    common = _string(primitives.get("common"), "expected_primitives.common")
    for primitive in (channel, common):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", primitive) is None:
            raise ValidationError("serial PHY recipe primitive name is invalid")
    protocol = manifest.get("protocol")
    if not isinstance(protocol, dict):
        raise ValidationError("serial PHY recipe protocol is missing")
    for field in ("line_rate_gbps_per_lane", "reference_clock_mhz"):
        value = protocol.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or float(value) <= 0
        ):
            raise ValidationError(f"protocol.{field} must be positive")
    encoding = _string(protocol.get("encoding"), "protocol.encoding")
    adapter = None
    provider_template = None
    raw_adapter = manifest.get("adapter")
    raw_provider = manifest.get("provider")
    if (raw_adapter is None) != (raw_provider is None):
        raise ValidationError(
            "serial PHY recipe adapter and provider template must appear together"
        )
    if raw_adapter is not None:
        if not isinstance(raw_adapter, dict) or not isinstance(raw_provider, dict):
            raise ValidationError("serial PHY provider adapter/template is invalid")
        adapter_relative = _string(raw_adapter.get("path"), "adapter.path")
        adapter_digest = _string(raw_adapter.get("sha256"), "adapter.sha256")
        if re.fullmatch(r"[0-9a-f]{64}", adapter_digest) is None:
            raise ValidationError("adapter.sha256 is invalid")
        adapter_path = (root / adapter_relative).resolve()
        if (
            root not in adapter_path.parents
            or not adapter_path.is_file()
            or adapter_path.suffix.lower() not in {".sv", ".v"}
            or _sha256(adapter_path) != adapter_digest
        ):
            raise ValidationError("serial PHY adapter is missing or modified")
        modules = raw_provider.get("modules")
        implementation = raw_provider.get("implementation")
        if (
            raw_provider.get("schema") != "emuflow.serial-phy-provider/v3"
            or not isinstance(modules, dict)
            or not isinstance(implementation, dict)
        ):
            raise ValidationError("serial PHY provider template is incompatible")
        provider_id = _string(raw_provider.get("id"), "provider.id")
        adapter_text = adapter_path.read_text(encoding="utf-8")
        for module in modules.values():
            module_name = _string(module, "provider.modules")
            if re.search(rf"\bmodule\s+{re.escape(module_name)}\b", adapter_text) is None:
                raise ValidationError(
                    f"serial PHY adapter omits provider module {module_name}"
                )
        for ip in raw_ips:
            if re.search(rf"\b{re.escape(ip)}\s+\w+\s*\(", adapter_text) is None:
                raise ValidationError(
                    f"serial PHY adapter omits generated module {ip}"
                )
        required_protocol = {
            "payload_bits_per_lane_per_cycle": int,
            "user_clock_mhz": (int, float),
            "link_training": str,
            "reset_sequence": str,
            "pcs_data_width": int,
            "pcs_header_width": int,
            "pcs_clock_mhz": (int, float),
            "pcs_implementation": str,
        }
        for field, expected_type in required_protocol.items():
            value = protocol.get(field)
            if isinstance(value, bool) or not isinstance(value, expected_type):
                raise ValidationError(f"protocol.{field} is invalid")
        if (
            protocol["pcs_data_width"] != 64
            or protocol["pcs_header_width"] != 2
            or abs(
                float(protocol["pcs_clock_mhz"]) * 66 / 1000
                - float(protocol["line_rate_gbps_per_lane"])
            )
            > 1e-9
        ):
            raise ValidationError("serial PHY recipe PCS boundary is incompatible")
        adapter = {
            "path": adapter_relative,
            "bytes": adapter_path.stat().st_size,
            "sha256": adapter_digest,
        }
        provider_template = {
            "schema": "emuflow.serial-phy-provider/v3",
            "id": provider_id,
            "modules": dict(modules),
            "implementation": dict(implementation),
        }
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("serial PHY recipe provenance is missing")
    license_id = _string(provenance.get("license"), "provenance.license")
    upstream = _string(provenance.get("upstream"), "provenance.upstream")
    revision = _string(provenance.get("revision"), "provenance.revision")
    compatibility = None
    if platform is not None:
        platform_parts = {fpga.part for fpga in platform.fpgas}
        unsupported = sorted(platform_parts - set(raw_parts))
        if unsupported:
            raise ValidationError(
                f"serial PHY recipe does not support platform parts: {unsupported}"
            )
        compatibility = {
            "platform": platform.name,
            "parts": sorted(platform_parts),
            "status": "compatible",
        }
    normalized = {
        "schema": SERIAL_PHY_RECIPE_SCHEMA,
        "id": recipe_id,
        "qualification": "vendor_generated_hardware",
        "generator": "vivado_gtwizard_ultrascale",
        "supported_parts": sorted(raw_parts),
        "recipe": {
            "path": relative,
            "bytes": recipe_path.stat().st_size,
            "sha256": actual_digest,
        },
        "expected_ips": sorted(raw_ips),
        "expected_primitives": {"channel": channel, "common": common},
        "protocol": {
            "line_rate_gbps_per_lane": float(
                protocol["line_rate_gbps_per_lane"]
            ),
            "reference_clock_mhz": float(protocol["reference_clock_mhz"]),
            "encoding": encoding,
            **(
                {
                    "payload_bits_per_lane_per_cycle": protocol[
                        "payload_bits_per_lane_per_cycle"
                    ],
                    "user_clock_mhz": float(protocol["user_clock_mhz"]),
                    "link_training": protocol["link_training"],
                    "reset_sequence": protocol["reset_sequence"],
                    "pcs_data_width": protocol["pcs_data_width"],
                    "pcs_header_width": protocol["pcs_header_width"],
                    "pcs_clock_mhz": float(protocol["pcs_clock_mhz"]),
                    "pcs_implementation": protocol["pcs_implementation"],
                }
                if adapter is not None
                else {}
            ),
        },
        "provenance": {
            "license": license_id,
            "upstream": upstream,
            "revision": revision,
        },
        "open_flow_qualification": {
            "counts_as_open_flow_implementation": False,
            "reason": "Vivado generates vendor-controlled GT Wizard products",
        },
        **({"adapter": adapter, "provider": provider_template} if adapter else {}),
    }
    return {
        "status": "pass",
        "recipe": recipe_id,
        "qualification": "vendor_generated_hardware",
        "compatibility": compatibility,
        "normalized": normalized,
    }


def build_vivado_recipe_tcl(
    *, recipe_path: Path, output_dir: Path, part: str, expected_ips: Sequence[str]
) -> str:
    if not expected_ips:
        raise ValidationError("serial PHY recipe expected IP list is empty")
    expected = " ".join(_tcl_quote(ip) for ip in sorted(expected_ips))
    return "\n".join(
        [
            "create_project -force emuflow_phy_recipe "
            + _tcl_quote(str(output_dir / "vivado-project"))
            + " -part "
            + _tcl_quote(part),
            "source " + _tcl_quote(str(recipe_path)),
            "set actual_ips [lsort [get_ips]]",
            "set expected_ips [lsort [list " + expected + "]]",
            "if {$actual_ips ne $expected_ips} {",
            "  puts stderr \"EMUFLOW_PHY_RECIPE ips=$actual_ips expected=$expected_ips\"",
            "  exit 3",
            "}",
            "generate_target all $actual_ips",
            "foreach ip $actual_ips {",
            "  puts \"EMUFLOW_PHY_RECIPE ip=$ip xci=[get_property IP_FILE $ip]\"",
            "}",
            "puts \"EMUFLOW_PHY_RECIPE status=pass part=" + part + "\"",
            "close_project",
            "exit 0",
            "",
        ]
    )


def materialize_serial_phy_recipe(
    *,
    manifest_path: Path,
    part: str,
    vivado_executable: Path,
    output_dir: Path,
    platform_path: Optional[Path] = None,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path) if platform_path is not None else None
    result = validate_serial_phy_recipe(
        read_json(manifest_path), manifest_path, platform
    )
    recipe = result["normalized"]
    if part not in recipe["supported_parts"]:
        raise ValidationError(f"serial PHY recipe does not support part {part}")
    if not vivado_executable.is_file():
        raise ValidationError("Vivado executable is missing")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValidationError("serial PHY recipe output path is not a directory")
        if any(output_dir.iterdir()):
            raise ValidationError(
                "serial PHY recipe output directory must be empty"
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = (manifest_path.parent / recipe["recipe"]["path"]).resolve()
    script_path = output_dir / "materialize_recipe.tcl"
    script = build_vivado_recipe_tcl(
        recipe_path=recipe_path,
        output_dir=output_dir,
        part=part,
        expected_ips=recipe["expected_ips"],
    )
    script_path.write_text(script, encoding="utf-8")
    version = subprocess.run(
        [str(vivado_executable), "-version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if version.returncode != 0:
        raise EmuFlowError("failed to query Vivado version")
    completed = subprocess.run(
        [
            str(vivado_executable),
            "-mode",
            "batch",
            "-nojournal",
            "-nolog",
            "-source",
            str(script_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    log_path = output_dir / "vivado.log"
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise EmuFlowError(
            "Vivado serial PHY recipe materialization failed: " + detail[-1500:]
        )
    project_dir = output_dir / "vivado-project"
    xci_files = sorted(project_dir.rglob("*.xci"))
    generated_hdl = sorted(
        path for suffix in ("*.v", "*.sv") for path in project_dir.rglob(suffix)
    )
    if len(xci_files) != len(recipe["expected_ips"]) or not generated_hdl:
        raise EmuFlowError("Vivado omitted expected GT Wizard products")
    hdl_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in generated_hdl
    )
    primitive_presence = {
        name: bool(re.search(rf"\b{re.escape(name)}\b", hdl_text))
        for name in recipe["expected_primitives"].values()
    }
    missing = sorted(name for name, present in primitive_presence.items() if not present)
    if missing:
        raise EmuFlowError(
            f"generated GT Wizard HDL omits expected primitives: {missing}"
        )
    xci_records = [
        {
            "path": str(path.relative_to(output_dir)),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in xci_files
    ]
    provider_artifact = None
    if recipe.get("adapter") is not None:
        adapter_source = (
            manifest_path.parent / recipe["adapter"]["path"]
        ).resolve()
        adapter_relative = Path("provider_sources") / adapter_source.name
        adapter_destination = output_dir / adapter_relative
        adapter_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(adapter_source, adapter_destination)
        if _sha256(adapter_destination) != recipe["adapter"]["sha256"]:
            raise EmuFlowError("copied serial PHY adapter digest changed")
        template = recipe["provider"]
        provider_manifest = {
            "schema": template["schema"],
            "id": template["id"],
            "qualification": "vendor_generated_hardware",
            "supported_parts": recipe["supported_parts"],
            "modules": template["modules"],
            "implementation": template["implementation"],
            "source_root": ".",
            "sources": [
                {
                    "path": adapter_relative.as_posix(),
                    "language": (
                        "systemverilog"
                        if adapter_destination.suffix.lower() == ".sv"
                        else "verilog"
                    ),
                    "role": "source_visible_v3_gtwizard_contract_adapter",
                    "sha256": _sha256(adapter_destination),
                }
            ],
            "protocol": {
                "payload_bits_per_lane_per_cycle": recipe["protocol"][
                    "payload_bits_per_lane_per_cycle"
                ],
                "user_clock_mhz": recipe["protocol"]["user_clock_mhz"],
                "line_rate_gbps_per_lane": recipe["protocol"][
                    "line_rate_gbps_per_lane"
                ],
                "encoding": "64b66b",
                "link_training": recipe["protocol"]["link_training"],
                "reset_sequence": recipe["protocol"]["reset_sequence"],
                "pcs_data_width": recipe["protocol"]["pcs_data_width"],
                "pcs_header_width": recipe["protocol"]["pcs_header_width"],
                "pcs_clock_mhz": recipe["protocol"]["pcs_clock_mhz"],
                "pcs_implementation": recipe["protocol"][
                    "pcs_implementation"
                ],
            },
            "vendor_products": {
                "generator": "vivado_gtwizard_ultrascale",
                "modules": recipe["expected_ips"],
                "xci": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in xci_records
                ],
            },
            "provenance": {
                "license": recipe["provenance"]["license"],
                "upstream": recipe["provenance"]["upstream"],
            },
        }
        provider_path = output_dir / "serial_phy_provider.json"
        write_json(provider_path, provider_manifest)
        from .serial_phy_provider import validate_serial_phy_provider

        validate_serial_phy_provider(
            provider_manifest, provider_path, platform
        )
        provider_artifact = {
            "path": provider_path.name,
            "sha256": _sha256(provider_path),
            "adapter": adapter_relative.as_posix(),
            "adapter_sha256": _sha256(adapter_destination),
        }
    report = {
        "schema": SERIAL_PHY_RECIPE_REPORT_SCHEMA,
        "status": "pass",
        "qualification": "vendor_generated_hardware_recipe_materialized",
        "hardware_release_authorized": False,
        "counts_as_open_flow_implementation": False,
        "recipe": recipe,
        "manifest_sha256": _sha256(manifest_path),
        "part": part,
        "tool": {
            "name": "vivado",
            "version": (version.stdout + version.stderr).strip(),
            "executable": str(vivado_executable),
        },
        "products": {
            "xci": xci_records,
            "generated_hdl_files": len(generated_hdl),
            "generated_hdl_bytes": sum(path.stat().st_size for path in generated_hdl),
            "primitive_presence": primitive_presence,
        },
        "artifacts": {
            "script": script_path.name,
            "script_sha256": _sha256(script_path),
            "log": log_path.name,
            "log_sha256": _sha256(log_path),
            **({"provider": provider_artifact} if provider_artifact else {}),
        },
        "next_required_gates": [
            *(
                ["phase6c_source_binding"]
                if provider_artifact
                else ["emuflow_contract_adapter", "phase6c_source_binding"]
            ),
            "board_reference_clock_and_reset_overlay",
            "vivado_synthesis_placement_routing_timing_drc",
            "hardware_link_training",
        ],
    }
    write_json(output_dir / "serial_phy_recipe_report.json", report)
    return report
