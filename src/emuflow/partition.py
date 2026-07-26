import fnmatch
import hashlib
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ValidationError
from .io import read_json
from .ir import EmuIR
from .platform import Platform
from .resources import RESOURCE_FIELDS, ResourceVector


CLUSTERS_SCHEMA = "emuflow.clusters/v1"
PARTITION_ASSIGNMENT_SCHEMA = "emuflow.partition-assignment/v1"
PARTITION_CONSTRAINTS_SCHEMA = "emuflow.partition-constraints/v1"
LEGAL_CUT_CLASSES = {"register_output", "primary_input"}
REPLICATED_NET_CLASSES = {"clock", "reset", "primary_input"}
HARD_MACRO_RESOURCES = {"bram18k", "uram288", "dsp48", "carry8"}


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent[item]
        while parent != self.parent[parent]:
            parent = self.parent[parent]
        while item != parent:
            next_item = self.parent[item]
            self.parent[item] = parent
            item = next_item
        return parent

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _instance_ids_on_net(net: Mapping[str, Any]) -> List[str]:
    return sorted(
        {
            endpoint["instance"]
            for collection in ("drivers", "sinks")
            for endpoint in net[collection]
            if endpoint["instance"] is not None
        }
    )


def _sum_resources(
    instance_ids: Iterable[str],
    instances: Mapping[str, Mapping[str, Any]],
) -> ResourceVector:
    return ResourceVector.sum(
        ResourceVector.from_mapping(instances[instance_id]["resources"])
        for instance_id in instance_ids
    )


def _is_hard_macro(instance: Mapping[str, Any]) -> bool:
    resources = ResourceVector.from_mapping(instance["resources"])
    return any(getattr(resources, field) for field in HARD_MACRO_RESOURCES)


def _expand_instance_patterns(
    patterns: Sequence[str],
    instance_ids: Sequence[str],
    context: str,
) -> List[str]:
    matches: Set[str] = set()
    for pattern_index, pattern in enumerate(patterns):
        if not isinstance(pattern, str) or not pattern:
            raise ValidationError(
                f"{context}[{pattern_index}]: expected a non-empty string"
            )
        pattern_matches = [
            instance_id
            for instance_id in instance_ids
            if fnmatch.fnmatchcase(instance_id, pattern)
        ]
        if not pattern_matches:
            raise ValidationError(
                f"{context}[{pattern_index}]: pattern {pattern!r} matched no instances"
            )
        matches.update(pattern_matches)
    return sorted(matches)


