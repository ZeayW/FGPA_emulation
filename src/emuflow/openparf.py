import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple

from .architecture import ArchitectureDB
from .io import write_json
from .ir import EmuIR


OPENPARF_MANIFEST_SCHEMA = "emuflow.openparf-manifest/v1"


def _pins_by_cell_type(ir: EmuIR) -> Dict[str, Dict[str, str]]:
    instance_types = {
        instance["id"]: instance["type"] for instance in ir.value["instances"]
    }
    result: Dict[str, Dict[str, str]] = defaultdict(dict)
    for net in ir.value["nets"]:
        for endpoint in net["drivers"]:
            instance = endpoint.get("instance")
            if instance is not None:
                result[instance_types[instance]][endpoint["port"]] = "OUTPUT"
        for endpoint in net["sinks"]:
            instance = endpoint.get("instance")
            if instance is not None:
                result[instance_types[instance]][endpoint["port"]] = "INPUT"
    return result


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_nodes(ir: EmuIR) -> str:
    return "".join(
        f"{instance['id']} {instance['type']}\n"
        for instance in sorted(ir.value["instances"], key=lambda item: item["id"])
    )


def _render_lib(ir: EmuIR) -> str:
    blocks: List[str] = []
    for cell_type, pins in sorted(_pins_by_cell_type(ir).items()):
        lines = [f"CELL {cell_type}"]
        for pin, direction in sorted(pins.items()):
            qualifier = ""
            if direction == "INPUT" and pin in {"C", "CLK"}:
                qualifier = " CLOCK"
            elif direction == "INPUT" and pin in {
                "CE",
                "R",
                "S",
                "CLR",
                "PRE",
            }:
                qualifier = " CTRL"
            lines.append(f"  PIN {pin} {direction}{qualifier}")
        lines.append("END CELL")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _net_endpoints(net: Mapping[str, Any]) -> Iterable[Tuple[str, str]]:
    for collection in ("drivers", "sinks"):
        for endpoint in net[collection]:
            instance = endpoint.get("instance")
            if instance is not None:
                yield instance, endpoint["port"]


def _render_nets(ir: EmuIR) -> Tuple[str, int]:
    lines: List[str] = []
    emitted = 0
    for net in sorted(ir.value["nets"], key=lambda item: item["id"]):
        endpoints = list(_net_endpoints(net))
        if len(endpoints) < 2:
            continue
        emitted += 1
        lines.append(f"net {net['id']} {len(endpoints)}")
        lines.extend(f"  {instance} {pin}" for instance, pin in endpoints)
        lines.append("endnet")
    return "\n".join(lines) + "\n", emitted


def _site_resource_counts(site: Mapping[str, Any]) -> Dict[str, int]:
    slots: Dict[str, Set[str]] = defaultdict(set)
    for bel in site["bels"]:
        for cell_type in bel["compatible_cells"]:
            if cell_type.startswith("LUT"):
                slots["LUT"].add(bel["name"])
            elif cell_type.startswith("FD"):
                slots["FF"].add(bel["name"])
            elif cell_type == "CARRY8":
                slots["CARRY8"].add(bel["name"])
            elif cell_type == "DSP48E2":
                slots["DSP"].add(bel["name"])
            elif cell_type == "RAMB36E2":
                slots["RAM"].add(bel["name"])
    return {resource: len(bels) for resource, bels in slots.items()}


def _render_scl(architecture: ArchitectureDB) -> str:
    by_site_type: Dict[str, Dict[str, int]] = {}
    for site in architecture.sites:
        resources = _site_resource_counts(site)
        existing = by_site_type.setdefault(site["type"], resources)
        if existing != resources:
            raise ValueError(
                f"site type {site['type']!r} has inconsistent resource counts"
            )
    lines: List[str] = []
    for site_type, resources in sorted(by_site_type.items()):
        lines.append(f"SITE {site_type}")
        for resource, count in sorted(resources.items()):
            lines.append(f"  {resource} {count}")
        lines.extend(["END SITE", ""])
    cell_types = sorted(
        {
            instance_type
            for site in architecture.sites
            for bel in site["bels"]
            for instance_type in bel["compatible_cells"]
        }
    )
    resource_cells: Dict[str, List[str]] = defaultdict(list)
    for cell_type in cell_types:
        if cell_type.startswith("LUT"):
            resource_cells["LUT"].append(cell_type)
        elif cell_type.startswith("FD"):
            resource_cells["FF"].append(cell_type)
        elif cell_type == "CARRY8":
            resource_cells["CARRY8"].append(cell_type)
        elif cell_type == "DSP48E2":
            resource_cells["DSP"].append(cell_type)
        elif cell_type == "RAMB36E2":
            resource_cells["RAM"].append(cell_type)
    lines.append("RESOURCES")
    for resource, types in sorted(resource_cells.items()):
        lines.append(f"  {resource} {' '.join(types)}")
    lines.extend(["END RESOURCES", ""])
    width = max(site["x"] for site in architecture.sites) + 1
    height = max(site["y"] for site in architecture.sites) + 1
    lines.append(f"SITEMAP {width} {height}")
    for site in sorted(
        architecture.sites, key=lambda item: (item["x"], item["y"])
    ):
        lines.append(f"{site['x']} {site['y']} {site['type']}")
    lines.append("END SITEMAP")
    return "\n".join(lines) + "\n"


