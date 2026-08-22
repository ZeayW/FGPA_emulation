from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ValidationError
from .ir import EmuIR
from .platform import Platform
from .resources import RESOURCE_FIELDS, ResourceVector


PARTITION_REPLICATION_SCHEMA = "emuflow.partition-replication/v1"
REPLICABLE_BOUNDARY_CLASSES = {
    "clock",
    "primary_input",
    "register_output",
    "reset",
}
_COMBINATIONAL_TYPES = (
    re.compile(r"^LUT[1-6]$"),
)
_STATEFUL_RESOURCES = {
    "bram18k",
    "clock",
    "dsp48",
    "ff",
    "io",
    "uram288",
}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_known_combinational(instance: Mapping[str, Any]) -> bool:
    resources = instance["resources"]
    if any(resources.get(field, 0) for field in _STATEFUL_RESOURCES):
        return False
    return any(
        pattern.fullmatch(instance["type"])
        for pattern in _COMBINATIONAL_TYPES
    )


def replicable_clusters(
    ir: EmuIR,
    clusters_artifact: Mapping[str, Any],
    proof_clusters: Optional[Set[str]] = None,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    proof_targets = proof_clusters or set()
    instances = {
        instance["id"]: instance for instance in ir.value["instances"]
    }
    clusters = {
        cluster["id"]: cluster for cluster in clusters_artifact["clusters"]
    }
    cluster_by_instance = {
        instance_id: cluster_id
        for cluster_id, cluster in clusters.items()
        for instance_id in cluster["instances"]
    }
    candidates = {
        cluster_id
        for cluster_id, cluster in clusters.items()
        if all(
            _is_known_combinational(instances[instance_id])
            for instance_id in cluster["instances"]
        )
    }
    candidate_members = {
        cluster_id: set(clusters[cluster_id]["instances"])
        for cluster_id in candidates
    }
    invalid: Set[str] = set()
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    indegree = {
        instance_id: 0
        for cluster_id in candidates
        for instance_id in clusters[cluster_id]["instances"]
    }
    boundary_inputs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    internal_nets: Dict[str, int] = defaultdict(int)

    for net in ir.value["nets"]:
        driver_instances = {
            endpoint["instance"]
            for endpoint in net["drivers"]
            if endpoint["instance"] is not None
        }
        sink_instances = {
            endpoint["instance"]
            for endpoint in net["sinks"]
            if endpoint["instance"] is not None
        }
        sink_clusters = {
            cluster_by_instance[instance_id]
            for instance_id in sink_instances
            if cluster_by_instance[instance_id] in candidates
        }
        for cluster_id in sink_clusters:
            if cluster_id in invalid:
                continue
            members = candidate_members[cluster_id]
            internal_drivers = driver_instances & members
            internal_sinks = sink_instances & members
            if (
                len(driver_instances) > 1
                or net["cut_class"] == "multi_driver"
            ):
                invalid.add(cluster_id)
                continue
            external_drivers = driver_instances - members
            if external_drivers or not driver_instances:
                if net["cut_class"] not in REPLICABLE_BOUNDARY_CLASSES:
                    invalid.add(cluster_id)
                    continue
                if cluster_id in proof_targets:
                    boundary_inputs[cluster_id].append(
                        {
                            "net": net["id"],
                            "cut_class": net["cut_class"],
                            "external_drivers": sorted(external_drivers),
                        }
                    )
            if internal_drivers and cluster_id in proof_targets:
                internal_nets[cluster_id] += 1
            for driver in internal_drivers:
                for sink in internal_sinks:
                    if sink in adjacency[driver]:
                        continue
                    adjacency[driver].add(sink)
                    indegree[sink] += 1

    legal: List[str] = []
    proofs: Dict[str, Dict[str, Any]] = {}
    for cluster_id in sorted(candidates - invalid):
        members = candidate_members[cluster_id]
        local_indegree = {
            instance_id: indegree[instance_id] for instance_id in members
        }
        queue = deque(
            sorted(
                node for node, degree in local_indegree.items() if degree == 0
            )
        )
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in sorted(adjacency[node]):
                local_indegree[neighbor] -= 1
                if local_indegree[neighbor] == 0:
                    queue.append(neighbor)
        if visited != len(members):
            continue
        legal.append(cluster_id)
        if cluster_id in proof_targets:
            proofs[cluster_id] = {
                "cluster": cluster_id,
                "instances": len(members),
                "internal_nets": internal_nets[cluster_id],
                "boundary_inputs": sorted(
                    boundary_inputs[cluster_id],
                    key=lambda item: (item["net"], item["cut_class"]),
                ),
                "acyclic": True,
                "known_combinational_primitives": True,
                "fanin_closed": True,
            }
    return legal, proofs


def _normalize_replicas(
    replicas: Mapping[str, Sequence[str]],
    cluster_assignment: Mapping[str, str],
    fpga_ids: Set[str],
) -> Dict[str, List[str]]:
    normalized: Dict[str, List[str]] = {}
    unknown_clusters = sorted(set(replicas) - set(cluster_assignment))
    if unknown_clusters:
        raise ValidationError(
            f"replicas reference unknown clusters {unknown_clusters[:8]}"
        )
    for cluster_id, targets in sorted(replicas.items()):
        if not isinstance(targets, (list, tuple)) or not all(
            isinstance(target, str) for target in targets
        ):
            raise ValidationError(
                f"replicas[{cluster_id!r}]: expected an array of FPGA ids"
            )
        duplicate_targets = sorted(
            target for target in set(targets) if targets.count(target) > 1
        )
        if duplicate_targets:
            raise ValidationError(
                f"replicas[{cluster_id!r}] contains duplicate targets "
                f"{duplicate_targets}"
            )
        unknown_targets = sorted(set(targets) - fpga_ids)
        if unknown_targets:
            raise ValidationError(
                f"replicas[{cluster_id!r}] references unknown FPGAs "
                f"{unknown_targets}"
            )
        if cluster_assignment[cluster_id] in targets:
            raise ValidationError(
                f"replicas[{cluster_id!r}] repeats its primary FPGA"
            )
        if targets:
            normalized[cluster_id] = sorted(targets)
    return normalized


def _instance_locations(
    cluster_assignment: Mapping[str, str],
    clusters: Mapping[str, Mapping[str, Any]],
    replicas: Mapping[str, Sequence[str]],
) -> Dict[str, List[str]]:
    return {
        instance_id: [
            cluster_assignment[cluster_id],
            *replicas.get(cluster_id, []),
        ]
        for cluster_id, cluster in clusters.items()
        for instance_id in cluster["instances"]
    }


def _compute_effective_cuts(
    ir: EmuIR,
    primary_assignment: Mapping[str, str],
    locations: Mapping[str, Sequence[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[Tuple[str, str], int]]:
    cuts: List[Dict[str, Any]] = []
    endpoint_counts: Dict[Tuple[str, str], int] = {}
    replicated_primary_inputs = 0
    global_nets = 0
    cut_sink_endpoints = 0
    for net in ir.value["nets"]:
        primary_driver_fpgas = sorted(
            {
                primary_assignment[endpoint["instance"]]
                for endpoint in net["drivers"]
                if endpoint["instance"] is not None
            }
        )
        if len(primary_driver_fpgas) > 1:
            raise ValidationError(
                f"replication requires a unique primary driver for net "
                f"{net['id']!r}"
            )
        driver_locations = {
            fpga_id
            for endpoint in net["drivers"]
            if endpoint["instance"] is not None
            for fpga_id in locations[endpoint["instance"]]
        }
        sink_endpoints_by_fpga: Dict[str, int] = defaultdict(int)
        for endpoint in net["sinks"]:
            if endpoint["instance"] is None:
                continue
            for fpga_id in locations[endpoint["instance"]]:
                sink_endpoints_by_fpga[fpga_id] += 1
        sink_fpgas = sorted(sink_endpoints_by_fpga)
        instance_fpgas = driver_locations | set(sink_fpgas)

        if net["cut_class"] in {"clock", "reset"}:
            if len(instance_fpgas) > 1:
                global_nets += 1
            continue
        if not primary_driver_fpgas and net["cut_class"] == "primary_input":
            if len(sink_fpgas) > 1:
                replicated_primary_inputs += 1
            continue

        remote_sink_fpgas = sorted(set(sink_fpgas) - driver_locations)
        if not remote_sink_fpgas:
            continue
        remote_endpoints = sum(
            sink_endpoints_by_fpga[fpga_id] for fpga_id in remote_sink_fpgas
        )
        cut_sink_endpoints += remote_endpoints
        for fpga_id in remote_sink_fpgas:
            endpoint_counts[(net["id"], fpga_id)] = sink_endpoints_by_fpga[
                fpga_id
            ]
        cut: Dict[str, Any] = {
            "net": net["id"],
            "cut_class": net["cut_class"],
            "source_fpgas": primary_driver_fpgas,
            "sink_fpgas": remote_sink_fpgas,
            "sink_endpoints": remote_endpoints,
        }
        if net["cut_class"] == "register_input":
            cut["transport_round"] = 1
        cuts.append(cut)

    metrics = {
        "cut_nets": len(cuts),
        "cut_sink_endpoints": cut_sink_endpoints,
        "replicated_primary_inputs": replicated_primary_inputs,
        "global_nets": global_nets,
    }
    register_input_cuts = sum(
        cut["cut_class"] == "register_input" for cut in cuts
    )
    if register_input_cuts:
        metrics.update(
            {
                "register_input_cut_nets": register_input_cuts,
                "transport_rounds": 2,
                "round_barriers": 1,
            }
        )
    return sorted(cuts, key=lambda item: item["net"]), metrics, endpoint_counts


def build_replication_artifact(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    primary_assignment_artifact: Mapping[str, Any],
    replicas: Mapping[str, Sequence[str]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, int]]:
    clusters = {
        cluster["id"]: cluster for cluster in clusters_artifact["clusters"]
    }
    cluster_assignment = primary_assignment_artifact["cluster_assignment"]
    primary_assignment = primary_assignment_artifact["instance_assignment"]
    fpga_ids = {fpga.id for fpga in platform.fpgas}
    normalized = _normalize_replicas(
        replicas, cluster_assignment, fpga_ids
    )

    legal_clusters, all_proofs = replicable_clusters(
        ir,
        clusters_artifact,
        proof_clusters=set(normalized),
    )
    illegal = sorted(set(normalized) - set(legal_clusters))
    if illegal:
        raise ValidationError(
            "RePart requested replicas for clusters that failed the "
            f"combinational-fanin proof: {illegal[:8]}"
        )
    proofs = [all_proofs[cluster_id] for cluster_id in sorted(normalized)]
    locations = _instance_locations(cluster_assignment, clusters, normalized)

    primary_resources = {
        fpga.id: ResourceVector.sum(
            ResourceVector.from_mapping(instance["resources"])
            for instance in ir.value["instances"]
            if primary_assignment[instance["id"]] == fpga.id
        )
        for fpga in platform.fpgas
    }
    replica_resources = {
        fpga.id: ResourceVector.sum(
            ResourceVector.from_mapping(clusters[cluster_id]["resources"])
            for cluster_id, targets in normalized.items()
            if fpga.id in targets
        )
        for fpga in platform.fpgas
    }
    effective_resources = {
        fpga.id: ResourceVector.from_mapping(
            {
                field: getattr(primary_resources[fpga.id], field)
                + getattr(replica_resources[fpga.id], field)
                for field in RESOURCE_FIELDS
            }
        )
        for fpga in platform.fpgas
    }
    for fpga in platform.fpgas:
        if not effective_resources[fpga.id].fits_capacity(
            fpga.effective_capacity
        ):
            raise ValidationError(
                f"replicas exceed effective capacity of {fpga.id!r}: "
                f"{effective_resources[fpga.id].to_dict(include_zeros=False)}"
            )

    from .partition import compute_cut_nets

    base_cuts, base_metrics = compute_cut_nets(ir, primary_assignment)
    effective_cuts, effective_metrics, effective_endpoints = (
        _compute_effective_cuts(ir, primary_assignment, locations)
    )
    _, _, base_endpoints = _compute_effective_cuts(
        ir,
        primary_assignment,
        {
            instance_id: [fpga_id]
            for instance_id, fpga_id in primary_assignment.items()
        },
    )
    deltas = []
    for net, fpga in sorted(set(base_endpoints) | set(effective_endpoints)):
        before = base_endpoints.get((net, fpga), 0)
        after = effective_endpoints.get((net, fpga), 0)
        if before != after:
            deltas.append(
                {
                    "net": net,
                    "fpga": fpga,
                    "base_sink_endpoints": before,
                    "effective_sink_endpoints": after,
                    "delta": after - before,
                }
            )

    replica_records = []
    replica_instance_assignment: Dict[str, str] = {}
    replica_cells = 0
    original_instance_ids = set(primary_assignment)
    for cluster_id, targets in normalized.items():
        cluster = clusters[cluster_id]
        replica_cells += len(cluster["instances"]) * len(targets)
        for target in targets:
            copies = []
            for instance_id in cluster["instances"]:
                copy_id = f"{instance_id}__emuflow_replica__{target}"
                if (
                    copy_id in original_instance_ids
                    or copy_id in replica_instance_assignment
                ):
                    raise ValidationError(
                        f"generated replica id {copy_id!r} is not unique"
                    )
                replica_instance_assignment[copy_id] = target
                copies.append(
                    {
                        "original_instance": instance_id,
                        "replica_instance": copy_id,
                    }
                )
            replica_records.append(
                {
                    "cluster": cluster_id,
                    "primary_fpga": cluster_assignment[cluster_id],
                    "target_fpga": target,
                    "resources": dict(sorted(cluster["resources"].items())),
                    "instances": copies,
                }
            )

    base_endpoints_total = base_metrics["cut_sink_endpoints"]
    effective_endpoints_total = effective_metrics["cut_sink_endpoints"]
    endpoint_reduction = base_endpoints_total - effective_endpoints_total
    artifact = {
        "schema": PARTITION_REPLICATION_SCHEMA,
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "replicas": replica_records,
        "replica_instance_assignment": dict(
            sorted(replica_instance_assignment.items())
        ),
        "fanin_proofs": proofs,
        "cut_deltas": deltas,
        "resources_by_fpga": {
            fpga.id: {
                "primary": primary_resources[fpga.id].to_dict(
                    include_zeros=False
                ),
                "replica": replica_resources[fpga.id].to_dict(
                    include_zeros=False
                ),
                "effective": effective_resources[fpga.id].to_dict(
                    include_zeros=False
                ),
                "effective_capacity": dict(
                    sorted(fpga.effective_capacity.items())
                ),
            }
            for fpga in platform.fpgas
        },
        "metrics": {
            "replica_clusters": len(normalized),
            "replica_copies": len(replica_records),
            "replica_instances": replica_cells,
            "replica_luts": sum(
                record["resources"].get("lut", 0)
                for record in replica_records
            ),
            "base_cut_nets": base_metrics["cut_nets"],
            "effective_cut_nets": effective_metrics["cut_nets"],
            "cut_net_reduction": (
                base_metrics["cut_nets"] - effective_metrics["cut_nets"]
            ),
            "base_cut_sink_endpoints": base_endpoints_total,
            "effective_cut_sink_endpoints": effective_endpoints_total,
            "cut_sink_endpoint_reduction": endpoint_reduction,
            "cut_sink_endpoint_reduction_per_replica": (
                endpoint_reduction / replica_cells if replica_cells else 0.0
            ),
        },
        "digests": {
            "base_cut_nets_sha256": _canonical_digest(base_cuts),
            "effective_cut_nets_sha256": _canonical_digest(effective_cuts),
        },
        "policy": {
            "replicable_boundary_classes": sorted(
                REPLICABLE_BOUNDARY_CLASSES
            ),
            "stateful_resource_classes": sorted(_STATEFUL_RESOURCES),
            "requires_acyclic_fanin_closed_cluster": True,
            "clock_reset_transport": "global-runtime-distribution",
        },
    }
    return artifact, effective_cuts, effective_metrics


def apply_replication(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    primary_assignment_artifact: Mapping[str, Any],
    replicas: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    artifact, effective_cuts, effective_metrics = build_replication_artifact(
        ir,
        platform,
        clusters_artifact,
        primary_assignment_artifact,
        replicas,
    )
    result = dict(primary_assignment_artifact)
    result["replication"] = artifact
    result["cut_nets"] = effective_cuts
    result["metrics"] = {
        **primary_assignment_artifact["metrics"],
        **effective_metrics,
        **artifact["metrics"],
    }
    resources = artifact["resources_by_fpga"]
    result["partitions"] = [
        {
            **record,
            "primary_resources": resources[record["fpga"]]["primary"],
            "replica_resources": resources[record["fpga"]]["replica"],
            "resources": resources[record["fpga"]]["effective"],
            "replica_instance_count": sum(
                len(replica["instances"])
                for replica in artifact["replicas"]
                if replica["target_fpga"] == record["fpga"]
            ),
            "effective_instance_count": (
                record["instance_count"]
                + sum(
                    len(replica["instances"])
                    for replica in artifact["replicas"]
                    if replica["target_fpga"] == record["fpga"]
                )
            ),
            "utilization": {
                field: value / record["effective_capacity"][field]
                for field, value in resources[record["fpga"]][
                    "effective"
                ].items()
                if record["effective_capacity"].get(field, 0) > 0
            },
        }
        for record in primary_assignment_artifact["partitions"]
    ]
    return result


def validate_replication_artifact(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    assignment_artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    raw = assignment_artifact.get("replication")
    if not isinstance(raw, dict):
        raise ValidationError("assignment.replication: expected an object")
    replicas: Dict[str, List[str]] = defaultdict(list)
    raw_records = raw.get("replicas")
    if not isinstance(raw_records, list):
        raise ValidationError("assignment.replication.replicas: expected an array")
    for index, record in enumerate(raw_records):
        if not isinstance(record, dict):
            raise ValidationError(
                f"assignment.replication.replicas[{index}]: expected an object"
            )
        cluster_id = record.get("cluster")
        target = record.get("target_fpga")
        if not isinstance(cluster_id, str) or not isinstance(target, str):
            raise ValidationError(
                f"assignment.replication.replicas[{index}]: invalid cluster/target"
            )
        replicas[cluster_id].append(target)

    expected, effective_cuts, effective_metrics = build_replication_artifact(
        ir,
        platform,
        clusters_artifact,
        {
            **assignment_artifact,
            "replication": None,
        },
        replicas,
    )
    if raw != expected:
        raise ValidationError(
            "assignment.replication does not match independently recomputed "
            "replica legality, resources, or communication deltas"
        )
    return {
        "artifact": expected,
        "cut_nets": effective_cuts,
        "metrics": effective_metrics,
        "resources_by_fpga": {
            fpga_id: ResourceVector.from_mapping(record["effective"])
            for fpga_id, record in expected["resources_by_fpga"].items()
        },
    }
