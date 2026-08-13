"""Materialize an academic Chimew lookahead from an open physical prepass.

The public contest BoardDBs do not carry a real package-pin inventory.  This
adapter deliberately uses the routed baseline only as a *lookahead prepass*:
the OpenPARF placement and imported VTR architecture are converted into the
paper-facing crossing, RUDY, and bank/channel inputs.  The generated package
pins are explicitly virtual academic identities and must never be used as a
hardware BSP.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .chimew_bank_channel import (
    CHIMEW_BANK_CHANNEL_INPUT_PROVIDER,
    CHIMEW_BANK_CHANNEL_INPUT_SCHEMA,
)
from .chimew_grouping import (
    CHIMEW_ACADEMIC_CROSSING_PROVIDER,
    CHIMEW_CROSSING_SCHEMA,
    CHIMEW_TIMING_GUARD_PROVIDER,
    build_chimew_initial_groups,
    materialize_chimew_schedule_ratios,
)
from .chimew_phase6 import (
    CHIMEW_ELECTRICAL_MAP_PROVIDER,
    CHIMEW_ELECTRICAL_MAP_SCHEMA,
)
from .chimew_qualification import canonical_sha256
from .chimew_refinement import (
    CHIMEW_POSITION_PROVIDER,
    CHIMEW_POSITION_SCHEMA,
    refine_chimew_groups,
)
from .chimew_rudy import CHIMEW_RUDY_INPUT_PROVIDER, CHIMEW_RUDY_INPUT_SCHEMA
from .errors import ValidationError
from .io import read_json, write_json
from .platform import Platform


ACADEMIC_CHIMEW_LOOKAHEAD_SCHEMA = "emuflow.academic-chimew-lookahead/v1"
ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER = (
    "openparf-vtr-baseline-lookahead+virtual-electrical-map-v1"
)
ACADEMIC_CHIMEW_TIMING_WEIGHT_PROVIDER = (
    "partition-projected-sta-criticality-v1"
)


_VTR_INSTANCE_ATOM_RE = re.compile(
    r"^i(?P<index>[0-9]+)(?:(?:__bit[0-9]+)|(?:__control))?$"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_vpr_placement(path: Path) -> Dict[str, Tuple[float, float]]:
    result: Dict[str, Tuple[float, float]] = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(
                ("Netlist_File:", "Array size:")
            ):
                continue
            fields = line.split()
            if len(fields) != 6 or not fields[5].startswith("#"):
                raise ValidationError(
                    f"{path}:{line_number}: malformed VPR lookahead placement"
                )
            if fields[0] in result:
                raise ValidationError(
                    f"{path}:{line_number}: duplicate placed block {fields[0]!r}"
                )
            result[fields[0]] = (float(fields[1]), float(fields[2]))
    if not result:
        raise ValidationError("academic Chimew lookahead placement is empty")
    return result


def _instance_locations(
    physical_report: Mapping[str, Any], output_dir: Path
) -> Tuple[
    Dict[str, Dict[str, Tuple[float, float, float, float]]],
    Dict[str, Tuple[float, float]],
    Dict[str, Dict[str, Tuple[float, float, float, float]]],
    Path,
    Path,
]:
    records = physical_report.get("fpgas")
    if not isinstance(records, list) or not records:
        raise ValidationError("academic Chimew prepass has no FPGA records")
    placement_source = output_dir / "sources" / "placement.json"
    architecture_source = output_dir / "sources" / "architecture.json"
    placement_records = []
    architecture_records = []
    locations: Dict[
        str, Dict[str, Tuple[float, float, float, float]]
    ] = {}
    boundary_locations: Dict[
        str, Dict[str, Tuple[float, float, float, float]]
    ] = {}
    y_bounds: Dict[str, Tuple[float, float]] = {}
    for record in records:
        if not isinstance(record, Mapping) or record.get("status") != "pass":
            raise ValidationError("academic Chimew prepass FPGA did not pass")
        fpga = record.get("fpga")
        stages = record.get("stages")
        if not isinstance(fpga, str) or not isinstance(stages, Mapping):
            raise ValidationError("academic Chimew prepass FPGA is malformed")
        placement_stage = stages.get("openparf_placement")
        packed_stage = stages.get("packed_contract")
        lowering = stages.get("placement_ir")
        if not all(
            isinstance(stage, Mapping)
            for stage in (placement_stage, packed_stage, lowering)
        ):
            raise ValidationError("academic Chimew prepass lacks open placement")
        placement_path = Path(placement_stage["artifacts"]["vpr_placement"])
        packed_path = Path(packed_stage["output"])
        fpga_root = Path(lowering["output"]).parent
        architecture_path = fpga_root / "architecture.json"
        for path in (placement_path, packed_path, architecture_path):
            if not path.is_file():
                raise ValidationError(
                    f"academic Chimew prepass artifact is missing: {path}"
                )
        cluster_locations = _parse_vpr_placement(placement_path)
        packed = read_json(packed_path)
        placement_ir = read_json(Path(lowering["output"]))
        raw_instances = placement_ir.get("instances")
        if not isinstance(raw_instances, list) or not raw_instances:
            raise ValidationError(
                f"academic Chimew prepass FPGA {fpga!r} has no placement instances"
            )
        instance_order = []
        for index, instance in enumerate(raw_instances):
            instance_id = instance.get("id") if isinstance(instance, Mapping) else None
            if not isinstance(instance_id, str) or not instance_id:
                raise ValidationError(
                    f"academic Chimew placement instance {index} is malformed"
                )
            instance_order.append(instance_id)
        if len(instance_order) != len(set(instance_order)):
            raise ValidationError(
                f"academic Chimew prepass FPGA {fpga!r} has duplicate instances"
            )
        known_instances = set(instance_order)
        instance_points: Dict[str, list[Tuple[float, float]]] = defaultdict(list)
        unmapped_atoms = 0
        for cluster in packed.get("clusters", []):
            point = cluster_locations.get(cluster.get("name"))
            if point is None:
                raise ValidationError(
                    f"packed cluster {cluster.get('name')!r} has no placement"
                )
            for atom in cluster.get("atoms", []):
                instance_id = atom if atom in known_instances else None
                if instance_id is None and isinstance(atom, str):
                    match = _VTR_INSTANCE_ATOM_RE.fullmatch(atom)
                    if match is not None:
                        instance_index = int(match.group("index"))
                        if instance_index < len(instance_order):
                            instance_id = instance_order[instance_index]
                if instance_id is None:
                    # VPR may retain constant/helper atoms which are not
                    # source EmuIR instances. They cannot be timing endpoints.
                    unmapped_atoms += 1
                    continue
                instance_points[instance_id].append(point)
        if not instance_points:
            raise ValidationError(
                f"academic Chimew prepass FPGA {fpga!r} has no source-mapped atoms"
            )
        raw_instance_map = {
            instance: (
                sum(point[0] for point in points) / len(points),
                sum(point[1] for point in points) / len(points),
            )
            for instance, points in instance_points.items()
        }
        x_values = [point[0] for point in raw_instance_map.values()]
        y_values = [point[1] for point in raw_instance_map.values()]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        y_bounds[fpga] = (y_min, y_max if y_max > y_min else y_min + 1.0)

        def _normalise(value: float, lower: float, upper: float) -> float:
            return 0.5 if upper == lower else (value - lower) / (upper - lower)

        instance_map = {
            atom: (
                point[0],
                point[1],
                _normalise(point[0], x_min, x_max),
                _normalise(point[1], y_min, y_max),
            )
            for atom, point in raw_instance_map.items()
        }
        locations[fpga] = instance_map
        boundary_path = lowering.get("boundary_identity", {}).get("output")
        if not isinstance(boundary_path, str) or not Path(boundary_path).is_file():
            raise ValidationError(
                f"academic Chimew prepass FPGA {fpga!r} has no boundary identity"
            )
        boundary_document = read_json(Path(boundary_path))
        boundary_map: Dict[str, Tuple[float, float, float, float]] = {}
        for endpoint in boundary_document.get("endpoints", []):
            if not isinstance(endpoint, Mapping):
                raise ValidationError("academic Chimew boundary endpoint is malformed")
            entry_id = endpoint.get("schedule_entry")
            registers = endpoint.get("merged_ir", {}).get(
                "boundary_register_instances"
            )
            if not isinstance(entry_id, str) or not isinstance(registers, list):
                raise ValidationError("academic Chimew boundary endpoint is incomplete")
            points = [instance_map.get(instance) for instance in registers]
            present = [point for point in points if point is not None]
            if not present:
                # Some boundary identities are not exposed by the current
                # schedule (for example a superseded lane endpoint).  Keep
                # only placed identities here and require coverage when an
                # active entry actually needs the forwarding fallback below.
                continue
            point = (
                sum(value[0] for value in present) / len(present),
                sum(value[1] for value in present) / len(present),
                sum(value[2] for value in present) / len(present),
                sum(value[3] for value in present) / len(present),
            )
            if entry_id in boundary_map:
                raise ValidationError(
                    f"academic Chimew boundary entry {entry_id!r} is duplicated"
                )
            boundary_map[entry_id] = point
        boundary_locations[fpga] = boundary_map
        placement_records.append(
            {
                "fpga": fpga,
                "placement_sha256": _sha256(placement_path),
                "packed_contract_sha256": _sha256(packed_path),
                "raw_bounds": {
                    "x_min": x_min,
                    "x_max": x_max,
                    "y_min": y_min,
                    "y_max": y_max,
                },
                "instances": [
                    {
                        "id": instance,
                        "raw_x": point[0],
                        "raw_y": point[1],
                        "normalised_x": point[2],
                        "normalised_y": point[3],
                    }
                    for instance, point in sorted(instance_map.items())
                ],
                "atom_mapping": {
                    "provider": "vtr-eblif-deterministic-instance-order-v1",
                    "source_instances": len(instance_order),
                    "placed_source_instances": len(instance_map),
                    "unmapped_helper_atoms": unmapped_atoms,
                },
            }
        )
        architecture_records.append(
            {
                "fpga": fpga,
                "sha256": _sha256(architecture_path),
                "architecture": read_json(architecture_path),
            }
        )
    placement_source.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        placement_source,
        {
            "schema": "emuflow.academic-lookahead-placement-source/v1",
            "provider": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "fpgas": placement_records,
        },
    )
    write_json(
        architecture_source,
        {
            "schema": "emuflow.academic-lookahead-architecture-source/v1",
            "provider": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "fpgas": architecture_records,
        },
    )
    return (
        locations,
        y_bounds,
        boundary_locations,
        placement_source,
        architecture_source,
    )


def _centroid(
    fpga: str,
    instances: list[str],
    locations: Mapping[
        str, Mapping[str, Tuple[float, float, float, float]]
    ],
) -> Tuple[float, float, float, bool]:
    points = [locations.get(fpga, {}).get(instance) for instance in instances]
    present = [point for point in points if point is not None]
    if not present:
        return 0.5, 0.5, 0.5, True
    return (
        sum(point[0] for point in present) / len(present),
        sum(point[1] for point in present) / len(present),
        sum(point[3] for point in present) / len(present),
        False,
    )


def _crossed_boundaries(value: float, anchor: float, count: int) -> list[int]:
    if count <= 0:
        return []
    lower, upper = sorted((value, anchor))
    return [
        boundary
        for boundary in range(count)
        if lower < float(boundary + 1) / float(count + 1) <= upper
    ]


def _timing_weights(
    timing_paths_path: Optional[Path],
    schedule: Mapping[str, Any],
    routes: Mapping[str, Any],
    *,
    scale: float = 9.0,
    path_scope: str = "exact-hop",
) -> Tuple[Dict[str, float], Optional[Path], Dict[str, int]]:
    """Return a stable per-entry timing weight for the EmuFlow extension.

    Chimew's published geometric assignment treats signals uniformly.  The
    open-flow integration may additionally bind partition-projected STA paths
    and use the same bounded power-law criticality employed by EmuFlow's
    timing-driven partitioner.  The native kernel still performs the complete
    matching; this adapter merely materializes the source-bound weights.
    """

    if not math.isfinite(scale) or scale < 0.0:
        raise ValidationError("academic Chimew timing weight scale is invalid")
    if path_scope not in {"exact-hop", "whole-net"}:
        raise ValidationError("academic Chimew timing path scope is invalid")
    if timing_paths_path is None:
        return {}, None, {"exact_path_hops": 0, "whole_net_fallbacks": 0}
    document = read_json(timing_paths_path)
    if document.get("schema") != "emuflow.sta-paths/v1":
        raise ValidationError("academic Chimew timing path schema is invalid")
    if document.get("design") != schedule.get("design"):
        raise ValidationError("academic Chimew timing path design differs")
    entries_by_net: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for entry in schedule.get("entries", []):
        entries_by_net[entry["net"]].append(entry)
    route_timing_paths = {
        item["path"]: item
        for item in routes.get("timing", {}).get("paths", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    route_by_net = {
        item["net"]: item
        for item in routes.get("routes", [])
        if isinstance(item, Mapping) and isinstance(item.get("net"), str)
    }
    criticality: Dict[str, float] = {}
    paths = document.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValidationError("academic Chimew timing paths are empty")
    validated_paths: list[Tuple[Mapping[str, Any], float]] = []
    negative_deficits: list[float] = []
    for index, path in enumerate(paths):
        if not isinstance(path, Mapping):
            raise ValidationError(
                f"academic Chimew timing paths[{index}] is invalid"
            )
        period = path.get("clock_period_ns")
        slack = path.get("slack_ns")
        cut_nets = path.get("cut_nets")
        if (
            isinstance(period, bool)
            or not isinstance(period, (int, float))
            or not math.isfinite(float(period))
            or not float(period) > 0.0
            or isinstance(slack, bool)
            or not isinstance(slack, (int, float))
            or not math.isfinite(float(slack))
            or not isinstance(cut_nets, list)
        ):
            raise ValidationError(
                f"academic Chimew timing paths[{index}] is malformed"
            )
        normalized = path.get("normalized_slack")
        if normalized is None:
            normalized = float(slack) / float(period)
        if (
            isinstance(normalized, bool)
            or not isinstance(normalized, (int, float))
            or not math.isfinite(float(normalized))
        ):
            raise ValidationError(
                f"academic Chimew timing paths[{index}] has invalid normalized slack"
            )
        normalized_value = float(normalized)
        validated_paths.append((path, normalized_value))
        negative_deficits.append(max(0.0, -normalized_value))
    maximum_deficit = max(negative_deficits, default=0.0)
    exact_path_hops = 0
    whole_net_fallbacks = 0
    for path, normalized_slack in validated_paths:
        if maximum_deficit > 0.0:
            value = max(0.0, -normalized_slack) / maximum_deficit
        else:
            period = float(path["clock_period_ns"])
            slack = float(path["slack_ns"])
            value = max(0.0, min(1.0, 1.0 - slack / period))
        selected_entries: set[str] = set()
        route_timing = (
            route_timing_paths.get(path.get("id"))
            if path_scope == "exact-hop"
            else None
        )
        if route_timing is not None:
            for transition in route_timing.get("cut_transitions", []):
                if not isinstance(transition, Mapping):
                    raise ValidationError(
                        "academic Chimew route timing transition is invalid"
                    )
                net = transition.get("net")
                source = transition.get("from")
                target = transition.get("to")
                route = route_by_net.get(net)
                if route is None:
                    raise ValidationError(
                        f"academic Chimew timing path route for {net!r} is absent"
                    )
                parents: Dict[str, Tuple[str, str]] = {}
                for edge in route.get("tree_edges", []):
                    if not isinstance(edge, Mapping):
                        raise ValidationError(
                            "academic Chimew route tree edge is invalid"
                        )
                    parents[edge["to"]] = (edge["from"], edge["link"])
                current = target
                while current != source:
                    parent = parents.get(current)
                    if parent is None:
                        raise ValidationError(
                            f"academic Chimew route tree does not reach {target!r}"
                        )
                    previous, link = parent
                    matches = [
                        entry["id"]
                        for entry in entries_by_net.get(net, [])
                        if entry.get("from") == previous
                        and entry.get("to") == current
                        and entry.get("link") == link
                    ]
                    if len(matches) != 1:
                        raise ValidationError(
                            "academic Chimew timing hop does not identify one schedule entry"
                        )
                    selected_entries.add(matches[0])
                    current = previous
            exact_path_hops += len(selected_entries)
        else:
            whole_net_fallbacks += 1
            for net in path["cut_nets"]:
                if not isinstance(net, str):
                    raise ValidationError(
                        "academic Chimew timing path has an invalid cut net"
                    )
                selected_entries.update(
                    entry["id"] for entry in entries_by_net.get(net, [])
                )
        for entry in selected_entries:
            criticality[entry] = max(criticality.get(entry, 0.0), value)
    weights = {
        entry["id"]: 1.0 + scale * criticality.get(entry["id"], 0.0) ** 2.0
        for entry in schedule.get("entries", [])
    }
    return weights, timing_paths_path, {
        "exact_path_hops": exact_path_hops,
        "whole_net_fallbacks": whole_net_fallbacks,
    }


def materialize_academic_chimew_inputs(
    *,
    ir_path: Path,
    schedule_path: Path,
    routes_path: Path,
    platform_path: Path,
    physical_report: Mapping[str, Any],
    output_dir: Path,
    timing_paths_path: Optional[Path] = None,
    timing_weight_scale: float = 9.0,
    timing_path_scope: str = "exact-hop",
    region_count: int = 4,
    grouper: Optional[str] = None,
    refiner: Optional[str] = None,
) -> Dict[str, Any]:
    """Build source-bound academic Chimew inputs from a baseline prepass."""

    if not 2 <= region_count <= 31:
        raise ValidationError("academic Chimew region count must be in [2, 31]")
    ir = read_json(ir_path)
    source_schedule = read_json(schedule_path)
    explicit_ratios = [
        "tdm_ratio" in entry for entry in source_schedule.get("entries", [])
    ]
    if any(explicit_ratios) and not all(explicit_ratios):
        raise ValidationError(
            "academic Chimew schedule mixes explicit and implicit TDM ratios"
        )
    schedule = (
        source_schedule
        if explicit_ratios and all(explicit_ratios)
        else materialize_chimew_schedule_ratios(source_schedule)
    )
    platform = Platform.load(platform_path)
    routes = read_json(routes_path)
    timing_weights, timing_source, timing_coverage = _timing_weights(
        timing_paths_path,
        schedule,
        routes,
        scale=timing_weight_scale,
        path_scope=timing_path_scope,
    )
    link_by_id = {link.id: link for link in platform.links}
    (
        locations,
        fpga_y_bounds,
        boundary_locations,
        placement_source,
        architecture_source,
    ) = _instance_locations(physical_report, output_dir)
    placement_mapping_records = read_json(placement_source)["fpgas"]
    total_placed_source_instances = sum(
        record["atom_mapping"]["placed_source_instances"]
        for record in placement_mapping_records
    )
    total_source_instances = sum(
        record["atom_mapping"]["source_instances"]
        for record in placement_mapping_records
    )
    total_unmapped_helper_atoms = sum(
        record["atom_mapping"]["unmapped_helper_atoms"]
        for record in placement_mapping_records
    )
    placement_sha = _sha256(placement_source)
    architecture_sha = _sha256(architecture_source)
    net_by_id = {net["id"]: net for net in ir["nets"]}
    fpga_order = {fpga.id: index for index, fpga in enumerate(platform.fpgas)}
    coordinate_scale = 1.0 + max(
        point[0]
        for per_fpga in locations.values()
        for point in per_fpga.values()
    )
    entry_points: Dict[str, Dict[str, Any]] = {}
    fallbacks = 0
    forwarded_boundary_endpoints = 0
    for entry in schedule.get("entries", []):
        net = net_by_id.get(entry.get("net"))
        if net is None:
            raise ValidationError(
                f"schedule entry {entry.get('id')!r} references an unknown net"
            )
        drivers = [
            endpoint["instance"]
            for endpoint in net["drivers"]
            if endpoint.get("instance") is not None
        ]
        sinks = [
            endpoint["instance"]
            for endpoint in net["sinks"]
            if endpoint.get("instance") is not None
        ]
        source_x, source_y, source_norm_y, source_fallback = _centroid(
            entry["from"], drivers, locations
        )
        if source_fallback:
            boundary = boundary_locations.get(entry["from"], {}).get(entry["id"])
            if boundary is not None:
                source_x, source_y, _source_norm_x, source_norm_y = boundary
                source_fallback = False
                forwarded_boundary_endpoints += 1
        sink_x, sink_y, sink_norm_y, sink_fallback = _centroid(
            entry["to"], sinks, locations
        )
        if sink_fallback:
            boundary = boundary_locations.get(entry["to"], {}).get(entry["id"])
            if boundary is not None:
                sink_x, sink_y, _sink_norm_x, sink_norm_y = boundary
                sink_fallback = False
                forwarded_boundary_endpoints += 1
        fallbacks += int(source_fallback) + int(sink_fallback)
        # Separate FPGA canvases in x while preserving physical-site y.
        source_x += fpga_order[entry["from"]] * coordinate_scale
        sink_x += fpga_order[entry["to"]] * coordinate_scale
        if source_x == sink_x:
            sink_x += 0.25
        entry_points[entry["id"]] = {
            "source": (source_x, source_y),
            "sink": (sink_x, sink_y),
            "source_normalised_y": source_norm_y,
            "sink_normalised_y": sink_norm_y,
            "fallback": source_fallback or sink_fallback,
        }

    routing_source = output_dir / "sources" / "routing.json"
    write_json(
        routing_source,
        {
            "schema": "emuflow.academic-lookahead-routing-source/v1",
            "provider": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "routes_sha256": _sha256(routes_path),
            "placement_sha256": placement_sha,
            "architecture_sha256": architecture_sha,
            "routes": routes,
        },
    )
    routing_sha = _sha256(routing_source)

    crossing_entries = []
    total_crossings = 0
    sll_count = region_count - 1
    for entry in schedule["entries"]:
        source_y = entry_points[entry["id"]]["source_normalised_y"]
        sink_y = entry_points[entry["id"]]["sink_normalised_y"]
        # Virtual academic package banks are evenly distributed by the
        # existing logical lane.  These are lookahead cuts, not final SLLs.
        link = link_by_id[entry["link"]]
        lanes = link.transport_bits_per_cycle_per_direction
        anchor = 0.5 if lanes == 1 else float(entry["lane"]) / float(lanes - 1)
        source_slls = _crossed_boundaries(source_y, anchor, sll_count)
        sink_slls = _crossed_boundaries(sink_y, anchor, sll_count)
        encoding = sum(1 << value for value in source_slls) | sum(
            1 << (sll_count + value) for value in sink_slls
        )
        total_crossings += len(source_slls) + len(sink_slls)
        crossing_entries.append(
            {
                "schedule_entry": entry["id"],
                "source_slls": source_slls,
                "sink_slls": sink_slls,
                "encoding": encoding,
            }
        )
    crossings = {
        "schema": CHIMEW_CROSSING_SCHEMA,
        "provider": CHIMEW_ACADEMIC_CROSSING_PROVIDER,
        "qualification": "academic-virtual-region-lookahead",
        "coordinate_system": "normalized-placement-y",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "slls_per_fpga": sll_count,
        "provenance": {
            "producer": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "producer_version": "1",
            "routing_sha256": routing_sha,
            "claim_boundary": (
                "virtual regions derived from normalized open-placement "
                "coordinates; not device SLR/SLL closure"
            ),
        },
        "metrics": {
            "signals": len(crossing_entries),
            "physical_sll_crossings": total_crossings,
        },
        "entries": crossing_entries,
    }
    positions = {
        "schema": CHIMEW_POSITION_SCHEMA,
        "provider": CHIMEW_POSITION_PROVIDER,
        "qualification": "academic-open-placement-lookahead",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "coordinate_system": "physical-site-y",
        "provenance": {
            "producer": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "producer_version": "1",
            "placement_sha256": placement_sha,
        },
        "metrics": {"signals": len(entry_points)},
        "entries": [
            {
                "schedule_entry": entry["id"],
                "source_y": entry_points[entry["id"]]["source"][1],
            }
            for entry in schedule["entries"]
        ],
    }
    maximum_timing_weight = max(timing_weights.values(), default=1.0)
    protected_entries = (
        {
            entry_id
            for entry_id, weight in timing_weights.items()
            if maximum_timing_weight > 1.0
            and math.isclose(
                weight,
                maximum_timing_weight,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
        }
        if timing_source is not None
        else set()
    )
    if protected_entries:
        schedule["chimew_timing_guard"] = {
            "provider": CHIMEW_TIMING_GUARD_PROVIDER,
            "scope": "EmuFlow extension, not a Chimew paper claim",
            "source_sha256": _sha256(timing_source),
            "maximum_weight": maximum_timing_weight,
            "protected_entries": sorted(protected_entries),
        }
    initial = build_chimew_initial_groups(
        schedule,
        crossings,
        executable=grouper,
    )
    refined = refine_chimew_groups(
        schedule, crossings, initial, positions, executable=refiner
    )
    group_by_entry = {
        item["schedule_entry"]: item["group"] for item in refined["entries"]
    }

    grouped: Dict[Tuple[str, str, int], list[Dict[str, Any]]] = defaultdict(list)
    for entry in schedule["entries"]:
        link = link_by_id[entry["link"]]
        endpoint_a, endpoint_b = link.endpoints
        direction = (
            "a_to_b"
            if (entry["from"], entry["to"]) == (endpoint_a, endpoint_b)
            else "b_to_a"
        )
        # Full-duplex/per-direction BoardDB links expose an independent lane
        # budget in each direction.  Keep those budgets in distinct Chimew
        # assignment domains: the paper-facing bank/channel optimizer does not
        # otherwise know an electrical channel's direction and would make the
        # two directions incorrectly compete for one lane pool.
        key = (entry["link"], direction, group_by_entry[entry["id"]])
        source = entry_points[entry["id"]]["source"]
        sink = entry_points[entry["id"]]["sink"]
        grouped[key].append(
            {
                "id": entry["id"],
                "fanout": {"x": source[0], "y": source[1]},
                "fanins": [{"x": sink[0], "y": sink[1]}],
                **(
                    {"timing_weight": timing_weights[entry["id"]]}
                    if timing_source is not None
                    else {}
                ),
            }
        )

    domains = []
    bank_pairs = []
    channels = []
    package_records = []
    for link_id in sorted({key[0] for key in grouped}):
        link = link_by_id[link_id]
        endpoint_a, endpoint_b = link.endpoints
        if link.direction != "full_duplex" or link.capacity_sharing != "per_direction":
            raise ValidationError(
                "academic Chimew default currently requires full-duplex, "
                f"per-direction BoardDB links; {link_id!r} is incompatible"
            )
        lane_count = link.transport_bits_per_cycle_per_direction
        a_y_min, a_y_max = fpga_y_bounds[endpoint_a]
        b_y_min, b_y_max = fpga_y_bounds[endpoint_b]
        active_directions = sorted(
            {key[1] for key in grouped if key[0] == link_id}
        )
        for direction in active_directions:
            domain_id = f"{link_id}:{direction}"
            bank_a_id = f"academic-{endpoint_a}-{domain_id}-bank"
            bank_b_id = f"academic-{endpoint_b}-{domain_id}-bank"
            domains.append(
                {"id": domain_id, "fpga_a": endpoint_a, "fpga_b": endpoint_b}
            )
            raw_channels = []
            for lane in range(lane_count):
                channel_id = (
                    f"academic-{link_id}-{direction}-channel-{lane:04d}"
                )
                fraction = (
                    0.5
                    if lane_count == 1
                    else float(lane) / float(lane_count - 1)
                )
                raw_channels.append(
                    {
                        "id": channel_id,
                        "order": lane,
                        "pin_a": {
                            "x": fpga_order[endpoint_a] * coordinate_scale,
                            "y": a_y_min + fraction * (a_y_max - a_y_min),
                        },
                        "pin_b": {
                            "x": fpga_order[endpoint_b] * coordinate_scale,
                            "y": b_y_min + fraction * (b_y_max - b_y_min),
                        },
                    }
                )
                # A full-duplex physical lane has distinct transmit/receive
                # package pins.  Direction-qualified synthetic identities keep
                # the academic model honest while allowing the BoardDB's
                # per-direction lane capacity to be used concurrently.
                pin_a = (
                    f"ACADEMIC_{endpoint_a}_{link_id}_{direction}_P{lane}"
                )
                pin_b = (
                    f"ACADEMIC_{endpoint_b}_{link_id}_{direction}_P{lane}"
                )
                package_records.extend(
                    [
                        {"fpga": endpoint_a, "pin": pin_a},
                        {"fpga": endpoint_b, "pin": pin_b},
                    ]
                )
                channels.append(
                    {
                        "chimew_channel": channel_id,
                        "link": link_id,
                        "physical_lane": lane,
                        "direction": direction,
                        "bank_a": bank_a_id,
                        "bank_b": bank_b_id,
                        "package_pin_a": pin_a,
                        "package_pin_b": pin_b,
                        "iostandard": "LVCMOS18",
                        "supported_iostandards": ["LVCMOS18"],
                        "bank_voltage": 1.8,
                        "electrical_class": "single_ended_parallel",
                        "reserved": False,
                    }
                )
            bank_pairs.append(
                {
                    "id": f"academic-{domain_id}-bank-pair",
                    "domain": domain_id,
                    "bank_a": {
                        "id": bank_a_id,
                        "x": fpga_order[endpoint_a] * coordinate_scale,
                        "y": (a_y_min + a_y_max) / 2.0,
                    },
                    "bank_b": {
                        "id": bank_b_id,
                        "x": fpga_order[endpoint_b] * coordinate_scale,
                        "y": (b_y_min + b_y_max) / 2.0,
                    },
                    "channels": raw_channels,
                }
            )

    package_source = output_dir / "sources" / "package-pins.json"
    write_json(
        package_source,
        {
            "schema": "emuflow.academic-virtual-package-pins/v1",
            "qualification": "synthetic-algorithm-validation-only",
            "pins": package_records,
        },
    )
    package_sha = _sha256(package_source)

    group_records = [
        {
            "id": f"academic-{link_id}-{direction}-group-{group_id}",
            "domain": f"{link_id}:{direction}",
            "kind": "tdm_group",
            "direction": direction,
            "members": members,
        }
        for (link_id, direction, group_id), members in sorted(grouped.items())
    ]
    bank_input = {
        "schema": CHIMEW_BANK_CHANNEL_INPUT_SCHEMA,
        "provider": CHIMEW_BANK_CHANNEL_INPUT_PROVIDER,
        "design": schedule["design"],
        "platform": schedule["platform"],
        "coordinate_system": "physical-site-xy",
        "cost_quantization_per_site": 1000,
        "provenance": {
            "producer": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "producer_version": "1",
            "grouping_sha256": canonical_sha256(refined),
            "placement_sha256": placement_sha,
            "architecture_sha256": architecture_sha,
        },
        "domains": domains,
        "bank_pairs": bank_pairs,
        "groups": group_records,
        "metrics": {
            "groups": len(group_records),
            "signals": len(schedule["entries"]),
            "fanins": len(schedule["entries"]),
            "bank_pairs": len(bank_pairs),
            "channels": len(channels),
        },
    }
    electrical_map = {
        "schema": CHIMEW_ELECTRICAL_MAP_SCHEMA,
        "provider": CHIMEW_ELECTRICAL_MAP_PROVIDER,
        "design": schedule["design"],
        "platform": schedule["platform"],
        "provenance": {
            "producer": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "producer_version": "1",
            "boarddb_sha256": _sha256(platform_path),
            "package_pin_inventory_sha256": package_sha,
        },
        "fpga_y_bounds": [
            {
                "fpga": fpga.id,
                "y_min": fpga_y_bounds[fpga.id][0],
                "y_max": fpga_y_bounds[fpga.id][1],
            }
            for fpga in platform.fpgas
        ],
        "channels": channels,
        "metrics": {
            "channels": len(channels),
            "package_pins": len(package_records),
            "concrete_lanes": len(channels),
        },
    }

    rudy_nets = []
    bbox_guard_points = 0
    for entry in schedule["entries"]:
        source = entry_points[entry["id"]]["source"]
        sink = entry_points[entry["id"]]["sink"]
        pins = [
            {"x": source[0], "y": source[1]},
            {"x": sink[0], "y": sink[1]},
        ]
        if source[1] == sink[1]:
            # Chimew's displayed RUDY equation has no zero-height convention.
            # Add an explicitly reported academic half-site guard point instead
            # of silently changing the paper kernel's reject policy.
            pins.append({"x": (source[0] + sink[0]) / 2.0, "y": source[1] + 0.5})
            bbox_guard_points += 1
        rudy_nets.append({"id": f"academic-{entry['id']}", "pins": pins})
    max_x = max(pin["x"] for net in rudy_nets for pin in net["pins"]) + 1.0
    max_y = max(pin["y"] for net in rudy_nets for pin in net["pins"]) + 1.0
    # The capacity is an explicit academic scaling policy.  It keeps RUDY a
    # comparative metric while avoiding a false real-device capacity claim.
    capacity = max(1.0, float(len(rudy_nets)) * (max_x + max_y) * 4.0)
    rudy_input = {
        "schema": CHIMEW_RUDY_INPUT_SCHEMA,
        "provider": CHIMEW_RUDY_INPUT_PROVIDER,
        "design": schedule["design"],
        "platform": schedule["platform"],
        "coordinate_system": "physical-site-xy",
        "degenerate_bbox_policy": "reject",
        "wire_pitch_per_layer": 1.0,
        "max_utilization": 1.0,
        "provenance": {
            "producer": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
            "producer_version": "1",
            "placement_sha256": placement_sha,
            "netlist_sha256": _sha256(ir_path),
            "architecture_sha256": architecture_sha,
        },
        "grid": {
            "origin_x": 0.0,
            "origin_y": 0.0,
            "bin_width": max_x,
            "bin_height": max_y,
            "columns": 1,
            "rows": 1,
            "capacities": [capacity],
        },
        "academic_bbox_guard_points": bbox_guard_points,
        "metrics": {
            "nets": len(rudy_nets),
            "pins": sum(len(net["pins"]) for net in rudy_nets),
        },
        "nets": rudy_nets,
    }

    inputs_dir = output_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    documents = {
        "schedule": schedule,
        "crossings": crossings,
        "positions": positions,
        "rudy_input": rudy_input,
        "bank_channel_input": bank_input,
        "electrical_map": electrical_map,
    }
    paths = {}
    for label, document in documents.items():
        path = inputs_dir / f"{label}.json"
        write_json(path, document)
        paths[label] = path
    report = {
        "schema": ACADEMIC_CHIMEW_LOOKAHEAD_SCHEMA,
        "status": "pass",
        "provider": ACADEMIC_CHIMEW_LOOKAHEAD_PROVIDER,
        "qualification": "academic-virtual-physical-model",
        "design": schedule["design"],
        "platform": schedule["platform"],
        "timing_guard": initial["timing_guard"],
        "metrics": {
            "signals": len(schedule["entries"]),
            "placement_endpoint_fallbacks": fallbacks,
            "forwarded_boundary_endpoints": forwarded_boundary_endpoints,
            "placed_source_instances": total_placed_source_instances,
            "source_instances": total_source_instances,
            "unmapped_helper_atoms": total_unmapped_helper_atoms,
            "predicted_sll_crossings": total_crossings,
            "groups": len(group_records),
            "virtual_package_pins": len(package_records),
            "tdm_groups_before_chimew": len(
                {
                    (
                        entry["link"],
                        entry["from"],
                        entry["to"],
                        entry["lane"],
                    )
                    for entry in schedule["entries"]
                }
            ),
        },
        "artifacts": {
            label: {"path": str(path), "sha256": _sha256(path)}
            for label, path in paths.items()
        },
        "sources": {
            "routing": str(routing_source),
            "placement": str(placement_source),
            "netlist": str(ir_path),
            "architecture": str(architecture_source),
            "package_pins": str(package_source),
            **(
                {"timing_paths": str(timing_source)}
                if timing_source is not None
                else {}
            ),
        },
        **(
            {
                "timing_weighting": {
                    "provider": ACADEMIC_CHIMEW_TIMING_WEIGHT_PROVIDER,
                    "source_sha256": _sha256(timing_source),
                    "weighted_signals": sum(
                        weight > 1.0 for weight in timing_weights.values()
                    ),
                    "maximum_weight": max(timing_weights.values()),
                    "weight_scale": timing_weight_scale,
                    "path_scope": timing_path_scope,
                    "exact_path_hops": timing_coverage[
                        "exact_path_hops"
                    ],
                    "whole_net_fallbacks": timing_coverage[
                        "whole_net_fallbacks"
                    ],
                }
            }
            if timing_source is not None
            else {}
        ),
    }
    write_json(output_dir / "academic-chimew-lookahead-report.json", report)
    return report
