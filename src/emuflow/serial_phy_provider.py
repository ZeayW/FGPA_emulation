"""Editable-source inventory and compatibility checks for serial PHY providers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import ValidationError
from .io import read_json, write_json
from .platform import Platform
from .serial_contract import SERIAL_CLOCK_RESET_MODULE, SERIAL_PHY_MODULE


SERIAL_PHY_PROVIDER_SCHEMA = "emuflow.serial-phy-provider/v1"
VALID_PROVIDER_QUALIFICATIONS = {
    "editable_source_hardware",
    "simulation_only",
}
VALID_SOURCE_LANGUAGES = {"systemverilog", "verilog", "tcl", "xdc"}
ALLOWED_SOURCE_SUFFIXES = {".sv", ".v", ".svh", ".vh", ".tcl", ".xdc"}
FORBIDDEN_SOURCE_SUFFIXES = {
    ".a", ".dcp", ".dll", ".edf", ".edf.gz", ".exe", ".lib", ".o",
    ".so", ".xci", ".xo", ".zip",
}


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}: expected a non-empty string")
    return value


def _positive_number(value: Any, context: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) <= 0.0
    ):
        raise ValidationError(f"{context}: expected a positive number")
    return float(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_platform_compatibility(
    provider: Mapping[str, Any], platform: Platform
) -> Dict[str, Any]:
    supported_parts = set(provider["supported_parts"])
    platform_parts = {fpga.part for fpga in platform.fpgas}
    unsupported = sorted(platform_parts - supported_parts)
    if unsupported:
        raise ValidationError(
            f"serial PHY provider does not support platform parts: {unsupported}"
        )
    serial_links = [link for link in platform.links if link.mode == "serial"]
    if not serial_links:
        raise ValidationError("platform has no serial link for the PHY provider")
    protocol = provider["protocol"]
    mismatches = []
    for link in serial_links:
        if (
            link.payload_bits_per_lane_per_cycle
            != protocol["payload_bits_per_lane_per_cycle"]
        ):
            mismatches.append(f"{link.id}:payload_width")
        if abs(link.fabric_clock_mhz - protocol["user_clock_mhz"]) > 1e-9:
            mismatches.append(f"{link.id}:user_clock")
        configured_rate = (
            link.fabric_clock_mhz
            * link.payload_bits_per_lane_per_cycle
            / 1000.0
        )
        if protocol["line_rate_gbps_per_lane"] + 1e-9 < configured_rate:
            mismatches.append(f"{link.id}:provider_line_rate_below_user_rate")
        if (
            link.max_line_rate_gbps_per_lane is not None
            and protocol["line_rate_gbps_per_lane"]
            > link.max_line_rate_gbps_per_lane * (1.0 + 1e-9)
        ):
            mismatches.append(f"{link.id}:provider_line_rate_above_board_ceiling")
    if mismatches:
        raise ValidationError(
            "serial PHY provider/BoardDB incompatibility: " + ", ".join(mismatches)
        )
    return {
        "platform": platform.name,
        "parts": sorted(platform_parts),
        "serial_links": len(serial_links),
        "status": "compatible",
    }


def validate_serial_phy_provider(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    platform: Optional[Platform] = None,
) -> Dict[str, Any]:
    if manifest.get("schema") != SERIAL_PHY_PROVIDER_SCHEMA:
        raise ValidationError(
            f"serial PHY provider schema must be {SERIAL_PHY_PROVIDER_SCHEMA!r}"
        )
    provider_id = _string(manifest.get("id"), "provider.id")
    qualification = manifest.get("qualification")
    if qualification not in VALID_PROVIDER_QUALIFICATIONS:
        raise ValidationError("serial PHY provider qualification is invalid")
    raw_parts = manifest.get("supported_parts")
    if (
        not isinstance(raw_parts, list)
        or not raw_parts
        or any(not isinstance(part, str) or not part for part in raw_parts)
        or len(set(raw_parts)) != len(raw_parts)
    ):
        raise ValidationError("serial PHY provider supported_parts are invalid")
    modules = manifest.get("modules")
    if not isinstance(modules, dict):
        raise ValidationError("serial PHY provider modules are missing")
    if modules.get("clock_reset") != SERIAL_CLOCK_RESET_MODULE:
        raise ValidationError("serial PHY clock/reset module name is incompatible")
    if modules.get("lane") != SERIAL_PHY_MODULE:
        raise ValidationError("serial PHY lane module name is incompatible")

    protocol = manifest.get("protocol")
    if not isinstance(protocol, dict):
        raise ValidationError("serial PHY protocol record is missing")
    normalized_protocol = {
        "payload_bits_per_lane_per_cycle": protocol.get(
            "payload_bits_per_lane_per_cycle"
        ),
        "user_clock_mhz": _positive_number(
            protocol.get("user_clock_mhz"), "protocol.user_clock_mhz"
        ),
        "line_rate_gbps_per_lane": _positive_number(
            protocol.get("line_rate_gbps_per_lane"),
            "protocol.line_rate_gbps_per_lane",
        ),
        "encoding": _string(protocol.get("encoding"), "protocol.encoding"),
        "link_training": _string(
            protocol.get("link_training"), "protocol.link_training"
        ),
        "reset_sequence": _string(
            protocol.get("reset_sequence"), "protocol.reset_sequence"
        ),
    }
    payload_width = normalized_protocol["payload_bits_per_lane_per_cycle"]
    if (
        isinstance(payload_width, bool)
        or not isinstance(payload_width, int)
        or payload_width <= 0
    ):
        raise ValidationError(
            "protocol.payload_bits_per_lane_per_cycle must be a positive integer"
        )

    raw_root = _string(manifest.get("source_root"), "provider.source_root")
    root = (manifest_path.parent / raw_root).resolve()
    if not root.is_dir():
        raise ValidationError("serial PHY provider source_root does not exist")
    raw_sources = manifest.get("sources")
    if (
        not isinstance(raw_sources, list)
        or not raw_sources
        or any(not isinstance(item, dict) for item in raw_sources)
    ):
        raise ValidationError("serial PHY provider sources are invalid")
    inventory = []
    declared_paths = set()
    hdl_text = []
    for index, item in enumerate(raw_sources):
        context = f"provider.sources[{index}]"
        relative = _string(item.get("path"), f"{context}.path")
        language = item.get("language")
        role = _string(item.get("role"), f"{context}.role")
        expected = _string(item.get("sha256"), f"{context}.sha256")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValidationError(f"{context}.sha256 is invalid")
        if language not in VALID_SOURCE_LANGUAGES:
            raise ValidationError(f"{context}.language is invalid")
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValidationError(f"{context}: source escapes or is missing")
        suffixes = "".join(path.suffixes).lower()
        if (
            path.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES
            or any(suffixes.endswith(suffix) for suffix in FORBIDDEN_SOURCE_SUFFIXES)
        ):
            raise ValidationError(f"{context}: opaque provider artifact is forbidden")
        if relative in declared_paths:
            raise ValidationError(f"{context}: duplicate source path")
        actual = _sha256(path)
        if actual != expected:
            raise ValidationError(f"{context}: source SHA-256 mismatch")
        declared_paths.add(relative)
        inventory.append(
            {
                "path": relative,
                "language": language,
                "role": role,
                "bytes": path.stat().st_size,
                "sha256": actual,
            }
        )
        if language in {"systemverilog", "verilog"}:
            hdl_text.append(path.read_text(encoding="utf-8"))
    combined_hdl = "\n".join(hdl_text)
    if (
        qualification == "editable_source_hardware"
        and re.search(r"\(\*\s*black_box\s*\*\)", combined_hdl)
    ):
        raise ValidationError(
            "editable-source hardware provider cannot contain black-box modules"
        )
    for role, module in sorted(modules.items()):
        if re.search(rf"\bmodule\s+{re.escape(module)}\b", combined_hdl) is None:
            raise ValidationError(
                f"serial PHY provider source does not define {role} module {module}"
            )

    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("serial PHY provider provenance is missing")
    license_id = _string(provenance.get("license"), "provenance.license")
    upstream = _string(provenance.get("upstream"), "provenance.upstream")
    normalized = {
        "schema": SERIAL_PHY_PROVIDER_SCHEMA,
        "id": provider_id,
        "qualification": qualification,
        "supported_parts": sorted(raw_parts),
        "modules": {
            "clock_reset": SERIAL_CLOCK_RESET_MODULE,
            "lane": SERIAL_PHY_MODULE,
        },
        "source_root": raw_root,
        "sources": sorted(inventory, key=lambda item: item["path"]),
        "protocol": normalized_protocol,
        "provenance": {"license": license_id, "upstream": upstream},
    }
    compatibility = (
        _validate_platform_compatibility(normalized, platform)
        if platform is not None
        else None
    )
    return {
        "status": "pass",
        "provider": provider_id,
        "qualification": qualification,
        "editable_sources": len(inventory),
        "source_bytes": sum(item["bytes"] for item in inventory),
        "compatibility": compatibility,
        "normalized": normalized,
    }


def validate_serial_phy_provider_file(
    manifest_path: Path,
    platform_path: Optional[Path] = None,
    normalized_out: Optional[Path] = None,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path) if platform_path is not None else None
    result = validate_serial_phy_provider(
        read_json(manifest_path), manifest_path, platform
    )
    if normalized_out is not None:
        write_json(normalized_out, result["normalized"])
    return {key: value for key, value in result.items() if key != "normalized"}
