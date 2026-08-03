from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .boundary_timing import (
    build_boundary_identity_database,
    validate_boundary_identity_database,
)
from .errors import ValidationError
from .io import read_json, write_json
from .ir import EMUIR_SCHEMA, EmuIR
from .netlist import FPGA_NETLIST_SCHEMA, TRANSPORT_ENDPOINTS_SCHEMA


PLACEMENT_IR_REPORT_SCHEMA = "emuflow.placement-ir-report/v1"


def _top_net(
    index: Mapping[Tuple[str, int, str], List[Dict[str, Any]]],
    port: str,
    bit: int,
    collection: str,
) -> Dict[str, Any]:
    matches = index.get((port, bit, collection), [])
    if len(matches) != 1:
        raise ValidationError(
            f"transport EmuIR {port}[{bit}] expected one {collection[:-1]} "
            f"net, found {len(matches)}"
        )
    return matches[0]


def build_placement_ir(
    netlist: Mapping[str, Any],
    transport: Mapping[str, Any],
    transport_ir: EmuIR,
) -> EmuIR:
    if netlist.get("schema") != FPGA_NETLIST_SCHEMA:
        raise ValidationError("invalid per-FPGA netlist schema")
    if transport.get("schema") != TRANSPORT_ENDPOINTS_SCHEMA:
        raise ValidationError("invalid transport endpoint schema")
    if netlist.get("fpga") != transport.get("fpga"):
        raise ValidationError("netlist and transport target different FPGAs")

    top_net_index: Dict[
        Tuple[str, int, str], List[Dict[str, Any]]
    ] = {}
    for net in transport_ir.value["nets"]:
        for collection in ("drivers", "sinks"):
            for endpoint in net[collection]:
                if endpoint["instance"] is not None:
                    continue
                key = (
                    endpoint["port"],
                    endpoint["bit"],
                    collection,
                )
                top_net_index.setdefault(key, []).append(net)

    namespace = "__emuflow_transport__/"
    instance_map = {
        instance["id"]: f"{namespace}{instance['id']}"
        for instance in transport_ir.value["instances"]
    }

    def remap_endpoint(endpoint: Mapping[str, Any]) -> Dict[str, Any]:
        value = dict(endpoint)
        if value["instance"] is not None:
            value["instance"] = instance_map[value["instance"]]
        return value

    local_nets: Dict[str, Dict[str, Any]] = {}
    rx_endpoint_to_signal = {
        endpoint["id"]: endpoint["signal"]
        for endpoint in transport["endpoints"]
        if endpoint["kind"] == "rx"
    }
    shadow_index = {
        item["signal"]: item["index"] for item in transport["shadow_signals"]
    }
    for segment in netlist["nets"]:
        value = deepcopy(segment)
        value["id"] = segment["original_net"]
        value["name"] = segment["name"]
        value.setdefault("aliases", [])
        value.setdefault("bus_index", 0)
        value.setdefault("fanout", len(value["sinks"]))
        generated_drivers = [
            endpoint
            for endpoint in value["drivers"]
            if endpoint["instance"] is not None
            and endpoint["instance"].startswith("__emuflow_rx_")
        ]
        value["drivers"] = [
            endpoint
            for endpoint in value["drivers"]
            if endpoint not in generated_drivers
        ]
        if generated_drivers:
            endpoint_id = generated_drivers[0]["instance"]
            signal = rx_endpoint_to_signal.get(endpoint_id)
            if signal is None:
                raise ValidationError(
                    f"local net references unknown RX endpoint {endpoint_id!r}"
                )
            top_net = _top_net(
                top_net_index,
                "shadow_values",
                shadow_index[signal],
                "sinks",
            )
            value["drivers"].extend(
                remap_endpoint(endpoint)
                for endpoint in top_net["drivers"]
                if endpoint["instance"] is not None
            )
        local_nets[value["id"]] = value

    consumed_transport_nets = set()
    source_index = {
        item["signal"]: item["index"] for item in transport["source_signals"]
    }
    for signal, index in source_index.items():
        original_net = signal.removeprefix("net:")
        if original_net not in local_nets:
            raise ValidationError(
                f"transport source net {original_net!r} is not local"
            )
        top_net = _top_net(
            top_net_index,
            "source_values",
            index,
            "drivers",
        )
        consumed_transport_nets.add(top_net["id"])
        local_nets[original_net]["sinks"].extend(
            remap_endpoint(endpoint)
            for endpoint in top_net["sinks"]
            if endpoint["instance"] is not None
        )

    for signal, index in shadow_index.items():
        top_net = _top_net(
            top_net_index,
            "shadow_values",
            index,
            "sinks",
        )
        consumed_transport_nets.add(top_net["id"])

    # The generated transport RTL keeps each packed interface at width one
    # when a partition has no TX or no RX signals.  Yosys consequently emits
    # a dangling top-level source_values/shadow_values net for that dummy bit.
    # Both interface ports are removed below, so consume every remaining net
    # that references them as well.  Real interface bits have already been
    # stitched into local DUT nets by the loops above.
    removed_ports = {"source_values", "shadow_values"}
    for net in transport_ir.value["nets"]:
        if any(
            endpoint["instance"] is None
            and endpoint["port"] in removed_ports
            for collection in ("drivers", "sinks")
            for endpoint in net[collection]
        ):
            consumed_transport_nets.add(net["id"])

    transport_nets = []
    for net in transport_ir.value["nets"]:
        if net["id"] in consumed_transport_nets:
            continue
        value = deepcopy(net)
        value["id"] = f"{namespace}{net['id']}"
        value["name"] = f"{namespace}{net['name']}"
        value["drivers"] = [
            remap_endpoint(endpoint) for endpoint in net["drivers"]
        ]
        value["sinks"] = [
            remap_endpoint(endpoint) for endpoint in net["sinks"]
        ]
        transport_nets.append(value)

    ports = [deepcopy(port) for port in netlist["ports"]]
    port_ids = {port["id"] for port in ports}
    for port in transport_ir.value["ports"]:
        if port["id"] in removed_ports:
            continue
        if port["id"] in port_ids:
            raise ValidationError(
                f"transport top port collides with DUT port {port['id']!r}"
            )
        ports.append(deepcopy(port))
        port_ids.add(port["id"])

    instances = [deepcopy(item) for item in netlist["instances"]]
    instances.extend(
        {
            **deepcopy(instance),
            "id": instance_map[instance["id"]],
            "name": instance_map[instance["id"]],
        }
        for instance in transport_ir.value["instances"]
    )
    clocks = [
        {
            "id": port["id"],
            "name": port["name"],
            "source_port": port["id"],
            "period_ns": None,
        }
        for port in ports
        if port.get("clock")
    ]
    return EmuIR(
        {
            "schema": EMUIR_SCHEMA,
            "design": {
                **deepcopy(netlist["design"]),
                "name": f"{netlist['design']['name']}__{netlist['fpga']}",
                "top": f"{netlist['design']['top']}__{netlist['fpga']}",
                "source_format": "emuflow-phase7-merged",
            },
            "ports": sorted(ports, key=lambda item: item["id"]),
            "instances": sorted(instances, key=lambda item: item["id"]),
            "nets": sorted(
                [*local_nets.values(), *transport_nets],
                key=lambda item: item["id"],
            ),
            "clocks": clocks,
            "warnings": list(transport_ir.value.get("warnings", [])),
        }
    )


