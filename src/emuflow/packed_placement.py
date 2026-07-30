"""OpenPARF clustered placement and VPR placement-file handoff."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .architecture import ArchitectureDB
from .errors import ImportError, ValidationError
from .io import read_json, write_json
from .openparf import run_openparf
from .packed_netlist import validate_packed_netlist_contract


PACKED_OPENPARF_MANIFEST_SCHEMA = (
    "emuflow.vpr-packed-openparf-manifest/v1"
)
PACKED_PLACEMENT_REPORT_SCHEMA = "emuflow.vpr-packed-placement-report/v1"
_BLOCK_INDEX_RE = re.compile(r"\[(\d+)\]$")


def _openparf_resource_name(block_type: str) -> str:
    """Match reserved Bookshelf resource tokens normalized by OpenPARF."""
    upper = block_type.upper()
    return upper if upper in {"IO", "BRAM"} else block_type


def _cluster_capacity(
    architecture: ArchitectureDB,
    raw_site: Mapping[str, Any],
) -> Dict[str, int]:
    template_name = raw_site.get("template")
    if not isinstance(template_name, str):
        raise ValidationError(
            f"VTR site {raw_site.get('name')!r} has no template"
        )
    template = architecture.value["site_templates"].get(template_name)
    if not isinstance(template, Mapping):
        raise ValidationError(
            f"VTR site {raw_site.get('name')!r} has unknown template"
        )
    capacity = template.get("vtr_cluster_capacity")
    if (
        not isinstance(capacity, Mapping)
        or not capacity
        or any(
            not isinstance(block_type, str)
            or not block_type
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            for block_type, count in capacity.items()
        )
    ):
        raise ValidationError(
            f"VTR site template {template_name!r} has invalid cluster capacity"
        )
    return dict(capacity)


def _architecture_slots(
    architecture: ArchitectureDB,
) -> Tuple[
    Dict[str, List[Tuple[int, int, int]]],
    Dict[str, Dict[str, int]],
]:
    slots: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    site_types: Dict[str, Dict[str, int]] = {}
    for site in architecture.value["sites"]:
        capacity = _cluster_capacity(architecture, site)
        existing = site_types.setdefault(site["type"], capacity)
        if existing != capacity:
            raise ValidationError(
                f"VTR site type {site['type']!r} has inconsistent capacity"
            )
        for block_type, count in capacity.items():
            slots[block_type].extend(
                (site["x"], site["y"], z) for z in range(count)
            )
    return (
        {
            block_type: sorted(block_slots)
            for block_type, block_slots in sorted(slots.items())
        },
        site_types,
    )


def _name_maps(
    packed: Mapping[str, Any],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    cluster_names = {
        cluster["id"]: f"c{index}"
        for index, cluster in enumerate(packed["clusters"])
    }
    net_names = {
        net["id"]: f"n{index}" for index, net in enumerate(packed["nets"])
    }
    return cluster_names, net_names


def _pin_maps(
    packed: Mapping[str, Any],
) -> Tuple[
    Dict[Tuple[str, str], str],
    Dict[Tuple[str, str], str],
    Dict[str, Tuple[int, int]],
]:
    outgoing: Dict[str, List[str]] = defaultdict(list)
    incoming: Dict[str, List[str]] = defaultdict(list)
    for net in packed["nets"]:
        outgoing[net["driver"]].append(net["id"])
        for sink in net["sinks"]:
            incoming[sink].append(net["id"])
    output_pins: Dict[Tuple[str, str], str] = {}
    input_pins: Dict[Tuple[str, str], str] = {}
    maxima: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    block_type_by_cluster = {
        cluster["id"]: cluster["block_type"]
        for cluster in packed["clusters"]
    }
    for cluster_id in block_type_by_cluster:
        for index, net_id in enumerate(sorted(outgoing[cluster_id])):
            output_pins[(cluster_id, net_id)] = f"O{index}"
        for index, net_id in enumerate(sorted(incoming[cluster_id])):
            input_pins[(cluster_id, net_id)] = f"I{index}"
        block_type = block_type_by_cluster[cluster_id]
        maxima[block_type][0] = max(
            maxima[block_type][0], len(outgoing[cluster_id])
        )
        maxima[block_type][1] = max(
            maxima[block_type][1], len(incoming[cluster_id])
        )
    return (
        output_pins,
        input_pins,
        {
            block_type: (counts[0], counts[1])
            for block_type, counts in sorted(maxima.items())
        },
    )


def _render_nodes(
    packed: Mapping[str, Any], cluster_names: Mapping[str, str]
) -> str:
    return "".join(
        f"{cluster_names[cluster['id']]} VPR_{cluster['block_type']}\n"
        for cluster in packed["clusters"]
    )


def _render_library(pin_counts: Mapping[str, Tuple[int, int]]) -> str:
    blocks = []
    for block_type, (outputs, inputs) in sorted(pin_counts.items()):
        lines = [f"CELL VPR_{block_type}"]
        lines.extend(f"  PIN O{index} OUTPUT" for index in range(outputs))
        lines.extend(f"  PIN I{index} INPUT" for index in range(inputs))
        # OpenPARF's Bookshelf loader collects floating control pins onto its
        # dedicated constant net, which it excludes from movable signal nets.
        # Without one control pin the loader creates an empty constant net and
        # its placement database rejects the zero-degree net.
        lines.append("  PIN EMUFLOW_CONST INPUT CTRL_SR")
        lines.append("END CELL")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _render_nets(
    packed: Mapping[str, Any],
    cluster_names: Mapping[str, str],
    net_names: Mapping[str, str],
    output_pins: Mapping[Tuple[str, str], str],
    input_pins: Mapping[Tuple[str, str], str],
) -> str:
    lines = []
    for net in packed["nets"]:
        net_id = net["id"]
        endpoints = 1 + len(net["sinks"])
        lines.append(f"net {net_names[net_id]} {endpoints}")
        driver = net["driver"]
        lines.append(
            f"  {cluster_names[driver]} {output_pins[(driver, net_id)]}"
        )
        lines.extend(
            f"  {cluster_names[sink]} {input_pins[(sink, net_id)]}"
            for sink in net["sinks"]
        )
        lines.append("endnet")
    return "\n".join(lines) + "\n"


def _render_sites(
    architecture: ArchitectureDB,
    site_types: Mapping[str, Mapping[str, int]],
    used_block_types: List[str],
) -> str:
    used = set(used_block_types)
    lines: List[str] = []
    for site_type, capacity in sorted(site_types.items()):
        lines.append(f"SITE {site_type}")
        for block_type, count in sorted(capacity.items()):
            if block_type in used:
                lines.append(f"  {block_type} {count}")
        lines.extend(["END SITE", ""])
    lines.append("RESOURCES")
    for block_type in sorted(used):
        lines.append(f"  {block_type} VPR_{block_type}")
    lines.extend(["END RESOURCES", ""])
    width = max(site["x"] for site in architecture.value["sites"]) + 1
    height = max(site["y"] for site in architecture.value["sites"]) + 1
    lines.append(f"SITEMAP {width} {height}")
    lines.extend(
        f"{site['x']} {site['y']} {site['type']}"
        for site in sorted(
            architecture.value["sites"],
            key=lambda item: (item["x"], item["y"]),
        )
    )
    lines.append("END SITEMAP")
    return "\n".join(lines) + "\n"


def _fixed_multi_instance_placements(
    packed: Mapping[str, Any],
    cluster_names: Mapping[str, str],
    slots: Mapping[str, List[Tuple[int, int, int]]],
    max_site_capacity: Mapping[str, int],
) -> Tuple[str, List[str]]:
    clusters_by_type: Dict[str, List[str]] = defaultdict(list)
    for cluster in packed["clusters"]:
        clusters_by_type[cluster["block_type"]].append(cluster["id"])
    fixed_types = sorted(
        block_type
        for block_type, capacity in max_site_capacity.items()
        if clusters_by_type[block_type]
        and (
            capacity > 1
            or len(clusters_by_type[block_type]) == len(slots[block_type])
        )
    )
    lines = []
    for block_type in fixed_types:
        cluster_ids = sorted(clusters_by_type[block_type])
        available = slots[block_type]
        if len(cluster_ids) > len(available):
            raise ValidationError(
                f"VTR architecture has {len(available)} {block_type} slots "
                f"for {len(cluster_ids)} clusters"
            )
        for index, cluster_id in enumerate(cluster_ids):
            # Evenly sample the legal slot sequence so fixed boundary I/O does
            # not collapse onto the first edge of the academic device.
            slot_index = (index * len(available)) // len(cluster_ids)
            x, y, z = available[slot_index]
            lines.append(
                f"{cluster_names[cluster_id]} {x} {y} {z} FIXED"
            )
    return "\n".join(lines) + ("\n" if lines else ""), fixed_types


def _render_config(
    packed: Mapping[str, Any],
    output_dir: Path,
    slots: Mapping[str, List[Tuple[int, int, int]]],
    max_site_capacity: Mapping[str, int],
    fixed_types: List[str],
) -> Dict[str, Any]:
    demand = Counter(
        cluster["block_type"] for cluster in packed["clusters"]
    )
    utilization = max(
        demand[block_type] / len(slots[block_type])
        for block_type in demand
    )
    target_density = max(0.8, min(0.995, utilization + 0.005))
    model_map = {}
    resource_map = {}
    categories = {}
    for block_type in sorted(demand):
        capacity = max_site_capacity[block_type]
        dimension = f"sqrt(1/{capacity})"
        model_map[f"VPR_{block_type}"] = {
            block_type: [dimension, dimension],
            "isLUT": 0,
            "isFF": 0,
        }
        resource_name = _openparf_resource_name(block_type)
        resource_map[resource_name] = [block_type]
        categories[resource_name] = (
            "SSMIR" if capacity > 1 else "SSSIR"
        )
    return {
        "benchmark_name": packed["design"],
        "benchmark_format": "bookshelf",
        # OpenPARF currently names its generic non-XArch operator bundle
        # "ultrascale". Cluster models set neither LUT nor FF flags, so this
        # selects implementation code only; it does not substitute a Xilinx
        # architecture for the VTR Bookshelf site/resource database.
        "architecture_name": "ultrascale",
        "aux_input": str(output_dir / "design.aux"),
        "gpu": 0,
        "dtype": "float64",
        "target_density": target_density,
        "random_seed": 1000,
        "max_global_place_iters": 1000,
        "global_place_flag": 1,
        "legalize_flag": 1,
        "generic_cluster_placement_flag": 1,
        "logic_area_type_names": [
            block_type
            for block_type in sorted(demand)
            if block_type not in fixed_types
        ],
        "detailed_place_flag": 0,
        "plot_flag": 0,
        "plot_target_at_names": sorted(demand),
        "io_at_names": fixed_types,
        "num_threads": 8,
        "gp_model2area_types_map": model_map,
        "gp_resource2area_types_map": resource_map,
        "resource_categories": categories,
        "CLB_capacity": 1,
        "BLE_capacity": 1,
        "num_ControlSets_per_CLB": 1,
        "gp_adjust_area": 0,
        "gp_adjust_area_types": [],
        "gp_adjust_route_area": 0,
        "gp_adjust_pin_area": 0,
        "gp_adjust_resource_area": 0,
        "honor_clock_region_constraints": 0,
        "honor_half_column_constraints": 0,
        "result_dir": str(output_dir / "results"),
        "route_flag": 0,
        "slr_aware_flag": 0,
    }


def export_packed_bookshelf(
    packed_path: Path,
    architecture_path: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    packed = read_json(packed_path)
    packed_summary = validate_packed_netlist_contract(packed)
    architecture = ArchitectureDB.load(architecture_path)
    source_hash = architecture.value.get("source", {}).get("sha256")
    if packed["source"]["architecture_id"] != f"SHA256:{source_hash}":
        raise ValidationError(
            "packed netlist and ArchitectureDB source hashes do not match"
        )
    slots, site_types = _architecture_slots(architecture)
    demand = Counter(
        cluster["block_type"] for cluster in packed["clusters"]
    )
    for block_type, count in demand.items():
        if block_type not in slots or count > len(slots[block_type]):
            raise ValidationError(
                f"VTR architecture has {len(slots.get(block_type, []))} "
                f"{block_type} slots for {count} clusters"
            )
    max_site_capacity = {
        block_type: max(
            capacity.get(block_type, 0)
            for capacity in site_types.values()
        )
        for block_type in demand
    }
    cluster_names, net_names = _name_maps(packed)
    output_pins, input_pins, pin_counts = _pin_maps(packed)
    initial_placement, fixed_types = _fixed_multi_instance_placements(
        packed,
        cluster_names,
        slots,
        max_site_capacity,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "design.nodes": _render_nodes(packed, cluster_names),
        "design.lib": _render_library(pin_counts),
        "design.nets": _render_nets(
            packed,
            cluster_names,
            net_names,
            output_pins,
            input_pins,
        ),
        "design.scl": _render_sites(
            architecture, site_types, sorted(demand)
        ),
        "design.pl": initial_placement,
        "design.aux": (
            "design : design.nodes design.nets design.pl "
            "design.scl design.lib\n"
        ),
    }
    for name, content in files.items():
        (output_dir / name).write_text(content, encoding="utf-8")
    config = _render_config(
        packed,
        output_dir.resolve(),
        slots,
        max_site_capacity,
        fixed_types,
    )
    write_json(output_dir / "openparf.json", config)
    write_json(
        output_dir / "name_map.json",
        {
            "schema": "emuflow.vpr-packed-openparf-name-map/v1",
            "clusters": [
                {
                    "openparf": cluster_names[cluster["id"]],
                    "vpr": cluster["id"],
                    "name": cluster["name"],
                    "block_type": cluster["block_type"],
                }
                for cluster in packed["clusters"]
            ],
            "nets": [
                {"openparf": net_names[net["id"]], "vpr": net["id"]}
                for net in packed["nets"]
            ],
        },
    )
    manifest = {
        "schema": PACKED_OPENPARF_MANIFEST_SCHEMA,
        "status": "pass",
        "design": packed["design"],
        "part": architecture.part,
        "clusters": packed_summary["clusters"],
        "nets": packed_summary["cross_cluster_nets"],
        "block_types": dict(sorted(demand.items())),
        "fixed_multi_instance_types": fixed_types,
        "architecture_source_sha256": source_hash,
        "files": sorted(
            [*files, "openparf.json", "name_map.json"]
        ),
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def _read_openparf_cluster_placement(
    placement_path: Path,
    packed: Mapping[str, Any],
    architecture: ArchitectureDB,
    name_map: Mapping[str, Any],
) -> Dict[str, Tuple[int, int, int]]:
    safe_to_cluster = {
        entry["openparf"]: entry["vpr"]
        for entry in name_map["clusters"]
    }
    block_type_by_cluster = {
        cluster["id"]: cluster["block_type"]
        for cluster in packed["clusters"]
    }
    sites = {
        (site["x"], site["y"]): site
        for site in architecture.value["sites"]
    }
    locations: Dict[str, Tuple[int, int, int]] = {}
    occupied = set()
    with placement_path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) not in {4, 5}:
                raise ImportError(
                    f"{placement_path}:{line_number}: malformed placement"
                )
            safe_name = fields[0]
            cluster_id = safe_to_cluster.get(safe_name)
            if cluster_id is None:
                raise ImportError(
                    f"{placement_path}:{line_number}: unknown cluster "
                    f"{safe_name!r}"
                )
            if cluster_id in locations:
                raise ImportError(
                    f"{placement_path}:{line_number}: duplicate cluster"
                )
            try:
                raw_coordinates = [float(value) for value in fields[1:4]]
            except ValueError as error:
                raise ImportError(
                    f"{placement_path}:{line_number}: invalid coordinates"
                ) from error
            coordinates = tuple(
                int(round(value)) for value in raw_coordinates
            )
            if any(
                not math.isclose(raw, rounded, abs_tol=1.0e-6)
                for raw, rounded in zip(raw_coordinates, coordinates)
            ):
                raise ImportError(
                    f"{placement_path}:{line_number}: placement is not legal"
                )
            x, y, z = coordinates
            site = sites.get((x, y))
            if site is None:
                raise ImportError(
                    f"{placement_path}:{line_number}: no VTR site at ({x},{y})"
                )
            block_type = block_type_by_cluster[cluster_id]
            capacity = _cluster_capacity(architecture, site).get(
                block_type, 0
            )
            if z < 0 or z >= capacity:
                raise ImportError(
                    f"{placement_path}:{line_number}: {block_type} is illegal "
                    f"at ({x},{y},{z})"
                )
            slot = (x, y, block_type, z)
            if slot in occupied:
                raise ImportError(
                    f"{placement_path}:{line_number}: placement collision"
                )
            occupied.add(slot)
            locations[cluster_id] = (x, y, z)
    missing = sorted(set(block_type_by_cluster) - set(locations))
    if missing:
        raise ImportError(
            "OpenPARF placement is incomplete; missing clusters: "
            + ", ".join(missing[:10])
        )
    return locations


def emit_vpr_place(
    packed_path: Path,
    architecture_path: Path,
    name_map_path: Path,
    openparf_placement_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    packed = read_json(packed_path)
    packed_summary = validate_packed_netlist_contract(packed)
    architecture = ArchitectureDB.load(architecture_path)
    name_map = read_json(name_map_path)
    locations = _read_openparf_cluster_placement(
        openparf_placement_path,
        packed,
        architecture,
        name_map,
    )
    indexed = []
    for fallback, cluster in enumerate(packed["clusters"]):
        match = _BLOCK_INDEX_RE.search(cluster["instance"])
        block_number = int(match.group(1)) if match else fallback
        indexed.append((block_number, cluster))
    indexed.sort(key=lambda item: item[0])
    width = max(site["x"] for site in architecture.value["sites"]) + 1
    height = max(site["y"] for site in architecture.value["sites"]) + 1
    lines = [
        f"Netlist_File: {Path(packed['source']['path']).name} "
        f"Netlist_ID: SHA256:{packed['source']['sha256']}",
        f"Array size: {width} x {height} logic blocks",
        "",
        "#block name\tx\ty\tsubblk\tlayer\tblock number",
        "#----------\t--\t--\t------\t-----\t------------",
    ]
    for block_number, cluster in indexed:
        x, y, z = locations[cluster["id"]]
        lines.append(
            f"{cluster['name']}\t{x}\t{y}\t{z}\t0\t#{block_number}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "status": "pass",
        "clusters": packed_summary["clusters"],
        "output": str(output_path.resolve()),
        "array": {"width": width, "height": height},
        "netlist_id": f"SHA256:{packed['source']['sha256']}",
    }


def run_packed_openparf_placement(
    packed_path: Path,
    architecture_path: Path,
    output_dir: Path,
    *,
    openparf_install: Optional[Path] = None,
    openparf_python: Optional[Path] = None,
) -> Dict[str, Any]:
    bookshelf_dir = output_dir / "openparf"
    manifest = export_packed_bookshelf(
        packed_path, architecture_path, bookshelf_dir
    )
    placement = run_openparf(
        bookshelf_dir / "openparf.json",
        log_path=bookshelf_dir / "openparf.log",
        install_root=openparf_install,
        python_executable=openparf_python,
    )
    vpr_place_path = output_dir / f"{manifest['design']}.place"
    placement_report = emit_vpr_place(
        packed_path,
        architecture_path,
        bookshelf_dir / "name_map.json",
        placement,
        vpr_place_path,
    )
    report = {
        "schema": PACKED_PLACEMENT_REPORT_SCHEMA,
        "status": "pass",
        "provider": "openparf-root-build+emuflow-vpr-handoff",
        "design": manifest["design"],
        "clusters": manifest["clusters"],
        "nets": manifest["nets"],
        "block_types": manifest["block_types"],
        "fixed_multi_instance_types": manifest[
            "fixed_multi_instance_types"
        ],
        "artifacts": {
            "bookshelf": str(bookshelf_dir.resolve()),
            "openparf_placement": str(placement),
            "vpr_placement": str(vpr_place_path.resolve()),
        },
        "vpr_placement": placement_report,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "packed-placement-report.json", report)
    return report