def normalize_partition_constraints(
    value: Optional[Mapping[str, Any]],
    ir: EmuIR,
    platform: Platform,
    min_used_fpgas: Optional[int] = None,
    balance_tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    raw: Mapping[str, Any] = value or {}
    if raw and raw.get("schema") != PARTITION_CONSTRAINTS_SCHEMA:
        raise ValidationError(
            "constraints.schema: expected "
            f"{PARTITION_CONSTRAINTS_SCHEMA!r}, got {raw.get('schema')!r}"
        )

    instance_ids = sorted(instance["id"] for instance in ir.value["instances"])
    instance_set = set(instance_ids)
    fpga_ids = {fpga.id for fpga in platform.fpgas}

    raw_groups = raw.get("groups", [])
    if not isinstance(raw_groups, list):
        raise ValidationError("constraints.groups: expected an array")
    groups: List[Dict[str, Any]] = []
    group_ids: Set[str] = set()
    for index, item in enumerate(raw_groups):
        if not isinstance(item, dict):
            raise ValidationError(f"constraints.groups[{index}]: expected an object")
        group_id = item.get("id")
        if not isinstance(group_id, str) or not group_id:
            raise ValidationError(
                f"constraints.groups[{index}].id: expected a non-empty string"
            )
        if group_id in group_ids:
            raise ValidationError(
                f"constraints.groups[{index}].id: duplicate {group_id!r}"
            )
        group_ids.add(group_id)
        raw_instances = item.get("instances", [])
        raw_patterns = item.get("patterns", [])
        if not isinstance(raw_instances, list) or not all(
            isinstance(instance_id, str) for instance_id in raw_instances
        ):
            raise ValidationError(
                f"constraints.groups[{index}].instances: expected strings"
            )
        if not isinstance(raw_patterns, list):
            raise ValidationError(
                f"constraints.groups[{index}].patterns: expected an array"
            )
        unknown = sorted(set(raw_instances) - instance_set)
        if unknown:
            raise ValidationError(
                f"constraints.groups[{index}].instances: unknown instances {unknown}"
            )
        expanded = set(raw_instances)
        expanded.update(
            _expand_instance_patterns(
                raw_patterns,
                instance_ids,
                f"constraints.groups[{index}].patterns",
            )
        )
        if not expanded:
            raise ValidationError(
                f"constraints.groups[{index}]: expected at least one instance"
            )
        groups.append({"id": group_id, "instances": sorted(expanded)})

    raw_fixed = raw.get("fixed", [])
    if not isinstance(raw_fixed, list):
        raise ValidationError("constraints.fixed: expected an array")
    fixed_by_instance: Dict[str, str] = {}
    fixed: List[Dict[str, str]] = []
    for index, item in enumerate(raw_fixed):
        if not isinstance(item, dict):
            raise ValidationError(f"constraints.fixed[{index}]: expected an object")
        fpga_id = item.get("fpga")
        if fpga_id not in fpga_ids:
            raise ValidationError(
                f"constraints.fixed[{index}].fpga: unknown FPGA {fpga_id!r}"
            )
        raw_instances = item.get("instances", [])
        if "instance" in item:
            raw_instances = list(raw_instances) + [item.get("instance")]
        raw_patterns = item.get("patterns", [])
        if not isinstance(raw_instances, list) or not all(
            isinstance(instance_id, str) for instance_id in raw_instances
        ):
            raise ValidationError(
                f"constraints.fixed[{index}].instances: expected strings"
            )
        if not isinstance(raw_patterns, list):
            raise ValidationError(
                f"constraints.fixed[{index}].patterns: expected an array"
            )
        unknown = sorted(set(raw_instances) - instance_set)
        if unknown:
            raise ValidationError(
                f"constraints.fixed[{index}].instances: unknown instances {unknown}"
            )
        expanded = set(raw_instances)
        expanded.update(
            _expand_instance_patterns(
                raw_patterns,
                instance_ids,
                f"constraints.fixed[{index}].patterns",
            )
        )
        if not expanded:
            raise ValidationError(
                f"constraints.fixed[{index}]: expected at least one instance"
            )
        for instance_id in sorted(expanded):
            previous = fixed_by_instance.get(instance_id)
            if previous is not None and previous != fpga_id:
                raise ValidationError(
                    f"constraints.fixed: instance {instance_id!r} is fixed to "
                    f"both {previous!r} and {fpga_id!r}"
                )
            fixed_by_instance[instance_id] = fpga_id
            fixed.append({"instance": instance_id, "fpga": fpga_id})

    raw_min_used = raw.get("min_used_fpgas", len(platform.fpgas))
    if min_used_fpgas is not None:
        raw_min_used = min_used_fpgas
    if (
        isinstance(raw_min_used, bool)
        or not isinstance(raw_min_used, int)
        or raw_min_used <= 0
        or raw_min_used > len(platform.fpgas)
    ):
        raise ValidationError(
            "constraints.min_used_fpgas: expected an integer between 1 and "
            f"{len(platform.fpgas)}"
        )

    raw_tolerance = raw.get("balance_tolerance", 0.10)
    if balance_tolerance is not None:
        raw_tolerance = balance_tolerance
    if (
        isinstance(raw_tolerance, bool)
        or not isinstance(raw_tolerance, (int, float))
        or float(raw_tolerance) < 0.0
    ):
        raise ValidationError(
            "constraints.balance_tolerance: expected a non-negative number"
        )

    return {
        "schema": PARTITION_CONSTRAINTS_SCHEMA,
        "groups": groups,
        "fixed": sorted(
            fixed, key=lambda item: (item["instance"], item["fpga"])
        ),
        "min_used_fpgas": raw_min_used,
        "balance_tolerance": float(raw_tolerance),
    }


def load_partition_constraints(
    path: Optional[Path],
    ir: EmuIR,
    platform: Platform,
    min_used_fpgas: Optional[int] = None,
    balance_tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    value = read_json(path) if path is not None else None
    return normalize_partition_constraints(
        value,
        ir,
        platform,
        min_used_fpgas=min_used_fpgas,
        balance_tolerance=balance_tolerance,
    )


def build_clusters(
    ir: EmuIR,
    constraints: Mapping[str, Any],
) -> Dict[str, Any]:
    instances = {
        instance["id"]: instance for instance in ir.value["instances"]
    }
    instance_ids = sorted(instances)
    index_by_id = {
        instance_id: index for index, instance_id in enumerate(instance_ids)
    }
    union_find = _UnionFind(len(instance_ids))

    for group in constraints["groups"]:
        members = group["instances"]
        for member in members[1:]:
            union_find.union(index_by_id[members[0]], index_by_id[member])

    for net in ir.value["nets"]:
        members = _instance_ids_on_net(net)
        if len(members) < 2:
            continue
        if net["cut_class"] not in LEGAL_CUT_CLASSES | REPLICATED_NET_CLASSES:
            for member in members[1:]:
                union_find.union(index_by_id[members[0]], index_by_id[member])
        macro_members = [
            member for member in members if _is_hard_macro(instances[member])
        ]
        for member in macro_members[1:]:
            union_find.union(
                index_by_id[macro_members[0]], index_by_id[member]
            )

    members_by_root: Dict[int, List[str]] = defaultdict(list)
    for instance_id in instance_ids:
        members_by_root[union_find.find(index_by_id[instance_id])].append(
            instance_id
        )

    fixed_by_instance = {
        item["instance"]: item["fpga"] for item in constraints["fixed"]
    }
    groups_by_instance: Dict[str, List[str]] = defaultdict(list)
    for group in constraints["groups"]:
        for instance_id in group["instances"]:
            groups_by_instance[instance_id].append(group["id"])

    raw_clusters = sorted(
        (sorted(members) for members in members_by_root.values()),
        key=lambda members: members[0],
    )
    clusters: List[Dict[str, Any]] = []
    for index, members in enumerate(raw_clusters):
        fixed_fpgas = {
            fixed_by_instance[member]
            for member in members
            if member in fixed_by_instance
        }
        if len(fixed_fpgas) > 1:
            raise ValidationError(
                f"cluster containing {members[0]!r} has conflicting fixed FPGA "
                f"constraints {sorted(fixed_fpgas)}"
            )
        clusters.append(
            {
                "id": f"c{index:06d}",
                "instances": members,
                "resources": _sum_resources(members, instances).to_dict(
                    include_zeros=False
                ),
                "fixed_fpga": next(iter(fixed_fpgas), None),
                "groups": sorted(
                    {
                        group_id
                        for member in members
                        for group_id in groups_by_instance.get(member, [])
                    }
                ),
            }
        )

    return {
        "schema": CLUSTERS_SCHEMA,
        "design": ir.value["design"]["name"],
        "clusters": clusters,
        "instances": len(instance_ids),
        "policy": {
            "legal_cut_classes": sorted(LEGAL_CUT_CLASSES),
            "replicated_net_classes": sorted(REPLICATED_NET_CLASSES),
            "hard_macro_resources": sorted(HARD_MACRO_RESOURCES),
        },
    }


def _cluster_adjacency(
    ir: EmuIR,
    cluster_by_instance: Mapping[str, str],
) -> Dict[str, Dict[str, int]]:
    adjacency: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for net in ir.value["nets"]:
        if net["cut_class"] != "register_output":
            continue
        driver_clusters = {
            cluster_by_instance[endpoint["instance"]]
            for endpoint in net["drivers"]
            if endpoint["instance"] is not None
        }
        sink_clusters = {
            cluster_by_instance[endpoint["instance"]]
            for endpoint in net["sinks"]
            if endpoint["instance"] is not None
        }
        for driver_cluster in driver_clusters:
            for sink_cluster in sink_clusters:
                if driver_cluster == sink_cluster:
                    continue
                adjacency[driver_cluster][sink_cluster] += 1
                adjacency[sink_cluster][driver_cluster] += 1
    return adjacency


def _resource_add(
    left: Mapping[str, int], right: Mapping[str, int]
) -> Dict[str, int]:
    return {
        field: left.get(field, 0) + right.get(field, 0)
        for field in RESOURCE_FIELDS
    }


def _fits(resources: Mapping[str, int], capacity: Mapping[str, int]) -> bool:
    return all(resources.get(field, 0) <= limit for field, limit in capacity.items())


def _seeded_tie(seed: int, cluster_id: str) -> str:
    return hashlib.sha256(f"{seed}:{cluster_id}".encode("utf-8")).hexdigest()


def assign_clusters(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    constraints: Mapping[str, Any],
    seed: int = 0,
) -> Dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValidationError("partition seed: expected a non-negative integer")

    clusters = {
        cluster["id"]: cluster for cluster in clusters_artifact["clusters"]
    }
    if len(clusters) < constraints["min_used_fpgas"]:
        raise ValidationError(
            f"partitioning has {len(clusters)} atomic clusters but "
            f"{constraints['min_used_fpgas']} FPGAs must be used"
        )
    cluster_by_instance = {
        instance_id: cluster_id
        for cluster_id, cluster in clusters.items()
        for instance_id in cluster["instances"]
    }
    adjacency = _cluster_adjacency(ir, cluster_by_instance)
    fpga_ids = [fpga.id for fpga in platform.fpgas]
    effective_capacity = {
        fpga.id: fpga.effective_capacity for fpga in platform.fpgas
    }
    loads = {
        fpga_id: {field: 0 for field in RESOURCE_FIELDS}
        for fpga_id in fpga_ids
    }

    total_resources = ResourceVector.sum(
        ResourceVector.from_mapping(cluster["resources"])
        for cluster in clusters.values()
    ).to_dict()
    soft_caps: Dict[str, Dict[str, int]] = {
        fpga_id: {} for fpga_id in fpga_ids
    }
    tolerance = constraints["balance_tolerance"]
    for field in RESOURCE_FIELDS:
        total = total_resources[field]
        capacities = {
            fpga_id: effective_capacity[fpga_id].get(field, 0)
            for fpga_id in fpga_ids
        }
        capacity_total = sum(capacities.values())
        if total == 0 or capacity_total == 0:
            continue
        for fpga_id in fpga_ids:
            proportional = total * capacities[fpga_id] / capacity_total
            soft_caps[fpga_id][field] = min(
                capacities[fpga_id],
                math.ceil(proportional * (1.0 + tolerance)),
            )

    def dominant_size(cluster: Mapping[str, Any]) -> float:
        resources = cluster["resources"]
        ratios = []
        for fpga_id in fpga_ids:
            capacity = effective_capacity[fpga_id]
            ratios.extend(
                resources.get(field, 0) / capacity[field]
                for field in capacity
                if capacity[field] > 0
            )
        return max(ratios, default=0.0)

    order = sorted(
        clusters,
        key=lambda cluster_id: (
            -dominant_size(clusters[cluster_id]),
            -len(clusters[cluster_id]["instances"]),
            _seeded_tie(seed, cluster_id),
            cluster_id,
        ),
    )
    assignment: Dict[str, str] = {}

    def place(cluster_id: str, fpga_id: str) -> None:
        cluster_resources = ResourceVector.from_mapping(
            clusters[cluster_id]["resources"]
        ).to_dict()
        projected = _resource_add(loads[fpga_id], cluster_resources)
        if not _fits(projected, effective_capacity[fpga_id]):
            raise ValidationError(
                f"cluster {cluster_id!r} does not fit FPGA {fpga_id!r}"
            )
        assignment[cluster_id] = fpga_id
        loads[fpga_id] = projected

    for cluster_id in order:
        fixed_fpga = clusters[cluster_id]["fixed_fpga"]
        if fixed_fpga is not None:
            place(cluster_id, fixed_fpga)

    populated = {fpga_id for fpga_id in assignment.values()}
    for fpga_id in fpga_ids:
        if len(populated) >= constraints["min_used_fpgas"]:
            break
        if fpga_id in populated:
            continue
        candidate = next(
            (
                cluster_id
                for cluster_id in order
                if cluster_id not in assignment
                and _fits(
                    _resource_add(
                        loads[fpga_id],
                        ResourceVector.from_mapping(
                            clusters[cluster_id]["resources"]
                        ).to_dict(),
                    ),
                    effective_capacity[fpga_id],
                )
            ),
            None,
        )
        if candidate is None:
            raise ValidationError(
                f"cannot populate required FPGA {fpga_id!r} within capacity"
            )
        place(candidate, fpga_id)
        populated.add(fpga_id)

    for cluster_id in order:
        if cluster_id in assignment:
            continue
        resources = ResourceVector.from_mapping(
            clusters[cluster_id]["resources"]
        ).to_dict()
        actual_candidates = [
            fpga_id
            for fpga_id in fpga_ids
            if _fits(
                _resource_add(loads[fpga_id], resources),
                effective_capacity[fpga_id],
            )
        ]
        if not actual_candidates:
            raise ValidationError(
                f"cluster {cluster_id!r} cannot fit any FPGA; resources "
                f"{clusters[cluster_id]['resources']}"
            )
        balanced_candidates = [
            fpga_id
            for fpga_id in actual_candidates
            if _fits(
                _resource_add(loads[fpga_id], resources),
                soft_caps[fpga_id],
            )
        ]
        candidates = balanced_candidates or actual_candidates

        def score(fpga_id: str) -> Tuple[float, float, float, str]:
            cut_cost = sum(
                weight
                for neighbor, weight in adjacency.get(cluster_id, {}).items()
                if neighbor in assignment and assignment[neighbor] != fpga_id
            )
            projected = _resource_add(loads[fpga_id], resources)
            ratios = [
                projected[field] / effective_capacity[fpga_id][field]
                for field in effective_capacity[fpga_id]
                if effective_capacity[fpga_id][field] > 0
            ]
            return (
                float(cut_cost),
                max(ratios, default=0.0),
                sum(ratios),
                fpga_id,
            )

        place(cluster_id, min(candidates, key=score))

    instance_assignment = {
        instance_id: assignment[cluster_id]
        for instance_id, cluster_id in cluster_by_instance.items()
    }
    cut_nets, cut_metrics = compute_cut_nets(ir, instance_assignment)
    partition_records = []
    for fpga in platform.fpgas:
        cluster_ids = sorted(
            cluster_id
            for cluster_id, assigned_fpga in assignment.items()
            if assigned_fpga == fpga.id
        )
        instance_count = sum(
            len(clusters[cluster_id]["instances"]) for cluster_id in cluster_ids
        )
        resources = ResourceVector.from_mapping(loads[fpga.id]).to_dict(
            include_zeros=False
        )
        utilization = {
            field: resources.get(field, 0) / fpga.effective_capacity[field]
            for field in fpga.effective_capacity
            if fpga.effective_capacity[field] > 0
        }
        partition_records.append(
            {
                "fpga": fpga.id,
                "clusters": cluster_ids,
                "cluster_count": len(cluster_ids),
                "instance_count": instance_count,
                "resources": resources,
                "effective_capacity": dict(sorted(fpga.effective_capacity.items())),
                "utilization": dict(sorted(utilization.items())),
            }
        )

    return {
        "schema": PARTITION_ASSIGNMENT_SCHEMA,
        "design": ir.value["design"]["name"],
        "platform": platform.name,
        "provider": "deterministic-multiresource-greedy-v1",
        "seed": seed,
        "constraints": dict(constraints),
        "cluster_assignment": dict(sorted(assignment.items())),
        "instance_assignment": dict(sorted(instance_assignment.items())),
        "partitions": partition_records,
        "cut_nets": cut_nets,
        "metrics": {
            "instances": len(instance_assignment),
            "clusters": len(clusters),
            "used_fpgas": sum(
                1 for record in partition_records if record["instance_count"]
            ),
            **cut_metrics,
        },
    }


def compute_cut_nets(
    ir: EmuIR,
    instance_assignment: Mapping[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    cut_nets: List[Dict[str, Any]] = []
    replicated_primary_inputs = 0
    global_nets = 0
    cut_sink_endpoints = 0
    for net in ir.value["nets"]:
        driver_fpgas = sorted(
            {
                instance_assignment[endpoint["instance"]]
                for endpoint in net["drivers"]
                if endpoint["instance"] is not None
            }
        )
        sink_endpoints_by_fpga: Dict[str, int] = defaultdict(int)
        for endpoint in net["sinks"]:
            if endpoint["instance"] is not None:
                sink_endpoints_by_fpga[
                    instance_assignment[endpoint["instance"]]
                ] += 1
        sink_fpgas = sorted(sink_endpoints_by_fpga)
        instance_fpgas = set(driver_fpgas) | set(sink_fpgas)

        if net["cut_class"] in {"clock", "reset"}:
            if len(instance_fpgas) > 1:
                global_nets += 1
            continue
        if not driver_fpgas and net["cut_class"] == "primary_input":
            if len(sink_fpgas) > 1:
                replicated_primary_inputs += 1
            continue

        remote_sink_fpgas = sorted(
            fpga_id for fpga_id in sink_fpgas if fpga_id not in driver_fpgas
        )
        if not remote_sink_fpgas:
            continue
        remote_endpoints = sum(
            sink_endpoints_by_fpga[fpga_id] for fpga_id in remote_sink_fpgas
        )
        cut_sink_endpoints += remote_endpoints
        cut_nets.append(
            {
                "net": net["id"],
                "cut_class": net["cut_class"],
                "source_fpgas": driver_fpgas,
                "sink_fpgas": remote_sink_fpgas,
                "sink_endpoints": remote_endpoints,
            }
        )

    return (
        sorted(cut_nets, key=lambda item: item["net"]),
        {
            "cut_nets": len(cut_nets),
            "cut_sink_endpoints": cut_sink_endpoints,
            "replicated_primary_inputs": replicated_primary_inputs,
            "global_nets": global_nets,
        },
    )


def validate_partition_artifacts(
    ir: EmuIR,
    platform: Platform,
    clusters_artifact: Mapping[str, Any],
    assignment_artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    if clusters_artifact.get("schema") != CLUSTERS_SCHEMA:
        raise ValidationError(
            f"clusters.schema: expected {CLUSTERS_SCHEMA!r}, "
            f"got {clusters_artifact.get('schema')!r}"
        )
    if assignment_artifact.get("schema") != PARTITION_ASSIGNMENT_SCHEMA:
        raise ValidationError(
            f"assignment.schema: expected {PARTITION_ASSIGNMENT_SCHEMA!r}, "
            f"got {assignment_artifact.get('schema')!r}"
        )

    instance_ids = {instance["id"] for instance in ir.value["instances"]}
    instances = {
        instance["id"]: instance for instance in ir.value["instances"]
    }
    fpga_by_id = {fpga.id: fpga for fpga in platform.fpgas}
    raw_assignment = assignment_artifact.get("instance_assignment")
    if not isinstance(raw_assignment, dict):
        raise ValidationError("assignment.instance_assignment: expected an object")
    assigned_ids = set(raw_assignment)
    missing = sorted(instance_ids - assigned_ids)
    extra = sorted(assigned_ids - instance_ids)
    if missing or extra:
        raise ValidationError(
            "assignment.instance_assignment: exact coverage failed; "
            f"missing={missing[:8]}, extra={extra[:8]}"
        )
    unknown_fpgas = sorted(set(raw_assignment.values()) - set(fpga_by_id))
    if unknown_fpgas:
        raise ValidationError(
            f"assignment.instance_assignment: unknown FPGAs {unknown_fpgas}"
        )

    raw_clusters = clusters_artifact.get("clusters")
    if not isinstance(raw_clusters, list):
        raise ValidationError("clusters.clusters: expected an array")
    cluster_ids: Set[str] = set()
    cluster_members: Set[str] = set()
    for index, cluster in enumerate(raw_clusters):
        if not isinstance(cluster, dict):
            raise ValidationError(f"clusters[{index}]: expected an object")
        cluster_id = cluster.get("id")
        members = cluster.get("instances")
        if not isinstance(cluster_id, str) or not cluster_id:
            raise ValidationError(
                f"clusters[{index}].id: expected a non-empty string"
            )
        if cluster_id in cluster_ids:
            raise ValidationError(f"clusters[{index}].id: duplicate {cluster_id!r}")
        cluster_ids.add(cluster_id)
        if not isinstance(members, list) or not all(
            isinstance(member, str) for member in members
        ):
            raise ValidationError(
                f"clusters[{index}].instances: expected an array of strings"
            )
        duplicate_members = cluster_members & set(members)
        if duplicate_members:
            raise ValidationError(
                f"clusters[{index}].instances: duplicate coverage "
                f"{sorted(duplicate_members)[:8]}"
            )
        cluster_members.update(members)
        assigned_fpgas = {raw_assignment[member] for member in members}
        if len(assigned_fpgas) != 1:
            raise ValidationError(
                f"cluster {cluster_id!r} spans FPGAs {sorted(assigned_fpgas)}"
            )
        expected_resources = _sum_resources(members, instances).to_dict(
            include_zeros=False
        )
        if cluster.get("resources") != expected_resources:
            raise ValidationError(
                f"cluster {cluster_id!r} resource summary does not match EmuIR"
            )
    if cluster_members != instance_ids:
        raise ValidationError(
            "clusters: exact instance coverage failed; "
            f"missing={sorted(instance_ids - cluster_members)[:8]}, "
            f"extra={sorted(cluster_members - instance_ids)[:8]}"
        )

    constraints = normalize_partition_constraints(
        assignment_artifact.get("constraints"),
        ir,
        platform,
    )
    for group in constraints["groups"]:
        assigned_fpgas = {
            raw_assignment[instance_id] for instance_id in group["instances"]
        }
        if len(assigned_fpgas) != 1:
            raise ValidationError(
                f"group {group['id']!r} spans FPGAs {sorted(assigned_fpgas)}"
            )
    for fixed in constraints["fixed"]:
        actual = raw_assignment[fixed["instance"]]
        if actual != fixed["fpga"]:
            raise ValidationError(
                f"fixed instance {fixed['instance']!r}: expected "
                f"{fixed['fpga']!r}, got {actual!r}"
            )

    resources_by_fpga = {
        fpga_id: ResourceVector.sum(
            ResourceVector.from_mapping(instances[instance_id]["resources"])
            for instance_id, assigned_fpga in raw_assignment.items()
            if assigned_fpga == fpga_id
        )
        for fpga_id in fpga_by_id
    }
    for fpga_id, resources in resources_by_fpga.items():
        if not resources.fits_capacity(fpga_by_id[fpga_id].effective_capacity):
            raise ValidationError(
                f"FPGA {fpga_id!r} exceeds effective capacity: "
                f"{resources.to_dict(include_zeros=False)}"
            )
    used_fpgas = sum(
        1
        for fpga_id in fpga_by_id
        if any(assigned_fpga == fpga_id for assigned_fpga in raw_assignment.values())
    )
    if used_fpgas < constraints["min_used_fpgas"]:
        raise ValidationError(
            f"assignment uses {used_fpgas} FPGAs; "
            f"{constraints['min_used_fpgas']} required"
        )

    illegal_cuts: List[str] = []
    for net in ir.value["nets"]:
        fpga_ids = {
            raw_assignment[instance_id]
            for instance_id in _instance_ids_on_net(net)
        }
        if (
            len(fpga_ids) > 1
            and net["cut_class"]
            not in LEGAL_CUT_CLASSES | REPLICATED_NET_CLASSES
        ):
            illegal_cuts.append(net["id"])
    if illegal_cuts:
        raise ValidationError(
            "assignment contains forbidden combinational cuts: "
            f"{illegal_cuts[:8]}"
        )

    expected_cuts, expected_metrics = compute_cut_nets(ir, raw_assignment)
    if assignment_artifact.get("cut_nets") != expected_cuts:
        raise ValidationError("assignment.cut_nets does not match recomputed cuts")
    metrics = assignment_artifact.get("metrics")
    if not isinstance(metrics, dict):
        raise ValidationError("assignment.metrics: expected an object")
    for key, expected in expected_metrics.items():
        if metrics.get(key) != expected:
            raise ValidationError(
                f"assignment.metrics.{key}: expected {expected}, "
                f"got {metrics.get(key)!r}"
            )

    return {
        "status": "pass",
        "instances": len(instance_ids),
        "clusters": len(raw_clusters),
        "used_fpgas": used_fpgas,
        "illegal_cuts": 0,
        **expected_metrics,
        "resources_by_fpga": {
            fpga_id: resources.to_dict(include_zeros=False)
            for fpga_id, resources in resources_by_fpga.items()
        },
    }
