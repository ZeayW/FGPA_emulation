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
from .serial_contract import (
    SERIAL_CLOCK_RESET_MODULE,
    SERIAL_PHY_MODULE,
    SERIAL_PHY_QUAD_MODULE,
)
from .serial_phy_provider import (
    SERIAL_PHY_PROVIDER_V2_SCHEMA,
    validate_serial_phy_provider,
)
from .runtime_sync import (
    RUNTIME_SYNC_NODE_MODULE,
    validate_runtime_sync_provider,
    validate_runtime_sync_topology,
)
from .vivado_pin_sites import validate_vivado_pin_site_map


SERIAL_WRAPPER_SCHEMA = "emuflow.serial-wrapper/v1"
SERIAL_WRAPPER_REPORT_SCHEMA = "emuflow.phase6c-report/v1"
_GT_CHANNEL_SITE = re.compile(r"GT([YH])E4_CHANNEL_X(\d+)Y(\d+)")
_GT_COMMON_SITE = re.compile(r"GT([YH])E4_COMMON_X(\d+)Y(\d+)")


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


def _transceiver_quad_groups(
    sites: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    if not sites or any(
        not isinstance(site.get("transceiver_common_site"), str)
        for site in sites
    ):
        return []
    grouped: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    occupied = set()
    for site_index, site in enumerate(sites):
        channel_name = site.get("transceiver_site")
        common_name = site["transceiver_common_site"]
        channel_match = (
            _GT_CHANNEL_SITE.fullmatch(channel_name)
            if isinstance(channel_name, str)
            else None
        )
        common_match = _GT_COMMON_SITE.fullmatch(common_name)
        if channel_match is None or common_match is None:
            raise ValidationError("transceiver quad site name is malformed")
        channel_family, channel_x, channel_y_text = channel_match.groups()
        common_family, common_x, common_y_text = common_match.groups()
        channel_y = int(channel_y_text)
        common_y = int(common_y_text)
        channel_index = channel_y % 4
        if (
            channel_family != common_family
            or channel_x != common_x
            or channel_y // 4 != common_y
        ):
            raise ValidationError("GT channel/common topology is inconsistent")
        slot = (common_name, channel_index)
        if slot in occupied:
            raise ValidationError("GT quad channel slot is duplicated")
        occupied.add(slot)
        grouped[common_name].append(
            {
                "site_id": site["id"],
                "site_index": site_index,
                "channel_site": channel_name,
                "channel_index": channel_index,
                "link": site["link"],
                "physical_lane": site["physical_lane"],
            }
        )
    quads = []
    for common_name, channels in sorted(grouped.items()):
        channels.sort(key=lambda item: item["channel_index"])
        if len(channels) > 4:
            raise ValidationError("GT quad contains more than four channels")
        site_domains = {
            sites[channel["site_index"]].get("clock_reset_domain")
            for channel in channels
        }
        if len(site_domains) != 1:
            raise ValidationError(
                "GT channels sharing a common must use one clock/reset domain"
            )
        quads.append(
            {
                "id": common_name,
                "common_site": common_name,
                "channel_count": len(channels),
                "channels": channels,
                "clock_reset_domain": site_domains.pop(),
                "qualification": "vendor_device_db_same_tile_grouping",
            }
        )
    return quads


def _wrapper_module_name(fpga: str) -> str:
    return f"emuflow_serial_wrapper_{_sv_name(fpga)}"


def _clock_ports(binding_id: str) -> Tuple[str, str]:
    stem = f"refclk_{_sv_name(binding_id)}"
    return f"{stem}_p", f"{stem}_n"


def _reset_port(binding_id: str) -> str:
    return f"board_reset_{_sv_name(binding_id)}"


def serial_phy_contract_rtl() -> str:
    return """// External serial-PHY provider contract generated by EmuFlow.
// A board-specific implementation must replace this black box before bitstream.
(* black_box *)
module emuflow_external_serial_clock_reset #(
  parameter integer BOARD_RESET_ACTIVE_LOW = 1
) (
  input  wire refclk_p,
  input  wire refclk_n,
  input  wire board_reset,
  output wire phy_refclk,
  output wire phy_reset,
  output wire ready
);
endmodule

(* black_box *)
module emuflow_external_serial_phy_lane #(
  parameter integer PAYLOAD_WIDTH = 64
) (
  input  wire                     user_clk,
  input  wire                     reset,
  input  wire                     phy_refclk,
  input  wire                     phy_reset,
  input  wire [PAYLOAD_WIDTH-1:0] tx_data,
  output wire [PAYLOAD_WIDTH-1:0] rx_data,
  output wire                     txp,
  output wire                     txn,
  input  wire                     rxp,
  input  wire                     rxn,
  output wire                     ready
);
endmodule

(* black_box *)
module emuflow_external_serial_phy_quad #(
  parameter integer PAYLOAD_WIDTH = 64,
  parameter [3:0] ACTIVE_CHANNEL_MASK = 4'b0000
) (
  input  wire                         user_clk,
  input  wire                         reset,
  input  wire                         phy_refclk,
  input  wire                         phy_reset,
  input  wire [4*PAYLOAD_WIDTH-1:0]   tx_data,
  output wire [4*PAYLOAD_WIDTH-1:0]   rx_data,
  output wire [3:0]                   txp,
  output wire [3:0]                   txn,
  input  wire [3:0]                   rxp,
  input  wire [3:0]                   rxn,
  output wire [3:0]                   lane_ready,
  output wire                         common_ready
);
endmodule
"""


def serial_board_service_xdc(fpga_record: Mapping[str, Any]) -> str:
    services = fpga_record["board_services"]
    lines = [
        "# Generated source-backed serial clock/reset package constraints.",
        "# GT channel LOC constraints remain the external PHY provider's job.",
    ]
    for clock in services["reference_clocks"]:
        p, n = _clock_ports(clock["id"])
        period_ns = 1000.0 / clock["frequency_mhz"]
        lines.extend(
            [
                f"set_property PACKAGE_PIN {clock['package_pins']['p']} "
                f"[get_ports {{{p}}}]",
                f"set_property PACKAGE_PIN {clock['package_pins']['n']} "
                f"[get_ports {{{n}}}]",
                f"create_clock -name {_sv_name(clock['id'])} "
                f"-period {period_ns:.9g} [get_ports {{{p}}}]",
            ]
        )
    for reset_binding in services["resets"]:
        port = _reset_port(reset_binding["id"])
        lines.extend(
            [
                f"set_property PACKAGE_PIN {reset_binding['package_pin']} "
                f"[get_ports {{{port}}}]",
                f"set_property IOSTANDARD {reset_binding['iostandard']} "
                f"[get_ports {{{port}}}]",
            ]
        )
    return "\n".join(lines) + "\n"


def serial_gt_site_xdc(
    fpga_record: Mapping[str, Any], implementation: Mapping[str, Any]
) -> str:
    lines = [
        "# Generated trusted GT channel placement constraints.",
        "# Sites are source-backed or device-DB-derived from source-backed pins.",
        "# Cell paths are derived from the provider primitive hierarchy contract.",
    ]
    is_quad = "channel_instance_template" in implementation
    if is_quad:
        for quad_index, quad in enumerate(fpga_record["transceiver_quads"]):
            common_path = (
                f"serial_wrapper/quad_{quad_index}_phy/"
                f"{implementation['common_instance']}"
            )
            lines.append(
                f"set_property LOC {quad['common_site']} "
                f"[get_cells {{{common_path}}}]"
            )
            for channel in quad["channels"]:
                hierarchy = implementation["channel_instance_template"].format(
                    channel=channel["channel_index"]
                )
                channel_path = f"serial_wrapper/quad_{quad_index}_phy/{hierarchy}"
                lines.append(
                    f"set_property LOC {channel['channel_site']} "
                    f"[get_cells {{{channel_path}}}]"
                )
        return "\n".join(lines) + "\n"
    channel_instance = implementation["channel_instance"]
    for index, site in enumerate(fpga_record["sites"]):
        if site.get("transceiver_site_status") not in {
            "resolved_source_backed",
            "resolved_vendor_device_db",
        }:
            raise ValidationError("GT site XDC requires trusted site bindings")
        cell_path = f"serial_wrapper/site_{index}_phy/{channel_instance}"
        lines.append(
            f"set_property LOC {site['transceiver_site']} "
            f"[get_cells {{{cell_path}}}]"
        )
    return "\n".join(lines) + "\n"


def serial_wrapper_rtl(
    platform: Platform,
    fpga: str,
    sites: Sequence[Mapping[str, Any]],
    board_services: Optional[Mapping[str, Any]] = None,
    provider_schema: Optional[str] = None,
    transceiver_quads: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    board_services = board_services or {
        "reference_clocks": [], "resets": [], "clock_reset_domains": []
    }
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
    for clock in board_services["reference_clocks"]:
        p, n = _clock_ports(clock["id"])
        ports.extend((f"  input  wire {p}", f"  input  wire {n}"))
    for reset_binding in board_services["resets"]:
        ports.append(f"  input  wire {_reset_port(reset_binding['id'])}")
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
    domain_wires = {}
    for index, domain in enumerate(board_services["clock_reset_domains"]):
        stem = f"service_{index}"
        clock = next(
            item
            for item in board_services["reference_clocks"]
            if item["id"] == domain["reference_clock_binding"]
        )
        reset_binding = next(
            item
            for item in board_services["resets"]
            if item["id"] == domain["reset_binding"]
        )
        clock_p, clock_n = _clock_ports(clock["id"])
        reset_port = _reset_port(reset_binding["id"])
        active_low = 1 if reset_binding["polarity"] == "active_low" else 0
        lines.extend(
            [
                f"  wire {stem}_phy_refclk;",
                f"  wire {stem}_phy_reset;",
                f"  wire {stem}_ready;",
                f"  {SERIAL_CLOCK_RESET_MODULE} #(",
                f"    .BOARD_RESET_ACTIVE_LOW({active_low})",
                f"  ) {stem}_clock_reset (",
                f"    .refclk_p({clock_p}),",
                f"    .refclk_n({clock_n}),",
                f"    .board_reset({reset_port}),",
                f"    .phy_refclk({stem}_phy_refclk),",
                f"    .phy_reset({stem}_phy_reset),",
                f"    .ready({stem}_ready)",
                "  );",
                "",
            ]
        )
        domain_wires[domain["id"]] = stem
        ready_wires.append(f"{stem}_ready")
    quad_mode = provider_schema == SERIAL_PHY_PROVIDER_V2_SCHEMA
    if quad_mode:
        quads = list(transceiver_quads or [])
        covered_sites = {
            channel["site_index"]
            for quad in quads
            for channel in quad["channels"]
        }
        if covered_sites != set(range(len(sites))):
            raise ValidationError(
                "quad-aware serial wrapper requires a complete GT common map"
            )
        for quad_index, quad in enumerate(quads):
            stem = f"quad_{quad_index}"
            channel_by_index = {
                channel["channel_index"]: channel
                for channel in quad["channels"]
            }
            widths = {
                sites[channel["site_index"]]["payload_width"]
                for channel in quad["channels"]
            }
            if len(widths) != 1:
                raise ValidationError("GT quad channels use different payload widths")
            width = widths.pop()
            active_mask = sum(1 << channel for channel in channel_by_index)
            domain_stem = domain_wires.get(quad.get("clock_reset_domain"))
            phy_refclk = (
                f"{domain_stem}_phy_refclk"
                if domain_stem is not None
                else "1'b0"
            )
            phy_reset = (
                f"{domain_stem}_phy_reset"
                if domain_stem is not None
                else "reset"
            )
            tx_pieces = []
            rxp_pieces = []
            rxn_pieces = []
            for channel_index in reversed(range(4)):
                channel = channel_by_index.get(channel_index)
                if channel is None:
                    tx_pieces.append(f"{width}'b0")
                    rxp_pieces.append("1'b0")
                    rxn_pieces.append("1'b0")
                    continue
                site = sites[channel["site_index"]]
                link = next(item for item in incident if item.id == site["link"])
                suffix = f"{_sv_name(link.id)}_{_sv_name(site['peer'])}"
                lower = site["physical_lane"] * width
                tx_pieces.append(
                    f"tx_{suffix}[{lower} +: {width}]"
                    if site["tx"] is not None
                    else f"{width}'b0"
                )
                rxp_pieces.append(
                    site["rx"]["ports"]["p"]
                    if site["rx"] is not None
                    else "1'b0"
                )
                rxn_pieces.append(
                    site["rx"]["ports"]["n"]
                    if site["rx"] is not None
                    else "1'b0"
                )
            lines.extend(
                [
                    f"  wire [{4 * width - 1}:0] {stem}_tx_data;",
                    f"  wire [{4 * width - 1}:0] {stem}_rx_data;",
                    f"  wire [3:0] {stem}_txp;",
                    f"  wire [3:0] {stem}_txn;",
                    f"  wire [3:0] {stem}_rxp;",
                    f"  wire [3:0] {stem}_rxn;",
                    f"  wire [3:0] {stem}_lane_ready;",
                    f"  wire {stem}_common_ready;",
                    f"  assign {stem}_tx_data = {{{', '.join(tx_pieces)}}};",
                    f"  assign {stem}_rxp = {{{', '.join(rxp_pieces)}}};",
                    f"  assign {stem}_rxn = {{{', '.join(rxn_pieces)}}};",
                    f"  {SERIAL_PHY_QUAD_MODULE} #(",
                    f"    .PAYLOAD_WIDTH({width}),",
                    f"    .ACTIVE_CHANNEL_MASK(4'b{active_mask:04b})",
                    f"  ) {stem}_phy (",
                    "    .user_clk(fabric_clk),",
                    "    .reset(reset),",
                    f"    .phy_refclk({phy_refclk}),",
                    f"    .phy_reset({phy_reset}),",
                    f"    .tx_data({stem}_tx_data),",
                    f"    .rx_data({stem}_rx_data),",
                    f"    .txp({stem}_txp),",
                    f"    .txn({stem}_txn),",
                    f"    .rxp({stem}_rxp),",
                    f"    .rxn({stem}_rxn),",
                    f"    .lane_ready({stem}_lane_ready),",
                    f"    .common_ready({stem}_common_ready)",
                    "  );",
                    "",
                ]
            )
            ready_wires.append(f"{stem}_common_ready")
            for channel_index, channel in sorted(channel_by_index.items()):
                site_index = channel["site_index"]
                site = sites[site_index]
                lines.append(
                    f"  wire [{width - 1}:0] site_{site_index}_rx_data;"
                )
                lines.append(
                    f"  assign site_{site_index}_rx_data = "
                    f"{stem}_rx_data[{channel_index * width} +: {width}];"
                )
                if site["tx"] is not None:
                    lines.extend(
                        [
                            f"  assign {site['tx']['ports']['p']} = "
                            f"{stem}_txp[{channel_index}];",
                            f"  assign {site['tx']['ports']['n']} = "
                            f"{stem}_txn[{channel_index}];",
                        ]
                    )
                ready_wires.append(f"{stem}_lane_ready[{channel_index}]")
            lines.append("")
    else:
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
            domain_stem = domain_wires.get(site.get("clock_reset_domain"))
            phy_refclk = (
                f"{domain_stem}_phy_refclk" if domain_stem is not None else "1'b0"
            )
            phy_reset = (
                f"{domain_stem}_phy_reset" if domain_stem is not None else "reset"
            )
            lines.extend(
                [
                    f"  wire [{width - 1}:0] {stem}_rx_data;",
                    f"  wire {stem}_ready;",
                    f"  {SERIAL_PHY_MODULE} #(",
                    f"    .PAYLOAD_WIDTH({width})",
                    f"  ) {stem}_phy (",
                    "    .user_clk(fabric_clk),",
                    "    .reset(reset),",
                    f"    .phy_refclk({phy_refclk}),",
                    f"    .phy_reset({phy_reset}),",
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
    board_services: Optional[Mapping[str, Any]] = None,
    runtime_sync_node: Optional[Mapping[str, Any]] = None,
    runtime_sync_parameters: Optional[Mapping[str, Any]] = None,
) -> str:
    board_services = board_services or {
        "reference_clocks": [], "resets": [], "clock_reset_domains": []
    }
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
    if runtime_sync_node is not None:
        if runtime_sync_parameters is None:
            raise ValidationError("runtime sync node requires topology parameters")
        child_ports = max(1, len(runtime_sync_node["children"]))
        epoch_bits = runtime_sync_parameters["epoch_bits"]
        ports.extend(
            [
                f"  input  wire [{child_ports - 1}:0] sync_child_subtree_ready",
                "  input  wire sync_parent_start_valid",
                f"  input  wire [{epoch_bits - 1}:0] sync_parent_start_epoch",
                "  output wire sync_subtree_ready",
                "  output wire sync_child_start_valid",
                f"  output wire [{epoch_bits - 1}:0] sync_child_start_epoch",
                "  output wire sync_faulted",
            ]
        )
    for clock in board_services["reference_clocks"]:
        p, n = _clock_ports(clock["id"])
        ports.extend((f"  input  wire {p}", f"  input  wire {n}"))
    for reset_binding in board_services["resets"]:
        ports.append(f"  input  wire {_reset_port(reset_binding['id'])}")
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
        "  wire local_links_ready;",
        "  wire links_ready;",
    ]
    if runtime_sync_node is None:
        lines.extend(["  assign links_ready = local_links_ready;", ""])
    else:
        child_ports = max(1, len(runtime_sync_node["children"]))
        active_mask = (1 << len(runtime_sync_node["children"])) - 1
        parameters = runtime_sync_parameters
        lines.extend(
            [
                f"  wire [{parameters['epoch_bits'] - 1}:0] sync_epoch_debug;",
                f"  {RUNTIME_SYNC_NODE_MODULE} #(",
                f"    .EPOCH_BITS({parameters['epoch_bits']}),",
                f"    .CHILD_PORTS({child_ports}),",
                f"    .ACTIVE_CHILD_MASK({child_ports}'h{active_mask:x}),",
                f"    .IS_ROOT({1 if runtime_sync_node['role'] == 'root' else 0}),",
                f"    .START_MARGIN_CYCLES({parameters['start_margin_cycles']}),",
                f"    .READY_STABLE_CYCLES({parameters['ready_stable_cycles']})",
                "  ) runtime_sync (",
                "    .fabric_clk(fabric_clk), .reset(reset),",
                "    .local_ready(local_links_ready),",
                "    .child_subtree_ready(sync_child_subtree_ready),",
                "    .parent_start_valid(sync_parent_start_valid),",
                "    .parent_start_epoch(sync_parent_start_epoch),",
                "    .subtree_ready(sync_subtree_ready),",
                "    .child_start_valid(sync_child_start_valid),",
                "    .child_start_epoch(sync_child_start_epoch),",
                "    .global_ready(links_ready),",
                "    .faulted(sync_faulted), .epoch(sync_epoch_debug)",
                "  );",
                "",
            ]
        )
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
    for clock in board_services["reference_clocks"]:
        p, n = _clock_ports(clock["id"])
        wrapper_ports.extend((f"    .{p}({p})", f"    .{n}({n})"))
    for reset_binding in board_services["resets"]:
        port = _reset_port(reset_binding["id"])
        wrapper_ports.append(f"    .{port}({port})")
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
    wrapper_ports.append("    .links_ready(local_links_ready)")
    lines.append(",\n".join(wrapper_ports))
    lines.extend(["  );", "", "endmodule", ""])
    return "\n".join(lines)


def build_serial_wrapper_manifest(
    platform: Platform,
    binding: Mapping[str, Any],
    transports: Optional[Mapping[str, Mapping[str, Any]]] = None,
    board_overlay: Optional[Mapping[str, Any]] = None,
    phy_provider: Optional[Mapping[str, Any]] = None,
    gt_site_map: Optional[Mapping[str, Any]] = None,
    runtime_sync_topology: Optional[Mapping[str, Any]] = None,
    runtime_sync_provider: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if (runtime_sync_topology is None) != (runtime_sync_provider is None):
        raise ValidationError(
            "runtime sync topology and provider must be supplied together"
        )
    runtime_sync_nodes = {}
    if runtime_sync_topology is not None:
        validate_runtime_sync_topology(
            runtime_sync_topology, platform, runtime_sync_provider
        )
        runtime_sync_nodes = {
            node["fpga"]: node for node in runtime_sync_topology["nodes"]
        }
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
    normalized_gt_site_map = (
        validate_vivado_pin_site_map(gt_site_map, platform)
        if gt_site_map is not None
        else None
    )
    derived_sites = (
        {
            (item["fpga"], item["link"], item["physical_lane"]): item
            for item in normalized_gt_site_map["transceiver_sites"]
        }
        if normalized_gt_site_map is not None
        else {}
    )
    resolved_sites = 0
    vendor_derived_sites = 0
    for fpga_sites in sites.values():
        for site in fpga_sites:
            key = (site["fpga"], site["link"], site["physical_lane"])
            derived = derived_sites.get(key)
            resolved = overlay_sites.get(
                key
            )
            if (
                derived is not None
                and resolved is not None
                and derived["site"] != resolved["site"]
            ):
                raise ValidationError("board overlay and Vivado GT site map disagree")
            if derived is not None:
                site["transceiver_site"] = derived["site"]
                site["transceiver_common_site"] = derived["common_site"]
                site["transceiver_site_status"] = "resolved_vendor_device_db"
                vendor_derived_sites += 1
            if resolved is not None:
                site["transceiver_site"] = resolved["site"]
                site["reference_clock_binding"] = resolved[
                    "reference_clock_binding"
                ]
                site["reset_binding"] = resolved["reset_binding"]
                if overlay_result["hardware_qualification"] == "source_backed":
                    site["transceiver_site_status"] = "resolved_source_backed"
                elif derived is None:
                    site["transceiver_site_status"] = "resolved_unverified"
                resolved_sites += 1
    if transports is not None and set(transports) != set(sites):
        raise ValidationError(
            "serial wrapper transports must cover every FPGA exactly once"
        )
    fpgas = []
    overlay_clock_bindings = (
        {item["id"]: item for item in overlay["reference_clocks"]}
        if overlay is not None
        else {}
    )
    overlay_reset_bindings = (
        {item["id"]: item for item in overlay["resets"]}
        if overlay is not None
        else {}
    )
    for fpga in sorted(platform.fpgas, key=lambda item: item.id):
        fpga_sites = sites[fpga.id]
        service_pairs = sorted(
            {
                (site["reference_clock_binding"], site["reset_binding"])
                for site in fpga_sites
                if "reference_clock_binding" in site
                and "reset_binding" in site
            }
        )
        domains = [
            {
                "id": f"clock_reset_domain_{index}",
                "reference_clock_binding": clock_id,
                "reset_binding": reset_id,
            }
            for index, (clock_id, reset_id) in enumerate(service_pairs)
        ]
        domain_by_pair = {
            (domain["reference_clock_binding"], domain["reset_binding"]): domain[
                "id"
            ]
            for domain in domains
        }
        for site in fpga_sites:
            pair = (
                site.get("reference_clock_binding"), site.get("reset_binding")
            )
            if pair in domain_by_pair:
                site["clock_reset_domain"] = domain_by_pair[pair]
        transceiver_quads = _transceiver_quad_groups(fpga_sites)
        fpga_board_services = {
            "reference_clocks": [
                overlay_clock_bindings[clock_id]
                for clock_id in sorted({pair[0] for pair in service_pairs})
            ],
            "resets": [
                overlay_reset_bindings[reset_id]
                for reset_id in sorted({pair[1] for pair in service_pairs})
            ],
            "clock_reset_domains": domains,
        }
        constraints_status = (
            "source_backed_emittable"
            if domains
            and overlay_result is not None
            and overlay_result["hardware_qualification"] == "source_backed"
            else "unverified_not_emitted"
            if domains
            else "not_provided"
        )
        hardware_implementation = (
            phy_provider["implementation"]
            if phy_provider is not None
            and phy_provider["qualification"] == "editable_source_hardware"
            else None
        )
        gt_site_constraints_status = (
            "trusted_emittable"
            if fpga_sites
            and hardware_implementation is not None
            and all(
                site["transceiver_site_status"]
                in {"resolved_source_backed", "resolved_vendor_device_db"}
                for site in fpga_sites
            )
            else "provider_or_site_binding_unresolved"
        )
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
                "board_services": fpga_board_services,
                "board_service_constraints_status": constraints_status,
                "gt_site_constraints_status": gt_site_constraints_status,
                "transceiver_quads": transceiver_quads,
                "sites": fpga_sites,
                **(
                    {"runtime_sync": runtime_sync_nodes[fpga.id]}
                    if runtime_sync_topology is not None
                    else {}
                ),
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
    source_backed_resolved_sites = (
        resolved_sites
        if overlay_result is not None
        and overlay_result["hardware_qualification"] == "source_backed"
        else 0
    )
    trusted_resolved_sites = sum(
        site["transceiver_site_status"]
        in {"resolved_source_backed", "resolved_vendor_device_db"}
        for item in fpgas
        for site in item["sites"]
    )
    source_backed_service_resolution = all(
        item["board_service_constraints_status"] == "source_backed_emittable"
        for item in fpgas
        if item["active_transceiver_sites"] > 0
    )
    provider_hardware_source_bound = (
        phy_provider is not None
        and phy_provider.get("schema") == SERIAL_PHY_PROVIDER_V2_SCHEMA
        and phy_provider.get("qualification") == "editable_source_hardware"
        and sum(len(item["transceiver_quads"]) for item in fpgas) > 0
        and trusted_resolved_sites == active_sites
    )
    provider_bound_modules = (
        sum(len(item["transceiver_quads"]) for item in fpgas)
        if phy_provider is not None
        and phy_provider.get("schema") == SERIAL_PHY_PROVIDER_V2_SCHEMA
        else active_sites
    )
    active_phy_modules = provider_bound_modules
    required_provider_fields = []
    if trusted_resolved_sites != active_sites:
        required_provider_fields.append("transceiver_site")
    if not source_backed_service_resolution:
        required_provider_fields.extend(
            ["reference_clock_selection", "reference_clock_package_binding"]
        )
    if not provider_hardware_source_bound:
        required_provider_fields.extend(
            [
                "reset_synchronization",
                "line_encoding",
                "reset_sequence",
                "link_training",
            ]
        )
        if (
            phy_provider is not None
            and phy_provider.get("schema") != SERIAL_PHY_PROVIDER_V2_SCHEMA
        ):
            required_provider_fields.append("quad_shared_common")
    required_provider_fields.extend(
        ["fabric_clock_phase_alignment", "synchronous_reset_release"]
    )
    if runtime_sync_topology is None:
        required_provider_fields.append("global_ready_consensus")
    else:
        required_provider_fields.append("runtime_sync_control_transport")
    implementation_status = (
        "editable_source_bound_pending_tool_validation"
        if provider_hardware_source_bound
        else "simulation_source_bound"
        if phy_provider is not None
        else "black_box_unresolved"
    )
    return {
        "schema": SERIAL_WRAPPER_SCHEMA,
        "status": (
            "provider_source_bound"
            if phy_provider is not None
            else "awaiting_external_phy_provider"
        ),
        "design": binding["design"],
        "platform": platform.name,
        "binding_provider": SERIAL_TRANSCEIVER_PROVIDER,
        "phy_contract": {
            "module": (
                SERIAL_PHY_QUAD_MODULE
                if phy_provider is not None
                and phy_provider.get("schema") == SERIAL_PHY_PROVIDER_V2_SCHEMA
                else SERIAL_PHY_MODULE
            ),
            "rtl": "external_serial_phy_contract.sv",
            "implementation_status": implementation_status,
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
            "provider": (
                {
                    "status": "source_inventory_bound",
                    "id": phy_provider["id"],
                    "qualification": phy_provider["qualification"],
                    "supported_parts": phy_provider["supported_parts"],
                    "modules": phy_provider["modules"],
                    "implementation": phy_provider["implementation"],
                    "protocol": phy_provider["protocol"],
                    "sources": phy_provider["sources"],
                    "provenance": phy_provider["provenance"],
                }
                if phy_provider is not None
                else {"status": "not_provided"}
            ),
        },
        "runtime_sync": (
            {
                "status": "source_inventory_bound",
                "provider": runtime_sync_provider["id"],
                "qualification": runtime_sync_provider["qualification"],
                "module": RUNTIME_SYNC_NODE_MODULE,
                "algorithm": runtime_sync_topology["algorithm"],
                "root": runtime_sync_topology["root"],
                "parameters": runtime_sync_topology["parameters"],
                "fault_policy": runtime_sync_topology["fault_policy"],
                "board_proof": runtime_sync_topology["board_proof"],
                "hardware_release_status": runtime_sync_topology[
                    "hardware_release_status"
                ],
            }
            if runtime_sync_topology is not None
            else {"status": "not_provided"}
        ),
        "fpgas": fpgas,
        "metrics": {
            "fpgas": len(fpgas),
            "active_transceiver_sites": sum(
                item["active_transceiver_sites"] for item in fpgas
            ),
            "active_transceiver_quads": sum(
                len(item["transceiver_quads"]) for item in fpgas
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
            "vendor_derived_transceiver_sites": vendor_derived_sites,
            "unresolved_transceiver_sites": (
                active_sites - trusted_resolved_sites
            ),
            "active_phy_modules": active_phy_modules,
            "unresolved_phy_modules": (
                active_phy_modules if phy_provider is None else 0
            ),
            "provider_source_bound_phy_modules": (
                provider_bound_modules if phy_provider is not None else 0
            ),
            "integrated_transport_shells": (
                len(fpgas) if transports is not None else 0
            ),
            "runtime_sync_nodes": len(runtime_sync_nodes),
        },
    }


def run_phase6c(
    platform_path: Path,
    binding_path: Path,
    output_dir: Path,
    transport_paths: Optional[Mapping[str, Path]] = None,
    board_overlay_path: Optional[Path] = None,
    phy_provider_path: Optional[Path] = None,
    gt_site_map_path: Optional[Path] = None,
    runtime_sync_topology_path: Optional[Path] = None,
    runtime_sync_provider_path: Optional[Path] = None,
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
    gt_site_map = None
    if gt_site_map_path is not None:
        raw_gt_site_map = read_json(gt_site_map_path)
        platform_digest = hashlib.sha256(platform_path.read_bytes()).hexdigest()
        if raw_gt_site_map.get("platform_sha256") != platform_digest:
            raise ValidationError("Vivado GT site map BoardDB hash mismatch")
        gt_site_map = validate_vivado_pin_site_map(raw_gt_site_map, platform)
    phy_provider_result = (
        validate_serial_phy_provider(
            read_json(phy_provider_path), phy_provider_path, platform
        )
        if phy_provider_path is not None
        else None
    )
    phy_provider = (
        phy_provider_result["normalized"]
        if phy_provider_result is not None
        else None
    )
    if (runtime_sync_topology_path is None) != (
        runtime_sync_provider_path is None
    ):
        raise ValidationError(
            "runtime sync topology and provider paths must be supplied together"
        )
    runtime_sync_provider = None
    runtime_sync_topology = None
    if runtime_sync_provider_path is not None:
        runtime_sync_provider = validate_runtime_sync_provider(
            read_json(runtime_sync_provider_path), runtime_sync_provider_path
        )["normalized"]
        runtime_sync_topology = read_json(runtime_sync_topology_path)
        validate_runtime_sync_topology(
            runtime_sync_topology, platform, runtime_sync_provider
        )
    manifest = build_serial_wrapper_manifest(
        platform,
        binding,
        transports,
        board_overlay,
        phy_provider,
        gt_site_map,
        runtime_sync_topology,
        runtime_sync_provider,
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
            platform,
            record["fpga"],
            record["sites"],
            record["board_services"],
            provider_schema=(
                phy_provider["schema"] if phy_provider is not None else None
            ),
            transceiver_quads=record["transceiver_quads"],
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
                record["board_services"],
                runtime_sync_node=record.get("runtime_sync"),
                runtime_sync_parameters=(
                    runtime_sync_topology["parameters"]
                    if runtime_sync_topology is not None
                    else None
                ),
            )
            shell_path = output_dir / record["integration_shell"]
            shell_path.write_text(shell, encoding="utf-8")
            if shell_path.read_text(encoding="utf-8") != shell:
                raise ValidationError(
                    "written serial integration shell does not agree"
                )
        if record["board_service_constraints_status"] == "source_backed_emittable":
            service_xdc = serial_board_service_xdc(record)
            service_xdc_path = output_dir / f"{record['fpga']}.board_services.xdc"
            service_xdc_path.write_text(service_xdc, encoding="utf-8")
            if service_xdc_path.read_text(encoding="utf-8") != service_xdc:
                raise ValidationError("written board service XDC does not agree")
        if record["gt_site_constraints_status"] == "trusted_emittable":
            assert phy_provider is not None
            gt_site_xdc = serial_gt_site_xdc(
                record, phy_provider["implementation"]
            )
            gt_site_xdc_path = output_dir / f"{record['fpga']}.gt_sites.xdc"
            gt_site_xdc_path.write_text(gt_site_xdc, encoding="utf-8")
            if gt_site_xdc_path.read_text(encoding="utf-8") != gt_site_xdc:
                raise ValidationError("written GT site XDC does not agree")
    digest = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    manifest["binding_sha256"] = digest
    rebuilt = build_serial_wrapper_manifest(
        platform,
        binding,
        transports,
        board_overlay,
        phy_provider,
        gt_site_map,
        runtime_sync_topology,
        runtime_sync_provider,
    )
    rebuilt["binding_sha256"] = digest
    if board_overlay_path is not None:
        overlay_digest = hashlib.sha256(board_overlay_path.read_bytes()).hexdigest()
        manifest["board_overlay_sha256"] = overlay_digest
        rebuilt["board_overlay_sha256"] = overlay_digest
    if phy_provider_path is not None:
        provider_digest = hashlib.sha256(phy_provider_path.read_bytes()).hexdigest()
        manifest["phy_provider_manifest_sha256"] = provider_digest
        rebuilt["phy_provider_manifest_sha256"] = provider_digest
        write_json(
            output_dir / "serial_phy_provider.normalized.json", phy_provider
        )
    if gt_site_map_path is not None:
        gt_site_map_digest = hashlib.sha256(gt_site_map_path.read_bytes()).hexdigest()
        manifest["vivado_gt_site_map_sha256"] = gt_site_map_digest
        rebuilt["vivado_gt_site_map_sha256"] = gt_site_map_digest
        write_json(output_dir / "vivado_pin_site_map.bound.json", gt_site_map)
    if runtime_sync_topology_path is not None:
        topology_digest = hashlib.sha256(
            runtime_sync_topology_path.read_bytes()
        ).hexdigest()
        provider_digest = hashlib.sha256(
            runtime_sync_provider_path.read_bytes()
        ).hexdigest()
        manifest["runtime_sync_topology_sha256"] = topology_digest
        rebuilt["runtime_sync_topology_sha256"] = topology_digest
        manifest["runtime_sync_provider_manifest_sha256"] = provider_digest
        rebuilt["runtime_sync_provider_manifest_sha256"] = provider_digest
        write_json(
            output_dir / "runtime_sync_topology.bound.json",
            runtime_sync_topology,
        )
        write_json(
            output_dir / "runtime_sync_provider.normalized.json",
            runtime_sync_provider,
        )
        runtime_sync_rtl = []
        for source_record in runtime_sync_provider["sources"]:
            if source_record["language"] not in {"systemverilog", "verilog"}:
                continue
            sync_source = Path(source_record["absolute_path"])
            relative = Path("runtime_sync_provider_sources") / source_record["path"]
            destination = output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                sync_source.read_text(encoding="utf-8"), encoding="utf-8"
            )
            runtime_sync_rtl.append(relative.as_posix())
    write_json(output_dir / "serial_wrapper_manifest.json", manifest)
    if read_json(output_dir / "serial_wrapper_manifest.json") != rebuilt:
        raise ValidationError("serial wrapper manifest is not reproducible")
    report = {
        "schema": SERIAL_WRAPPER_REPORT_SCHEMA,
        "phase": "6C",
        "status": "pass",
        "design": manifest["design"],
        "platform": platform.name,
        "hardware_release_status": (
            "pending_vivado_provider_validation"
            if not manifest["phy_contract"]["required_provider_fields"]
            and manifest["phy_contract"]["implementation_status"]
            == "editable_source_bound_pending_tool_validation"
            else "blocked_on_external_phy_provider"
        ),
        "validation": dict(manifest["metrics"]),
        "artifacts": {
            "manifest": "serial_wrapper_manifest.json",
            "phy_contract": "external_serial_phy_contract.sv",
            **(
                {
                    "phy_provider_inventory": (
                        "serial_phy_provider.normalized.json"
                    )
                }
                if phy_provider is not None
                else {}
            ),
            **(
                {"gt_site_map": "vivado_pin_site_map.bound.json"}
                if gt_site_map is not None
                else {}
            ),
            **(
                {
                    "runtime_sync_topology": "runtime_sync_topology.bound.json",
                    "runtime_sync_provider": (
                        "runtime_sync_provider.normalized.json"
                    ),
                    "runtime_sync_rtl": runtime_sync_rtl,
                }
                if runtime_sync_topology is not None
                else {}
            ),
            "wrappers": {
                item["fpga"]: item["rtl"] for item in manifest["fpgas"]
            },
            "board_service_xdc": {
                item["fpga"]: f"{item['fpga']}.board_services.xdc"
                for item in manifest["fpgas"]
                if item["board_service_constraints_status"]
                == "source_backed_emittable"
            },
            "gt_site_xdc": {
                item["fpga"]: f"{item['fpga']}.gt_sites.xdc"
                for item in manifest["fpgas"]
                if item["gt_site_constraints_status"]
                == "trusted_emittable"
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