def run_placement_ir_lowering(
    netlist_path: Path,
    transport_path: Path,
    transport_ir_path: Path,
    output_path: Path,
    report_path: Optional[Path] = None,
    boundary_identity_path: Optional[Path] = None,
) -> Dict[str, Any]:
    transport = read_json(transport_path)
    transport_ir = EmuIR.load(transport_ir_path)
    result = build_placement_ir(
        read_json(netlist_path), transport, transport_ir
    )
    write_json(output_path, result.to_dict())
    boundary_path = (
        boundary_identity_path
        if boundary_identity_path is not None
        else output_path.with_name("boundary-identities.json")
    )
    boundary_database = build_boundary_identity_database(
        transport, result, transport_ir
    )
    boundary_validation = validate_boundary_identity_database(
        boundary_database, transport
    )
    write_json(boundary_path, boundary_database)
    stats = result.stats()
    report = {
        "schema": PLACEMENT_IR_REPORT_SCHEMA,
        "status": "pass",
        "design": stats["design"],
        "instances": stats["instances"],
        "nets": stats["nets"],
        "resource_totals": stats["resource_totals"],
        "transport_instances": sum(
            instance["id"].startswith("__emuflow_transport__/")
            for instance in result.value["instances"]
        ),
        "output": str(output_path),
        "boundary_identity": {
            "schema": boundary_database["schema"],
            "output": str(boundary_path),
            "validation": boundary_validation,
        },
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
