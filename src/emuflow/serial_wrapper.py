"""Generate the source-visible boundary around a board serial PHY provider."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .errors import ValidationError
from .io import read_json, write_json
from .physical_pins import (
    PACKAGE_PIN_BINDING_SCHEMA,
    SERIAL_TRANSCEIVER_PROVIDER,
)
from .platform import BoardLink, Platform


SERIAL_WRAPPER_SCHEMA = "emuflow.serial-wrapper/v1"
SERIAL_WRAPPER_REPORT_SCHEMA = "emuflow.phase6c-report/v1"
SERIAL_PHY_MODULE = "emuflow_external_serial_phy_lane"


def _sv_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not name or name[0].isdigit():
        name = f"n_{name}"
    return name


def _serial_port(
    link: str, peer: str, direction: str, polarity: str, lane: int
) -> str:
    return (
        f"gty_{direction}{polarity}_{_sv_name(link)}_"
        f"{_sv_name(peer)}_lane{lane}"
    )


def _binding_sites(
    platform: Platform, binding: Mapping[str, Any]
) -> Dict[str, list[Dict[str, Any]]]:
    if (
        binding.get("schema") != PACKAGE_PIN_BINDING_SCHEMA
        or binding.get("provider") != SERIAL_TRANSCEIVER_PROVIDER
        or binding.get("status") != "source_backed_boarddb"
        or binding.get("platform") != platform.name
    ):
        raise ValidationError("serial wrapper binding identity is invalid")
    raw_entries = binding.get("entries")
    if not isinstance(raw_entries, list) or any(
        not isinstance(entry, dict) for entry in raw_entries
    ):
        raise ValidationError("serial wrapper binding entries are malformed")
    links = {link.id: link for link in platform.links}
    sites: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    directed_channels = set()
    used_package_pins = set()
    logical_bindings = set()
    for entry in raw_entries:
        link = links.get(entry.get("link"))
        source = entry.get("source")
        sink = entry.get("sink")
        lane = entry.get("physical_lane")
        if (
            link is None
            or link.mode != "serial"
            or {source, sink} != set(link.endpoints)
            or isinstance(lane, bool)
            or not isinstance(lane, int)
            or not 0 <= lane < link.data_lanes_per_direction
        ):
            raise ValidationError("serial wrapper binding channel is invalid")
        channel_key = (link.id, source, sink, lane)
        if channel_key in directed_channels:
            raise ValidationError("serial wrapper has a duplicate directed channel")
        directed_channels.add(channel_key)
        if (
            entry.get("id")
            != f"{link.id}:{source}-to-{sink}:gty-{lane}"
            or entry.get("payload_bits_per_lane_per_cycle")
            != link.payload_bits_per_lane_per_cycle
            or entry.get("transceiver_site_status") != "unresolved"
        ):
            raise ValidationError("serial wrapper channel contract is invalid")
        source_endpoint = link.endpoint_binding(source)
        sink_endpoint = link.endpoint_binding(sink)
        if source_endpoint is None or sink_endpoint is None:
            raise ValidationError("serial wrapper lacks BoardDB endpoint bindings")
        source_lane = source_endpoint.lanes[lane]
        sink_lane = sink_endpoint.lanes[lane]
        expected = {
            "source_connector": source_endpoint.connector,
            "sink_connector": sink_endpoint.connector,
            "source_mgt_group": source_endpoint.mgt,
            "sink_mgt_group": sink_endpoint.mgt,
            "source_ports": {
                polarity: _serial_port(
                    link.id, sink, "tx", polarity, lane
                )
                for polarity in ("p", "n")
            },
            "sink_ports": {
                polarity: _serial_port(
                    link.id, source, "rx", polarity, lane
                )
                for polarity in ("p", "n")
            },
            "source_package_pins": {
                "p": source_lane.tx_package_pin_p,
                "n": source_lane.tx_package_pin_n,
            },
            "sink_package_pins": {
                "p": sink_lane.rx_package_pin_p,
                "n": sink_lane.rx_package_pin_n,
            },
        }
        if any(entry.get(field) != value for field, value in expected.items()):
            raise ValidationError(
                "serial wrapper binding disagrees with BoardDB endpoint pins"
            )
        logical_lanes = entry.get("logical_lanes")
        entry_bindings = entry.get("logical_bindings")
        lower = lane * link.payload_bits_per_lane_per_cycle
        upper = lower + link.payload_bits_per_lane_per_cycle
        if (
            not isinstance(logical_lanes, list)
            or not logical_lanes
            or any(
                isinstance(logical_lane, bool)
                or not isinstance(logical_lane, int)
                or not lower <= logical_lane < upper
                for logical_lane in logical_lanes
            )
            or len(set(logical_lanes)) != len(logical_lanes)
            or not isinstance(entry_bindings, list)
            or len(entry_bindings) != len(logical_lanes)
            or any(
                not isinstance(item, str) or not item
                for item in entry_bindings
            )
            or logical_bindings.intersection(entry_bindings)
        ):
            raise ValidationError("serial wrapper logical-lane projection is invalid")
        logical_bindings.update(entry_bindings)
        for fpga, pins in (
            (source, expected["source_package_pins"]),
            (sink, expected["sink_package_pins"]),
        ):
            for pin in pins.values():
                key = (fpga, pin)
                if key in used_package_pins:
                    raise ValidationError("serial wrapper package-pin collision")
                used_package_pins.add(key)
        for fpga, peer, direction in (
            (source, sink, "tx"),
            (sink, source, "rx"),
        ):
            site_key = (link.id, fpga, lane)
            site = sites.setdefault(
                site_key,
                {
                    "id": f"{link.id}:{fpga}:site-{lane}",
                    "link": link.id,
                    "fpga": fpga,
                    "peer": peer,
                    "physical_lane": lane,
                    "payload_width": link.payload_bits_per_lane_per_cycle,
                    "connector": link.endpoint_binding(fpga).connector,
                    "mgt_group": link.endpoint_binding(fpga).mgt,
                    "tx": None,
                    "rx": None,
                    "transceiver_site_status": "unresolved",
                },
            )
            if site["peer"] != peer or site[direction] is not None:
                raise ValidationError("serial wrapper site direction is duplicated")
            if direction == "tx":
                site["tx"] = {
                    "ports": expected["source_ports"],
                    "package_pins": expected["source_package_pins"],
                }
            else:
                site["rx"] = {
                    "ports": expected["sink_ports"],
                    "package_pins": expected["sink_package_pins"],
                }
    result = {fpga.id: [] for fpga in platform.fpgas}
    for site in sites.values():
        result[site["fpga"]].append(site)
    for values in result.values():
        values.sort(key=lambda item: item["id"])
    return result


def _incident_links(platform: Platform, fpga: str) -> Sequence[BoardLink]:
    return sorted(
        (link for link in platform.links if fpga in link.endpoints),
        key=lambda link: link.id,
    )


def _wrapper_module_name(fpga: str) -> str:
    return f"emuflow_serial_wrapper_{_sv_name(fpga)}"


def serial_phy_contract_rtl() -> str:
    return """// External serial-PHY provider contract generated by EmuFlow.
