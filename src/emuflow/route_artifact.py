"""Independent VPR route/RR-graph artifact checking."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import EmuFlowError, ValidationError
from .io import read_json, write_json
from .native_tools import resolve_native_executable
from .packed_netlist import validate_packed_netlist_contract


VPR_ROUTE_CHECK_SCHEMA = "emuflow.vpr-route-check/v1"
VPR_ROUTE_CHECK_PROVIDER = "emuflow-cpp-vpr-route-checker"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_vpr_route_artifacts(
    route_path: Path,
    rr_graph_path: Path,
    packed_contract_path: Path,
    placement_path: Path,
    output_path: Path,
    *,
    executable: Optional[str] = None,
) -> Dict[str, Any]:
    paths = {
        "route": route_path.resolve(),
        "rr_graph": rr_graph_path.resolve(),
        "packed_contract": packed_contract_path.resolve(),
        "placement": placement_path.resolve(),
    }
    for name, path in paths.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise EmuFlowError(f"VPR {name} artifact is missing: {path}")

    checker = resolve_native_executable(
        "emuflow_vpr_route_checker", executable
    )
    core_path = output_path.resolve().with_suffix(".core.json")
    core_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            checker,
            str(paths["route"]),
            str(paths["rr_graph"]),
            str(core_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValidationError(
            "independent VPR route checker failed:\n"
            + "\n".join(completed.stdout.splitlines()[-30:])
        )
    core = read_json(core_path)
    if (
        core.get("schema") != "emuflow.vpr-route-core-check/v1"
        or core.get("status") != "pass"
    ):
        raise ValidationError("native VPR route-check report is invalid")

    packed = read_json(paths["packed_contract"])
    packed_summary = validate_packed_netlist_contract(packed)
    packed_nets = {net["id"]: net for net in packed["nets"]}
    routed_nets = core.get("nets")
    if not isinstance(routed_nets, list):
        raise ValidationError("native route report has no net list")
    if [net.get("id") for net in routed_nets] != list(
        range(len(routed_nets))
    ):
        raise ValidationError("VPR route net IDs are not dense and ordered")
    route_by_name = {}
    for net in routed_nets:
        name = net.get("name")
        if not isinstance(name, str) or not name or name in route_by_name:
            raise ValidationError("VPR route net names are invalid")
        route_by_name[name] = net
    if set(route_by_name) != set(packed_nets):
        missing = sorted(set(packed_nets) - set(route_by_name))
        extra = sorted(set(route_by_name) - set(packed_nets))
        raise ValidationError(
            "route/packed net coverage mismatch; "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )

    global_nets = 0
    for name, packed_net in packed_nets.items():
        routed = route_by_name[name]
        expected_sinks = len(packed_net["sinks"])
        if routed.get("global") is True:
            global_nets += 1
            if routed.get("endpoints") != expected_sinks + 1:
                raise ValidationError(
                    f"global route net {name!r} endpoint count is wrong"
                )
        elif (
            routed.get("local_only") is not False
            or routed.get("sinks") != expected_sinks
        ):
            raise ValidationError(
                f"route net {name!r} sink coverage is wrong"
            )

    placement_id = f"SHA256:{_sha256(paths['placement'])}"
    if core.get("placement_id") != placement_id:
        raise ValidationError(
            "route placement ID does not match the placement artifact"
        )

    artifacts = {
        name: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for name, path in paths.items()
    }
    report = {
        "schema": VPR_ROUTE_CHECK_SCHEMA,
        "status": "pass",
        "provider": VPR_ROUTE_CHECK_PROVIDER,
        "design": packed["design"],
        "checks": {
            "placement_hash": "pass",
            "net_coverage": "pass",
            "sink_coverage": "pass",
            "rr_node_identity": "pass",
            "rr_edge_and_switch_legality": "pass",
            "rr_capacity": "pass",
        },
        "packed_nets": packed_summary["cross_cluster_nets"],
        "global_nets": global_nets,
        "routed_nets": core["routed_nets"],
        "route_nodes": core["route_nodes"],
        "route_edges": core["route_edges"],
        "branch_restarts": core["branch_restarts"],
        "rr_nodes": core["rr_nodes"],
        "rr_edges": core["rr_edges"],
        "max_occupancy": core["max_occupancy"],
        "max_capacity": core["max_capacity"],
        "array": core["array"],
        "artifacts": artifacts,
    }
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, report)
    core_path.unlink(missing_ok=True)
    return report
