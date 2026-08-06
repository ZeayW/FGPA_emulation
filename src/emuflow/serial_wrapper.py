"""Generate the source-visible boundary around a board serial PHY provider."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .board_support import validate_board_support_overlay
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


def _validate_transport_connections(
    platform: Platform,
    fpga: str,
    sites: Sequence[Mapping[str, Any]],
    transport: Mapping[str, Any],
) -> Dict[Tuple[str, str], set[str]]:
    if (
        transport.get("schema") != "emuflow.transport-endpoints/v1"
        or transport.get("platform") != platform.name
        or transport.get("fpga") != fpga
        or isinstance(transport.get("frame_slots"), bool)
        or not isinstance(transport.get("frame_slots"), int)
        or transport["frame_slots"] <= 0
        or not isinstance(transport.get("endpoints"), list)
    ):
        raise ValidationError(f"{fpga} transport document is invalid")
    incident = {link.id: link for link in _incident_links(platform, fpga)}
    kinds: Dict[Tuple[str, str], set[str]] = defaultdict(set)
    for endpoint in transport["endpoints"]:
        if not isinstance(endpoint, dict):
            raise ValidationError(f"{fpga} transport endpoint is malformed")
        link = incident.get(endpoint.get("link"))
        peer = endpoint.get("peer")
        kind = endpoint.get("kind")
        if (
            link is None
            or peer not in link.endpoints
            or peer == fpga
            or kind not in {"tx", "rx"}
        ):
            raise ValidationError(f"{fpga} transport endpoint is invalid")
        kinds[(link.id, peer)].add(kind)
    site_kinds: Dict[Tuple[str, str], set[str]] = defaultdict(set)
    for site in sites:
        for kind in ("tx", "rx"):
            if site[kind] is not None:
                site_kinds[(site["link"], site["peer"])].add(kind)
    if kinds != site_kinds:
        raise ValidationError(
            f"{fpga} transport ports and serial wrapper directions disagree"
        )
    for field in ("source_signals", "shadow_signals"):
        records = transport.get(field)
        if not isinstance(records, list) or any(
            not isinstance(record, dict)
            or not isinstance(record.get("signal"), str)
            or not record["signal"]
            or isinstance(record.get("index"), bool)
            or not isinstance(record.get("index"), int)
            for record in records
        ):
            raise ValidationError(f"{fpga} transport {field} is invalid")
        if (
            {record["index"] for record in records} != set(range(len(records)))
            or len({record["signal"] for record in records}) != len(records)
        ):
            raise ValidationError(f"{fpga} transport {field} is not contiguous")
    return kinds


def serial_integration_shell_rtl(
    platform: Platform,
    fpga: str,
    sites: Sequence[Mapping[str, Any]],
    transport: Mapping[str, Any],
) -> str:
    kinds = _validate_transport_connections(
        platform, fpga, sites, transport
    )
    source_count = max(1, len(transport["source_signals"]))
    shadow_count = max(1, len(transport["shadow_signals"]))
    slot_bits = max(1, (transport["frame_slots"] - 1).bit_length())
    ports = [
        "  input  wire fabric_clk",
        "  input  wire reset",
        f"  input  wire [{source_count - 1}:0] source_values",
        f"  output wire [{shadow_count - 1}:0] shadow_values",
        "  output wire virtual_clock_enable",
        f"  output wire [{slot_bits - 1}:0] slot_debug",
        "  output wire links_ready_debug",
    ]
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
    lines = [
        "// Generated Phase 6 transport-to-serial-wrapper integration shell.",
        f"module emuflow_partition_shell_{_sv_name(fpga)} (",
        ",\n".join(ports),
        ");",
        "",
        "  wire links_ready;",
    ]
    incident = _incident_links(platform, fpga)
    for link in incident:
        peer = link.endpoints[1] if link.endpoints[0] == fpga else link.endpoints[0]
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        width = link.transport_bits_per_cycle_per_direction
        lines.extend(
            [
                f"  wire [{width - 1}:0] link_tx_{suffix};",
                f"  wire [{width - 1}:0] link_rx_{suffix};",
            ]
        )
        if "tx" not in kinds.get((link.id, peer), set()):
            lines.append(f"  assign link_tx_{suffix} = {width}'b0;")
    lines.extend(
        [
            "  assign links_ready_debug = links_ready;",
            "",
            f"  emuflow_transport_{_sv_name(fpga)} transport (",
            "    .fabric_clk(fabric_clk),",
            "    .reset(reset),",
            "    .links_ready(links_ready),",
            "    .source_values(source_values),",
            "    .shadow_values(shadow_values),",
            "    .virtual_clock_enable(virtual_clock_enable),",
            "    .slot_debug(slot_debug)"
            + ("," if kinds else ""),
        ]
    )
    transport_ports = []
    for link in incident:
        peer = link.endpoints[1] if link.endpoints[0] == fpga else link.endpoints[0]
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        active = kinds.get((link.id, peer), set())
        if "rx" in active:
            transport_ports.append(
                f"    .rx_{suffix}(link_rx_{suffix})"
            )
        if "tx" in active:
            transport_ports.append(
                f"    .tx_{suffix}(link_tx_{suffix})"
            )
    if transport_ports:
        lines.append(",\n".join(transport_ports))
    lines.extend(
        [
            "  );",
            "",
            f"  {_wrapper_module_name(fpga)} serial_wrapper (",
            "    .fabric_clk(fabric_clk),",
            "    .reset(reset),",
        ]
    )
    wrapper_ports = []
    for link in incident:
        peer = link.endpoints[1] if link.endpoints[0] == fpga else link.endpoints[0]
        suffix = f"{_sv_name(link.id)}_{_sv_name(peer)}"
        wrapper_ports.extend(
            [
                f"    .tx_{suffix}(link_tx_{suffix})",
                f"    .rx_{suffix}(link_rx_{suffix})",
            ]
        )
    for site in sites:
        for direction in ("tx", "rx"):
            record = site[direction]
            if record is None:
                continue
            wrapper_ports.extend(
                f"    .{record['ports'][polarity]}("
                f"{record['ports'][polarity]})"
                for polarity in ("p", "n")
            )
    wrapper_ports.append("    .links_ready(links_ready)")
    lines.append(",\n".join(wrapper_ports))
    lines.extend(["  );", "", "endmodule", ""])
    return "\n".join(lines)


def build_serial_wrapper_manifest(
    platform: Platform,
    binding: Mapping[str, Any],
    transports: Optional[Mapping[str, Mapping[str, Any]]] = None,
    board_overlay: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    sites = _binding_sites(platform, binding)
    overlay_result = (
        validate_board_support_overlay(board_overlay, platform)
        if board_overlay is not None
        else None
    )
    overlay = overlay_result["normalized"] if overlay_result is not None else None
    overlay_sites = (
        {
            (item["fpga"], item["link"], item["physical_lane"]): item
            for item in overlay["transceiver_sites"]
        }
        if overlay is not None
        else {}
    )
    resolved_sites = 0
    for fpga_sites in sites.values():
        for site in fpga_sites:
            resolved = overlay_sites.get(
                (site["fpga"], site["link"], site["physical_lane"])
            )
            if resolved is None:
                continue
            site["transceiver_site"] = resolved["site"]
            site["reference_clock_binding"] = resolved[
                "reference_clock_binding"
            ]
            site["transceiver_site_status"] = (
                "resolved_source_backed"
                if overlay_result["hardware_qualification"] == "source_backed"
                else "resolved_unverified"
            )
            resolved_sites += 1
    if transports is not None and set(transports) != set(sites):
        raise ValidationError(
            "serial wrapper transports must cover every FPGA exactly once"
        )
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
            link_sites = [
                site for site in fpga_sites if site["link"] == link.id
            ]
            tx_active = any(site["tx"] is not None for site in link_sites)
            rx_active = any(site["rx"] is not None for site in link_sites)
            transport_connections.append(
                {
                    "link": link.id,
                    "peer": peer,
                    "width": link.transport_bits_per_cycle_per_direction,
                    "transport_tx_port": f"tx_{suffix}" if tx_active else None,
                    "transport_rx_port": f"rx_{suffix}" if rx_active else None,
                    "wrapper_tx_port": f"tx_{suffix}",
                    "wrapper_rx_port": f"rx_{suffix}",
                    "inactive_tx_policy": None if tx_active else "tie_zero",
                    "inactive_rx_policy": None if rx_active else "discard",
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
                **(
                    {
                        "integration_shell": (
                            f"{fpga.id}.serial_integration_shell.sv"
                        )
                    }
                    if transports is not None
                    else {}
                ),
            }
        )
        if transports is not None:
            if transports[fpga.id].get("design") != binding["design"]:
                raise ValidationError(
                    f"{fpga.id} transport design does not match binding"
                )
            _validate_transport_connections(
                platform, fpga.id, fpga_sites, transports[fpga.id]
            )
    active_sites = sum(item["active_transceiver_sites"] for item in fpgas)
    source_backed_site_resolution = (
        overlay_result is not None
        and overlay_result["hardware_qualification"] == "source_backed"
        and resolved_sites == active_sites
    )
    source_backed_resolved_sites = (
        resolved_sites
        if overlay_result is not None
        and overlay_result["hardware_qualification"] == "source_backed"
        else 0
    )
    required_provider_fields = [
        "reset_synchronization",
        "line_encoding",
        "reset_sequence",
        "link_training",
    ]
    if not source_backed_site_resolution:
        required_provider_fields[0:0] = [
            "transceiver_site",
            "reference_clock_selection",
            "reference_clock_package_binding",
        ]
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
            "required_provider_fields": required_provider_fields,
            "internal_reset": {
                "signal": "reset",
                "polarity": "active_high",
                "derivation_status": "unresolved_from_board_reset",
            },
            "board_service_candidates": {
                "reference_clocks": [
                    clock.to_dict() for clock in platform.clocks
                ],
                "resets": [reset.to_dict() for reset in platform.resets],
            },
            "board_support_overlay": (
                {
                    "status": "validated",
                    "qualification": overlay["qualification"],
                    "hardware_qualification": overlay_result[
                        "hardware_qualification"
                    ],
                    "reference_clock_bindings": overlay[
                        "reference_clocks"
                    ],
                    "reset_bindings": overlay["resets"],
                    "transceiver_site_bindings": len(
                        overlay["transceiver_sites"]
                    ),
                }
                if overlay is not None
                else {"status": "not_provided"}
            ),
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
            "overlay_bound_transceiver_sites": resolved_sites,
            "source_backed_resolved_transceiver_sites": (
                source_backed_resolved_sites
            ),
            "unresolved_transceiver_sites": (
                active_sites - source_backed_resolved_sites
            ),
            "unresolved_phy_modules": active_sites,
            "integrated_transport_shells": (
                len(fpgas) if transports is not None else 0
            ),
        },
    }


def run_phase6c(
    platform_path: Path,
    binding_path: Path,
    output_dir: Path,
    transport_paths: Optional[Mapping[str, Path]] = None,
    board_overlay_path: Optional[Path] = None,
) -> Dict[str, Any]:
    platform = Platform.load(platform_path)
    binding = read_json(binding_path)
    transports = (
        {
            fpga: read_json(path)
            for fpga, path in transport_paths.items()
        }
        if transport_paths is not None
        else None
    )
    board_overlay = (
        read_json(board_overlay_path) if board_overlay_path is not None else None
    )
    manifest = build_serial_wrapper_manifest(
        platform, binding, transports, board_overlay
    )
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
        if transports is not None:
            shell = serial_integration_shell_rtl(
                platform,
                record["fpga"],
                record["sites"],
                transports[record["fpga"]],
            )
            shell_path = output_dir / record["integration_shell"]
            shell_path.write_text(shell, encoding="utf-8")
            if shell_path.read_text(encoding="utf-8") != shell:
                raise ValidationError(
                    "written serial integration shell does not agree"
                )
    digest = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    manifest["binding_sha256"] = digest
    write_json(output_dir / "serial_wrapper_manifest.json", manifest)
    rebuilt = build_serial_wrapper_manifest(
        platform, binding, transports, board_overlay
    )
    rebuilt["binding_sha256"] = digest
    if board_overlay_path is not None:
        overlay_digest = hashlib.sha256(board_overlay_path.read_bytes()).hexdigest()
        manifest["board_overlay_sha256"] = overlay_digest
        rebuilt["board_overlay_sha256"] = overlay_digest
        write_json(output_dir / "serial_wrapper_manifest.json", manifest)
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
            **(
                {
                    "integration_shells": {
                        item["fpga"]: item["integration_shell"]
                        for item in manifest["fpgas"]
                    }
                }
                if transports is not None
                else {}
            ),
            "report": "phase6c_report.json",
        },
    }
    write_json(output_dir / "phase6c_report.json", report)
    return report