def _area_type_for_cell(cell_type: str) -> str:
    if cell_type.startswith("LUT"):
        return "LUT"
    if cell_type.startswith("FD"):
        return "FF"
    if cell_type == "CARRY8":
        return "CARRY8"
    if cell_type == "DSP48E2":
        return "DSP"
    if cell_type == "RAMB36E2":
        return "RAM"
    raise ValueError(f"OpenPARF adapter does not support cell type {cell_type!r}")


def _render_config(
    ir: EmuIR, architecture: ArchitectureDB, output_dir: Path
) -> Dict[str, Any]:
    types = sorted({instance["type"] for instance in ir.value["instances"]})
    model_map: Dict[str, Any] = {}
    resource_map: Dict[str, str] = {}
    resource_categories: Dict[str, str] = {}
    for cell_type in types:
        area_type = _area_type_for_cell(cell_type)
        site_capacity = max(
            _site_resource_counts(site).get(area_type, 0)
            for site in architecture.sites
        )
        if site_capacity <= 0:
            raise ValueError(
                f"ArchitectureDB has no {area_type} slots for {cell_type}"
            )
        entry: Dict[str, Any] = {
            area_type: [
                f"sqrt(1/{site_capacity})",
                f"sqrt(1/{site_capacity})",
            ],
            "isLUT": int(cell_type.startswith("LUT")),
            "isFF": int(cell_type.startswith("FD")),
        }
        model_map[cell_type] = entry
        resource_map[area_type] = area_type
        if area_type == "LUT":
            resource_categories[area_type] = "LUTL"
        elif area_type == "FF":
            resource_categories[area_type] = "FF"
        else:
            resource_categories[area_type] = "SSSIR"
    return {
        "benchmark_name": ir.value["design"]["name"],
        "benchmark_format": "bookshelf",
        "aux_input": str(output_dir / "design.aux"),
        "gpu": 0,
        "target_density": 0.8,
        "random_seed": 1000,
        "max_global_place_iters": 100,
        "global_place_flag": 1,
        "legalize_flag": 1,
        "detailed_place_flag": 1,
        "plot_flag": 0,
        "num_threads": 8,
        "gp_model2area_types_map": model_map,
        "gp_resource2area_types_map": resource_map,
        "resource_categories": resource_categories,
        "CLB_capacity": max(
            (_site_resource_counts(site).get("LUT", 0)
             for site in architecture.sites),
            default=8,
        ),
        "BLE_capacity": 2,
        "num_ControlSets_per_CLB": 4,
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


def export_bookshelf(
    ir: EmuIR, architecture: ArchitectureDB, output_dir: Path
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    nets, emitted_nets = _render_nets(ir)
    _write_text(output_dir / "design.nodes", _render_nodes(ir))
    _write_text(output_dir / "design.nets", nets)
    _write_text(output_dir / "design.lib", _render_lib(ir))
    _write_text(output_dir / "design.scl", _render_scl(architecture))
    _write_text(output_dir / "design.pl", "")
    _write_text(
        output_dir / "design.aux",
        "design : design.nodes design.nets design.pl design.scl design.lib\n",
    )
    write_json(
        output_dir / "openparf.json",
        _render_config(ir, architecture, output_dir.resolve()),
    )
    manifest = {
        "schema": OPENPARF_MANIFEST_SCHEMA,
        "design": ir.value["design"]["name"],
        "part": architecture.part,
        "instances": len(ir.value["instances"]),
        "nets": emitted_nets,
        "dropped_single_endpoint_nets": len(ir.value["nets"]) - emitted_nets,
        "coordinate_contract": (
            "OpenPARF x/y selects exactly one ArchitectureDB site; z plus "
            "cell type selects exactly one compatible BEL."
        ),
        "files": {
            "aux": "design.aux",
            "config": "openparf.json",
            "library": "design.lib",
            "nets": "design.nets",
            "nodes": "design.nodes",
            "placement": "design.pl",
            "sites": "design.scl",
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest
