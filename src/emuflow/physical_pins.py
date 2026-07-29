"""Electrical-rule-aware physical package-pin binding."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .native_tools import resolve_native_executable
from .pin_planning import validate_pin_plan
from .platform import Platform


HARDWARE_BSP_SCHEMA = "emuflow.hardware-bsp/v1"
PACKAGE_PIN_BINDING_SCHEMA = "emuflow.package-pin-binding/v1"
PACKAGE_PIN_PROVIDER = "chimew-package-pin-min-cost-flow-v1"
PHASE6B_REPORT_SCHEMA = "emuflow.phase6b-report/v1"
_COST_SCALE = 1_000_000
_IOSTANDARD_VOLTAGES = {
    "LVCMOS12": 1.2,
    "LVCMOS15": 1.5,
    "LVCMOS18": 1.8,
    "LVCMOS25": 2.5,
    "LVCMOS33": 3.3,
}


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _domain(entry: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (entry["link"], entry["from"], entry["to"])


def _sv_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not name or name[0].isdigit():
        name = f"n_{name}"
    return name


def _port(
    link: str, fpga: str, peer: str, direction: str, lane: int
) -> str:
    del fpga
    return f"{direction}_{_sv_name(link)}_{_sv_name(peer)}[{lane}]"


def validate_hardware_bsp(
    bsp: Mapping[str, Any], platform: Platform
) -> Dict[str, Any]:
    if bsp.get("schema") != HARDWARE_BSP_SCHEMA:
        raise ValidationError(
            f"hardware BSP schema must be {HARDWARE_BSP_SCHEMA!r}"
        )
    if bsp.get("platform") != platform.name:
        raise ValidationError("hardware BSP does not match BoardDB")
    board = bsp.get("board")
    if not isinstance(board, dict) or board.get("qualification") not in {
        "synthetic_validation",
        "hardware_definition",
    }:
        raise ValidationError("hardware BSP qualification is invalid")
    fpga_records = bsp.get("fpgas")
    if (
        not isinstance(fpga_records, list)
        or any(not isinstance(item, dict) for item in fpga_records)
    ):
        raise ValidationError("hardware BSP fpgas must be an array")
    by_fpga = {item.get("id"): item for item in fpga_records}
    expected_parts = {fpga.id: fpga.part for fpga in platform.fpgas}
    if set(by_fpga) != set(expected_parts) or len(by_fpga) != len(fpga_records):
        raise ValidationError("hardware BSP must cover every FPGA exactly")

    pins: Dict[str, Mapping[str, Any]] = {}
    banks: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    package_pins = set()
    for fpga_id, expected_part in expected_parts.items():
        record = by_fpga[fpga_id]
        if record.get("part") != expected_part:
            raise ValidationError(f"{fpga_id} BSP part does not match BoardDB")
        raw_banks = record.get("banks")
        raw_pins = record.get("pins")
        if not isinstance(raw_banks, list) or not isinstance(raw_pins, list):
            raise ValidationError(f"{fpga_id} BSP banks/pins are malformed")
        for bank in raw_banks:
            if not isinstance(bank, dict):
                raise ValidationError(f"{fpga_id} BSP bank is invalid")
            bank_id = bank.get("id")
            key = (fpga_id, bank_id)
            if (
                not isinstance(bank_id, str)
                or not bank_id
                or key in banks
                or not _number(bank.get("voltage"))
                or not isinstance(bank.get("iostandards"), list)
                or not isinstance(bank.get("max_pins"), int)
                or bank["max_pins"] <= 0
            ):
                raise ValidationError(f"{fpga_id} BSP bank is invalid")
            banks[key] = bank
        bank_pin_counts: Dict[str, int] = defaultdict(int)
        for pin in raw_pins:
            if not isinstance(pin, dict):
                raise ValidationError(f"{fpga_id} BSP pin is invalid")
            pin_id = pin.get("id")
            package_pin = pin.get("package_pin")
            bank_id = pin.get("bank")
            if (
                not isinstance(pin_id, str)
                or not pin_id
                or pin_id in pins
                or not isinstance(package_pin, str)
                or not package_pin
                or (fpga_id, package_pin) in package_pins
                or (fpga_id, bank_id) not in banks
                or not isinstance(pin.get("directions"), list)
                or not set(pin["directions"]).issubset({"tx", "rx", "inout"})
                or not pin["directions"]
                or not isinstance(pin.get("iostandards"), list)
                or not isinstance(pin.get("connector"), str)
                or not pin["connector"]
                or not isinstance(pin.get("connector_pin"), (int, str))
                or not _number(pin.get("region_y"))
                or not 0.0 <= float(pin["region_y"]) <= 1.0
                or not isinstance(pin.get("reserved"), bool)
                or not isinstance(pin.get("clock_capable"), bool)
            ):
                raise ValidationError(f"{fpga_id} BSP pin is invalid")
            if pin.get("fpga") != fpga_id:
                raise ValidationError(f"BSP pin {pin_id!r} has wrong FPGA")
            pins[pin_id] = pin
            package_pins.add((fpga_id, package_pin))
            bank_pin_counts[bank_id] += 1
        for bank_id, count in bank_pin_counts.items():
            if count > banks[(fpga_id, bank_id)]["max_pins"]:
                raise ValidationError(
                    f"{fpga_id} bank {bank_id!r} exceeds declared capacity"
                )

    link_by_id = {link.id: link for link in platform.links}
    channels_document = bsp.get("channels")
    if not isinstance(channels_document, list):
        raise ValidationError("hardware BSP channels must be an array")
    channels: Dict[str, Mapping[str, Any]] = {}
    channel_pin_uses = set()
    domain_counts: Dict[Tuple[str, str, str], int] = defaultdict(int)
    for channel in channels_document:
        if not isinstance(channel, dict):
            raise ValidationError("hardware BSP channel is invalid")
        channel_id = channel.get("id")
        link_id = channel.get("link")
        source = channel.get("source")
        sink = channel.get("sink")
        source_pin = pins.get(channel.get("source_pin"))
        sink_pin = pins.get(channel.get("sink_pin"))
        iostandard = channel.get("iostandard")
        required_voltage = _IOSTANDARD_VOLTAGES.get(iostandard)
        source_bank = (
            banks.get((source, source_pin.get("bank")))
            if source_pin is not None
            else None
        )
        sink_bank = (
            banks.get((sink, sink_pin.get("bank")))
            if sink_pin is not None
            else None
        )
        if (
            not isinstance(channel_id, str)
            or not channel_id
            or channel_id in channels
            or link_id not in link_by_id
            or source_pin is None
            or sink_pin is None
            or source == sink
            or {source, sink} != set(link_by_id[link_id].endpoints)
            or source_pin.get("fpga") != source
            or sink_pin.get("fpga") != sink
            or "tx" not in source_pin["directions"]
            or "rx" not in sink_pin["directions"]
            or source_pin["reserved"]
            or sink_pin["reserved"]
            or not isinstance(iostandard, str)
            or required_voltage is None
            or iostandard not in source_pin["iostandards"]
            or iostandard not in sink_pin["iostandards"]
            or source_bank is None
            or sink_bank is None
            or iostandard not in source_bank["iostandards"]
            or iostandard not in sink_bank["iostandards"]
            or abs(float(source_bank["voltage"]) - required_voltage) > 1.0e-9
            or abs(float(sink_bank["voltage"]) - required_voltage) > 1.0e-9
            or source_pin.get("connector") != sink_pin.get("connector")
            or not _number(channel.get("max_frequency_mhz"))
            or channel["max_frequency_mhz"]
            < link_by_id[link_id].fabric_clock_mhz
            or not _number(channel.get("skew_ps"))
            or channel["skew_ps"] < 0
        ):
            raise ValidationError(
                f"hardware BSP channel {channel_id!r} is invalid"
            )
        used_pins = (source_pin["id"], sink_pin["id"])
        if any(pin in channel_pin_uses for pin in used_pins):
            raise ValidationError("a BSP pin is used by multiple channels")
        channel_pin_uses.update(used_pins)
        channels[channel_id] = channel
        domain_counts[(link_id, source, sink)] += 1
    return {
        "status": "pass",
        "qualification": board["qualification"],
        "fpgas": len(by_fpga),
        "banks": len(banks),
        "pins": len(pins),
        "channels": len(channels),
        "domains": len(domain_counts),
        "_pins": pins,
        "_banks": banks,
        "_channels": channels,
    }


def _anchor_index(
    anchor_documents: Mapping[str, Mapping[str, Any]],
    platform: Platform,
) -> Dict[str, Mapping[str, Any]]:
    expected_fpgas = {fpga.id for fpga in platform.fpgas}
    if set(anchor_documents) != expected_fpgas:
        raise ValidationError("virtual anchors must cover every FPGA exactly")
    result = {}
    for fpga in sorted(expected_fpgas):
        document = anchor_documents[fpga]
        if (
            document.get("schema") != "emuflow.virtual-io-anchors/v1"
            or document.get("platform") != platform.name
            or document.get("fpga") != fpga
            or not isinstance(document.get("anchors"), list)
        ):
            raise ValidationError(f"{fpga} virtual anchors are invalid")
        for anchor in document["anchors"]:
            anchor_id = anchor.get("id")
            if (
                not isinstance(anchor_id, str)
                or not anchor_id
                or anchor_id in result
                or anchor.get("binding_status") != "unbound"
            ):
                raise ValidationError("virtual anchor record is invalid")
            result[anchor_id] = anchor
    return result


def _build_demands(
    schedule: Mapping[str, Any],
    platform: Platform,
    positions: Mapping[str, Any],
    plan: Mapping[str, Any],
    anchor_documents: Mapping[str, Mapping[str, Any]],
    *,
    iostandard: str,
) -> list[Dict[str, Any]]:
    validate_pin_plan(schedule, platform, positions, plan)
    anchor_by_id = _anchor_index(anchor_documents, platform)
    schedule_by_id = {entry["id"]: entry for entry in schedule["entries"]}
    position_by_id = {
        entry["schedule_entry"]: entry for entry in positions["entries"]
    }
    plan_by_group: Dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for item in plan["entries"]:
        plan_by_group[item["group"]].append(item)
    maximum_ratio = max(entry["tdm_ratio"] for entry in schedule["entries"])
    expected_anchors = set()
    demands = []
    for group, items in sorted(plan_by_group.items()):
        entries = [schedule_by_id[item["schedule_entry"]] for item in items]
        domains = {_domain(entry) for entry in entries}
        lanes = {item["physical_lane"] for item in items}
        ratios = {entry["tdm_ratio"] for entry in entries}
        if len(domains) != 1 or len(lanes) != 1 or len(ratios) != 1:
            raise ValidationError(f"pin-plan group {group} is not homogeneous")
        link, source, sink = next(iter(domains))
        lane = next(iter(lanes))
        ratio = next(iter(ratios))
        source_anchor = f"{link}:{source}:tx:{lane}"
        sink_anchor = f"{link}:{sink}:rx:{lane}"
        for anchor_id, fpga, peer, direction in (
            (source_anchor, source, sink, "tx"),
            (sink_anchor, sink, source, "rx"),
        ):
            anchor = anchor_by_id.get(anchor_id)
            if (
                anchor is None
                or anchor.get("peer") != peer
                or anchor.get("direction") != direction
                or anchor.get("logical_lane") != lane
            ):
                raise ValidationError(
                    f"pin-plan group {group} has no matching {fpga} anchor"
                )
            expected_anchors.add(anchor_id)
        hints = [position_by_id[item["schedule_entry"]] for item in items]
        demands.append(
            {
                "id": f"{link}:{source}-to-{sink}:group-{group}",
                "group": group,
                "link": link,
                "source": source,
                "sink": sink,
                "physical_lane": lane,
                "ratio": ratio,
                "criticality": 1.0 + (maximum_ratio - ratio) / maximum_ratio,
                "source_y": sum(item["source_y"] for item in hints)
                / len(hints),
                "sink_y": sum(item["sink_y"] for item in hints) / len(hints),
                "source_anchor": source_anchor,
                "sink_anchor": sink_anchor,
                "iostandard": iostandard,
            }
        )
    if set(anchor_by_id) != expected_anchors:
        missing = sorted(set(anchor_by_id) - expected_anchors)
        raise ValidationError(
            "virtual anchors and pin-plan groups do not agree exactly; "
            f"unmatched={missing[:5]}"
        )
    return demands


def _edge_cost(
    demand: Mapping[str, Any],
    channel: Mapping[str, Any],
    pins: Mapping[str, Mapping[str, Any]],
    *,
    placement_weight: float,
    skew_weight: float,
) -> float:
    source_pin = pins[channel["source_pin"]]
    sink_pin = pins[channel["sink_pin"]]
    placement = demand["criticality"] * (
        abs(demand["source_y"] - source_pin["region_y"])
        + abs(demand["sink_y"] - sink_pin["region_y"])
    )
    return (
        placement_weight * placement
        + skew_weight * float(channel["skew_ps"]) / 1000.0
    )


def _compatible(
    demand: Mapping[str, Any], channel: Mapping[str, Any]
) -> bool:
    return (
        channel["link"] == demand["link"]
        and channel["source"] == demand["source"]
        and channel["sink"] == demand["sink"]
        and channel["iostandard"] == demand["iostandard"]
    )


def _run_solver(
    demands: Sequence[Mapping[str, Any]],
    channels: Sequence[Mapping[str, Any]],
    pins: Mapping[str, Mapping[str, Any]],
    *,
    executable: Optional[str],
    placement_weight: float,
    skew_weight: float,
) -> Tuple[Dict[int, Tuple[int, int]], int]:
    domains = sorted(
        {
            (item["link"], item["source"], item["sink"])
            for item in [*demands, *channels]
        }
    )
    domain_index = {domain: index for index, domain in enumerate(domains)}
    lines = ["EMUFLOW_BSP_PIN_SOLVER_INPUT_V1"]
    for index, demand in enumerate(demands):
        lines.append(
            f"DEMAND {index} "
            f"{domain_index[(demand['link'], demand['source'], demand['sink'])]}"
        )
    for index, channel in enumerate(channels):
        lines.append(
            f"CHANNEL {index} "
            f"{domain_index[(channel['link'], channel['source'], channel['sink'])]}"
        )
    edge_costs = {}
    for demand_index, demand in enumerate(demands):
        for channel_index, channel in enumerate(channels):
            if not _compatible(demand, channel):
                continue
            cost = int(
                round(
                    _edge_cost(
                        demand,
                        channel,
                        pins,
                        placement_weight=placement_weight,
                        skew_weight=skew_weight,
                    )
                    * _COST_SCALE
                )
            )
            edge_costs[(demand_index, channel_index)] = cost
            lines.append(f"EDGE {demand_index} {channel_index} {cost}")
    native = resolve_native_executable("emuflow_bsp_pin_solver", executable)
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        input_path = root / "input.txt"
        output_path = root / "output.txt"
        input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        completed = subprocess.run(
            [native, str(input_path), str(output_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise EmuFlowError(
                f"in-tree BSP pin solver failed with exit code "
                f"{completed.returncode}: {detail}"
            )
        output_lines = output_path.read_text(
            encoding="utf-8"
        ).splitlines()
    if (
        not output_lines
        or output_lines[0] != "EMUFLOW_BSP_PIN_SOLVER_OUTPUT_V1"
    ):
        raise EmuFlowError("BSP pin solver returned an invalid header")
    assignments = {}
    native_total = None
    for line in output_lines[1:]:
        fields = line.split()
        if fields[:1] == ["METRIC"] and len(fields) == 3:
            if int(fields[1]) != len(demands):
                raise EmuFlowError("BSP pin solver assigned wrong demand count")
            native_total = int(fields[2])
        elif fields[:1] == ["ASSIGN"] and len(fields) == 4:
            demand = int(fields[1])
            if demand in assignments:
                raise EmuFlowError("BSP pin solver returned duplicate demand")
            assignments[demand] = (int(fields[2]), int(fields[3]))
        else:
            raise EmuFlowError(
                f"BSP pin solver returned malformed line: {line}"
            )
    if set(assignments) != set(range(len(demands))) or native_total is None:
        raise EmuFlowError("BSP pin solver output is incomplete")
    if sum(value[1] for value in assignments.values()) != native_total:
        raise EmuFlowError("BSP pin solver objective does not agree")
    for demand, (channel, cost) in assignments.items():
        if edge_costs.get((demand, channel)) != cost:
            raise EmuFlowError("BSP pin solver selected an illegal edge")
    return assignments, native_total


def _identity_lane_baseline(
    demands: Sequence[Mapping[str, Any]],
    channels: Sequence[Mapping[str, Any]],
    pins: Mapping[str, Mapping[str, Any]],
    *,
    placement_weight: float,
    skew_weight: float,
) -> int:
    channels_by_domain: Dict[
        Tuple[str, str, str], list[Mapping[str, Any]]
    ] = defaultdict(list)
    for channel in channels:
        channels_by_domain[
            (channel["link"], channel["source"], channel["sink"])
        ].append(channel)
    for values in channels_by_domain.values():
        values.sort(key=lambda item: item["id"])
    used = set()
    total = 0
    for demand in sorted(
        demands,
        key=lambda item: (
            item["link"],
            item["source"],
            item["sink"],
            item["physical_lane"],
            item["id"],
        ),
    ):
        candidates = channels_by_domain[
            (demand["link"], demand["source"], demand["sink"])
        ]
        preferred = (
            candidates[demand["physical_lane"]]
            if demand["physical_lane"] < len(candidates)
            else None
        )
        if (
            preferred is None
            or preferred["id"] in used
            or not _compatible(demand, preferred)
        ):
            preferred = next(
                (
                    channel
                    for channel in candidates
                    if channel["id"] not in used
                    and _compatible(demand, channel)
                ),
                None,
            )
        if preferred is None:
            raise ValidationError(
                "identity-lane package-pin baseline is infeasible"
            )
        used.add(preferred["id"])
        total += int(
            round(
                _edge_cost(
                    demand,
                    preferred,
                    pins,
                    placement_weight=placement_weight,
                    skew_weight=skew_weight,
                )
                * _COST_SCALE
            )
        )
    return total


def build_package_pin_binding(
    schedule: Mapping[str, Any],
    platform: Platform,
    positions: Mapping[str, Any],
    plan: Mapping[str, Any],
    anchor_documents: Mapping[str, Mapping[str, Any]],
    bsp: Mapping[str, Any],
    *,
    executable: Optional[str] = None,
    iostandard: str = "LVCMOS18",
    placement_weight: float = 1.0,
    skew_weight: float = 1.0,
) -> Dict[str, Any]:
    if placement_weight < 0.0 or skew_weight < 0.0:
        raise ValueError("package-pin objective weights must be non-negative")
    bsp_validation = validate_hardware_bsp(bsp, platform)
    demands = _build_demands(
        schedule,
        platform,
        positions,
        plan,
        anchor_documents,
        iostandard=iostandard,
    )
    channels = sorted(bsp_validation["_channels"].values(), key=lambda x: x["id"])
    assignments, objective = _run_solver(
        demands,
        channels,
        bsp_validation["_pins"],
        executable=executable,
        placement_weight=placement_weight,
        skew_weight=skew_weight,
    )
    baseline_objective = _identity_lane_baseline(
        demands,
        channels,
        bsp_validation["_pins"],
        placement_weight=placement_weight,
        skew_weight=skew_weight,
    )
    entries = []
    for index, demand in enumerate(demands):
        channel_index, integer_cost = assignments[index]
        channel = channels[channel_index]
        source_pin = bsp_validation["_pins"][channel["source_pin"]]
        sink_pin = bsp_validation["_pins"][channel["sink_pin"]]
        entries.append(
            {
                **demand,
                "channel": channel["id"],
                "source_port": _port(
                    demand["link"],
                    demand["source"],
                    demand["sink"],
                    "tx",
                    demand["physical_lane"],
                ),
                "sink_port": _port(
                    demand["link"],
                    demand["sink"],
                    demand["source"],
                    "rx",
                    demand["physical_lane"],
                ),
                "source_package_pin": source_pin["package_pin"],
                "sink_package_pin": sink_pin["package_pin"],
                "source_bank": source_pin["bank"],
                "sink_bank": sink_pin["bank"],
                "connector": source_pin["connector"],
                "cost": integer_cost / _COST_SCALE,
            }
        )
    binding = {
        "schema": PACKAGE_PIN_BINDING_SCHEMA,
        "status": (
            "synthetic_validation"
            if bsp_validation["qualification"] == "synthetic_validation"
            else "planned_hardware"
        ),
        "design": schedule["design"],
        "platform": platform.name,
        "board": bsp["board"]["name"],
        "provider": PACKAGE_PIN_PROVIDER,
        "configuration": {
            "iostandard": iostandard,
            "cost_scale": _COST_SCALE,
        },
        "weights": {
            "placement": placement_weight,
            "skew": skew_weight,
        },
        "metrics": {
            "bindings": len(entries),
            "bound_anchor_endpoints": 2 * len(entries),
            "objective": objective / _COST_SCALE,
            "baseline_identity_lane_objective": (
                baseline_objective / _COST_SCALE
            ),
            "objective_improvement_percent": (
                100.0
                * (baseline_objective - objective)
                / baseline_objective
                if baseline_objective
                else 0.0
            ),
            "package_pin_collisions": 0,
            "electrical_rule_violations": 0,
        },
        "entries": entries,
    }
    validate_package_pin_binding(
        schedule,
        platform,
        positions,
        plan,
        anchor_documents,
        bsp,
        binding,
    )
    return binding


def validate_package_pin_binding(
    schedule: Mapping[str, Any],
    platform: Platform,
    positions: Mapping[str, Any],
    plan: Mapping[str, Any],
    anchor_documents: Mapping[str, Mapping[str, Any]],
    bsp: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> Dict[str, Any]:
    bsp_validation = validate_hardware_bsp(bsp, platform)
    if (
        binding.get("schema") != PACKAGE_PIN_BINDING_SCHEMA
        or binding.get("provider") != PACKAGE_PIN_PROVIDER
        or binding.get("design") != schedule.get("design")
        or binding.get("platform") != platform.name
        or binding.get("board") != bsp["board"]["name"]
    ):
        raise ValidationError("package-pin binding identity is invalid")
    expected_status = (
        "synthetic_validation"
        if bsp_validation["qualification"] == "synthetic_validation"
        else "planned_hardware"
    )
    if binding.get("status") != expected_status:
        raise ValidationError("package-pin binding qualification is invalid")
    configuration = binding.get("configuration")
    weights = binding.get("weights")
    if not isinstance(configuration, dict) or not isinstance(weights, dict):
        raise ValidationError("package-pin binding configuration is invalid")
    iostandard = configuration.get("iostandard")
    if configuration.get("cost_scale") != _COST_SCALE:
        raise ValidationError("package-pin binding cost scale is invalid")
    try:
        placement_weight = float(weights["placement"])
        skew_weight = float(weights["skew"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValidationError("package-pin binding weights are invalid") from error
    if placement_weight < 0.0 or skew_weight < 0.0:
        raise ValidationError("package-pin binding weights are negative")
    demands = _build_demands(
        schedule,
        platform,
        positions,
        plan,
        anchor_documents,
        iostandard=iostandard,
    )
    demand_by_id = {item["id"]: item for item in demands}
    raw = binding.get("entries")
    if not isinstance(raw, list) or any(not isinstance(x, dict) for x in raw):
        raise ValidationError("package-pin binding entries are malformed")
    by_id = {item.get("id"): item for item in raw}
    if set(by_id) != set(demand_by_id) or len(by_id) != len(raw):
        raise ValidationError("package-pin binding must cover demands exactly")
    used_channels = set()
    used_package_pins = set()
    objective = 0
    for demand_id, demand in demand_by_id.items():
        item = by_id[demand_id]
        channel = bsp_validation["_channels"].get(item.get("channel"))
        if channel is None or not _compatible(demand, channel):
            raise ValidationError(f"binding {demand_id!r} uses illegal channel")
        if channel["id"] in used_channels:
            raise ValidationError("package-pin channel collision")
        used_channels.add(channel["id"])
        source_pin = bsp_validation["_pins"][channel["source_pin"]]
        sink_pin = bsp_validation["_pins"][channel["sink_pin"]]
        expected = {
            **demand,
            "channel": channel["id"],
            "source_port": _port(
                demand["link"],
                demand["source"],
                demand["sink"],
                "tx",
                demand["physical_lane"],
            ),
            "sink_port": _port(
                demand["link"],
                demand["sink"],
                demand["source"],
                "rx",
                demand["physical_lane"],
            ),
            "source_package_pin": source_pin["package_pin"],
            "sink_package_pin": sink_pin["package_pin"],
            "source_bank": source_pin["bank"],
            "sink_bank": sink_pin["bank"],
            "connector": source_pin["connector"],
        }
        integer_cost = int(
            round(
                _edge_cost(
                    demand,
                    channel,
                    bsp_validation["_pins"],
                    placement_weight=placement_weight,
                    skew_weight=skew_weight,
                )
                * _COST_SCALE
            )
        )
        expected["cost"] = integer_cost / _COST_SCALE
        if item != expected:
            raise ValidationError(
                f"binding {demand_id!r} does not independently agree"
            )
        package_keys = (
            (demand["source"], source_pin["package_pin"]),
            (demand["sink"], sink_pin["package_pin"]),
        )
        if any(key in used_package_pins for key in package_keys):
            raise ValidationError("package-pin collision")
        used_package_pins.update(package_keys)
        objective += integer_cost
    metrics = binding.get("metrics")
    channels = sorted(
        bsp_validation["_channels"].values(), key=lambda item: item["id"]
    )
    baseline_objective = _identity_lane_baseline(
        demands,
        channels,
        bsp_validation["_pins"],
        placement_weight=placement_weight,
        skew_weight=skew_weight,
    )
    expected_metrics = {
        "bindings": len(raw),
        "bound_anchor_endpoints": 2 * len(raw),
        "objective": objective / _COST_SCALE,
        "baseline_identity_lane_objective": (
            baseline_objective / _COST_SCALE
        ),
        "objective_improvement_percent": (
            100.0
            * (baseline_objective - objective)
            / baseline_objective
            if baseline_objective
            else 0.0
        ),
        "package_pin_collisions": 0,
        "electrical_rule_violations": 0,
    }
    if metrics != expected_metrics:
        raise ValidationError("package-pin binding metrics do not agree")
    return {
        "status": "pass",
        "qualification": expected_status,
        **expected_metrics,
    }


def binding_to_xdc(
    binding: Mapping[str, Any], fpga: str
) -> str:
    synthetic = binding["status"] == "synthetic_validation"
    lines = [
        "# Generated by EmuFlow Phase 6B.",
        (
            "# SYNTHETIC VALIDATION BSP: do not use on hardware."
            if synthetic
            else "# Hardware BSP package-pin plan; vendor DRC is still required."
        ),
        "",
    ]
    records = []
    for item in binding["entries"]:
        if item["source"] == fpga:
            records.append(
                (
                    item["source_port"],
                    item["source_package_pin"],
                    item["iostandard"],
                    item["id"],
                )
            )
        if item["sink"] == fpga:
            records.append(
                (
                    item["sink_port"],
                    item["sink_package_pin"],
                    item["iostandard"],
                    item["id"],
                )
            )
    for port, package_pin, iostandard, demand in sorted(records):
        lines.extend(
            [
                f"# binding={demand}",
                f"set_property PACKAGE_PIN {package_pin} "
                f"[get_ports {{{port}}}]",
                f"set_property IOSTANDARD {iostandard} "
                f"[get_ports {{{port}}}]",
                "",
            ]
        )
    return "\n".join(lines)


def run_phase6b(
    schedule_path: Path,
    platform_path: Path,
    positions_path: Path,
    pin_plan_path: Path,
    anchor_paths: Mapping[str, Path],
    bsp_path: Path,
    output_dir: Path,
    *,
    executable: Optional[str] = None,
    iostandard: str = "LVCMOS18",
    placement_weight: float = 1.0,
    skew_weight: float = 1.0,
) -> Dict[str, Any]:
    schedule = read_json(schedule_path)
    platform = Platform.load(platform_path)
    positions = read_json(positions_path)
    plan = read_json(pin_plan_path)
    anchors = {fpga: read_json(path) for fpga, path in anchor_paths.items()}
    bsp = read_json(bsp_path)
    binding = build_package_pin_binding(
        schedule,
        platform,
        positions,
        plan,
        anchors,
        bsp,
        executable=executable,
        iostandard=iostandard,
        placement_weight=placement_weight,
        skew_weight=skew_weight,
    )
    validation = validate_package_pin_binding(
        schedule, platform, positions, plan, anchors, bsp, binding
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "package_pin_binding.json", binding)
    xdc_files = {}
    for fpga in sorted(anchor_paths):
        filename = f"{fpga}.package_pins.xdc"
        (output_dir / filename).write_text(
            binding_to_xdc(binding, fpga), encoding="utf-8"
        )
        xdc_files[fpga] = filename
    report = {
        "schema": PHASE6B_REPORT_SCHEMA,
        "phase": "6B",
        "status": "pass",
        "design": schedule["design"],
        "platform": platform.name,
        "board": bsp["board"]["name"],
        "qualification": binding["status"],
        "provider": PACKAGE_PIN_PROVIDER,
        "validation": validation,
        "artifacts": {
            "binding": "package_pin_binding.json",
            "xdc": xdc_files,
            "report": "phase6b_report.json",
        },
    }
    write_json(output_dir / "phase6b_report.json", report)
    return report