// A board-specific implementation must replace this black box before bitstream.
(* black_box *)
module emuflow_external_serial_phy_lane #(
  parameter integer PAYLOAD_WIDTH = 64
) (
  input  wire                     user_clk,
  input  wire                     reset,
  input  wire [PAYLOAD_WIDTH-1:0] tx_data,
  output wire [PAYLOAD_WIDTH-1:0] rx_data,
  output wire                     txp,
  output wire                     txn,
  input  wire                     rxp,
  input  wire                     rxn,
  output wire                     ready
);
endmodule
"""


def serial_wrapper_rtl(
    platform: Platform,
    fpga: str,
    sites: Sequence[Mapping[str, Any]],
) -> str:
    incident = _incident_links(platform, fpga)
    site_by_key = {
        (site["link"], site["physical_lane"]): site for site in sites
    }
    site_index_by_key = {
        (site["link"], site["physical_lane"]): index
        for index, site in enumerate(sites)
    }
    ports = ["  input  wire fabric_clk", "  input  wire reset"]
    for link in incident:
        peer = link.endpoints[1] if link.endpoints[0] == fpga else link.endpoints[0]
        width = link.transport_bits_per_cycle_per_direction
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        ports.extend(
            [
                f"  input  wire [{width - 1}:0] tx_{suffix}",
                f"  output wire [{width - 1}:0] rx_{suffix}",
            ]
        )
    for site in sites:
        for direction in ("tx", "rx"):
            record = site[direction]
            if record is None:
                continue
            io = "output wire" if direction == "tx" else "input  wire"
            ports.extend(
                f"  {io} {record['ports'][polarity]}"
                for polarity in ("p", "n")
            )
    ports.append("  output wire links_ready")
    lines = [
        "// Generated source-visible wrapper around an external serial PHY.",
        "// The PHY contract is intentionally a black box until a BSP supplies it.",
        f"module {_wrapper_module_name(fpga)} (",
        ",\n".join(ports),
        ");",
        "",
    ]
    ready_wires = []
    for index, site in enumerate(sites):
        stem = f"site_{index}"
        width = site["payload_width"]
        link = next(link for link in incident if link.id == site["link"])
        peer = site["peer"]
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        lower = site["physical_lane"] * width
        tx_data = (
            f"tx_{suffix}[{lower} +: {width}]"
            if site["tx"] is not None
            else f"{width}'b0"
        )
        txp = (
            site["tx"]["ports"]["p"]
            if site["tx"] is not None
            else f"{stem}_unused_txp"
        )
        txn = (
            site["tx"]["ports"]["n"]
            if site["tx"] is not None
            else f"{stem}_unused_txn"
        )
        if site["tx"] is None:
            lines.extend((f"  wire {txp};", f"  wire {txn};"))
        lines.extend(
            [
                f"  wire [{width - 1}:0] {stem}_rx_data;",
                f"  wire {stem}_ready;",
                f"  {SERIAL_PHY_MODULE} #(",
                f"    .PAYLOAD_WIDTH({width})",
                f"  ) {stem}_phy (",
                "    .user_clk(fabric_clk),",
                "    .reset(reset),",
                f"    .tx_data({tx_data}),",
                f"    .rx_data({stem}_rx_data),",
                f"    .txp({txp}),",
                f"    .txn({txn}),",
                "    .rxp("
                + (
                    site["rx"]["ports"]["p"]
                    if site["rx"] is not None
                    else "1'b0"
                )
                + "),",
                "    .rxn("
                + (
                    site["rx"]["ports"]["n"]
                    if site["rx"] is not None
                    else "1'b0"
                )
                + "),",
                f"    .ready({stem}_ready)",
                "  );",
                "",
            ]
        )
        ready_wires.append(f"{stem}_ready")
    for link in incident:
        peer = link.endpoints[1] if link.endpoints[0] == fpga else link.endpoints[0]
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        pieces = []
        width = link.payload_bits_per_lane_per_cycle
        for lane in reversed(range(link.data_lanes_per_direction)):
            site = site_by_key.get((link.id, lane))
            if site is not None and site["rx"] is not None:
                index = site_index_by_key[(link.id, lane)]
                pieces.append(f"site_{index}_rx_data")
            else:
                pieces.append(f"{width}'b0")
        lines.append(f"  assign rx_{suffix} = {{{', '.join(pieces)}}};")
    lines.extend(
        [
            (
                f"  assign links_ready = {' & '.join(ready_wires)};"
                if ready_wires
                else "  assign links_ready = 1'b1;"
            ),
            "",
            "endmodule",
            "",
        ]
    )
    return "\n".join(lines)


def build_serial_wrapper_manifest(
    platform: Platform, binding: Mapping[str, Any]
) -> Dict[str, Any]:
    sites = _binding_sites(platform, binding)
    fpgas = []
    for fpga in sorted(platform.fpgas, key=lambda item: item.id):
        fpga_sites = sites[fpga.id]
        transport_connections = []
        for link in _incident_links(platform, fpga.id):
            peer = (
                link.endpoints[1]
                if link.endpoints[0] == fpga.id
                else link.endpoints[0]
            )
            suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
            transport_connections.append(
                {
                    "link": link.id,
                    "peer": peer,
                    "width": link.transport_bits_per_cycle_per_direction,
                    "transport_tx_port": f"tx_{suffix}",
                    "transport_rx_port": f"rx_{suffix}",
                }
            )
        fpgas.append(
            {
                "fpga": fpga.id,
                "part": fpga.part,
                "module": _wrapper_module_name(fpga.id),
                "rtl": f"{fpga.id}.serial_wrapper.sv",
                "active_transceiver_sites": len(fpga_sites),
                "active_tx_directions": sum(
                    site["tx"] is not None for site in fpga_sites
                ),
                "active_rx_directions": sum(
                    site["rx"] is not None for site in fpga_sites
                ),
                "transport_connections": transport_connections,
                "sites": fpga_sites,
            }
        )
    return {
        "schema": SERIAL_WRAPPER_SCHEMA,
        "status": "awaiting_external_phy_provider",
        "design": binding["design"],
        "platform": platform.name,
        "binding_provider": SERIAL_TRANSCEIVER_PROVIDER,
        "phy_contract": {
            "module": SERIAL_PHY_MODULE,
            "rtl": "external_serial_phy_contract.sv",
            "implementation_status": "black_box_unresolved",
            "required_provider_fields": [
                "transceiver_site",
                "reference_clock",
                "line_encoding",
                "reset_sequence",
                "link_training",
            ],
        },
        "fpgas": fpgas,
        "metrics": {
            "fpgas": len(fpgas),
            "active_transceiver_sites": sum(
                item["active_transceiver_sites"] for item in fpgas
            ),
            "active_tx_directions": sum(
                item["active_tx_directions"] for item in fpgas
            ),
            "active_rx_directions": sum(
                item["active_rx_directions"] for item in fpgas
            ),
            "unresolved_phy_modules": sum(
                item["active_transceiver_sites"] for item in fpgas
            ),
        },
    }


def run_phase6c(
    platform_path: Path,
    binding_path: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    binding = read_json(binding_path)
    manifest = build_serial_wrapper_manifest(platform, binding)
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = serial_phy_contract_rtl()
    contract_path = output_dir / "external_serial_phy_contract.sv"
    contract_path.write_text(
        contract, encoding="utf-8"
    )
    if contract_path.read_text(encoding="utf-8") != contract:
        raise ValidationError("written serial PHY contract does not agree")
    for record in manifest["fpgas"]:
        wrapper = serial_wrapper_rtl(
            platform, record["fpga"], record["sites"]
        )
        wrapper_path = output_dir / record["rtl"]
        wrapper_path.write_text(wrapper, encoding="utf-8")
        if wrapper_path.read_text(encoding="utf-8") != wrapper:
            raise ValidationError("written serial wrapper RTL does not agree")
    digest = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    manifest["binding_sha256"] = digest
    write_json(output_dir / "serial_wrapper_manifest.json", manifest)
    rebuilt = build_serial_wrapper_manifest(platform, binding)
    rebuilt["binding_sha256"] = digest
    if read_json(output_dir / "serial_wrapper_manifest.json") != rebuilt:
        raise ValidationError("serial wrapper manifest is not reproducible")
    report = {
        "schema": SERIAL_WRAPPER_REPORT_SCHEMA,
        "phase": "6C",
        "status": "pass",
        "design": manifest["design"],
        "platform": platform.name,
        "hardware_release_status": "blocked_on_external_phy_provider",
        "validation": dict(manifest["metrics"]),
        "artifacts": {
            "manifest": "serial_wrapper_manifest.json",
            "phy_contract": "external_serial_phy_contract.sv",
            "wrappers": {
                item["fpga"]: item["rtl"] for item in manifest["fpgas"]
            },
            "report": "phase6c_report.json",
        },
    }
    write_json(output_dir / "phase6c_report.json", report)
    return report
