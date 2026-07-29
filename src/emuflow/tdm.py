import hashlib
from collections import defaultdict, deque
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import ValidationError
from .platform import Platform
from .routing import (
    SYSTEM_ROUTES_SCHEMA,
    build_directed_graph,
    normalize_route_constraints,
)


TDM_SCHEDULE_SCHEMA = "emuflow.tdm-schedule/v1"
TDM_BASELINE_PROVIDER = "deterministic-round-barrier-earliest-slot-v2"
TDM_ACADEMIC_SCHEDULE_PROVIDER = (
    "lagrangian-kkt-ratio-aware-list-schedule-v1"
)
COMBINATIONAL_SETTLE_SLOTS = 1
HopKey = Tuple[str, str, str, str]


def _hop_key(
    demand_id: str, link_id: str, source: str, sink: str
) -> HopKey:
    return (demand_id, link_id, source, sink)


def _route_hops(
    route: Mapping[str, Any],
) -> List[Tuple[int, Mapping[str, Any]]]:
    adjacency: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for edge in route["tree_edges"]:
        adjacency[edge["from"]].append(edge)
    for node in adjacency:
        adjacency[node].sort(
            key=lambda edge: (edge["to"], edge["link"])
        )
    result: List[Tuple[int, Mapping[str, Any]]] = []
    queue = deque([(route["source"], 0)])
    seen = {route["source"]}
    while queue:
        node, depth = queue.popleft()
        for edge in adjacency.get(node, []):
            if edge["to"] in seen:
                raise ValidationError(
                    f"TDM input route {route['id']!r} is not an acyclic tree"
                )
            seen.add(edge["to"])
            result.append((depth, edge))
            queue.append((edge["to"], depth + 1))
    edge_nodes = {
        node
        for edge in route["tree_edges"]
        for node in (edge["from"], edge["to"])
    }
    if not edge_nodes <= seen:
        raise ValidationError(
            f"TDM input route {route['id']!r} has disconnected tree edges"
        )
    return result


def _link_by_id(platform: Platform):
    return {link.id: link for link in platform.links}


def _round_order(
    routes: Sequence[Mapping[str, Any]],
    planned_hops: Optional[Mapping[HopKey, Mapping[str, Any]]] = None,
) -> Tuple[List[Mapping[str, Any]], List[int]]:
    for route in routes:
        transport_round = route.get("transport_round", 0)
        if (
            isinstance(transport_round, bool)
            or not isinstance(transport_round, int)
            or transport_round < 0
        ):
            raise ValidationError(
                f"TDM route {route['id']!r}.transport_round must be a "
                "non-negative integer"
            )
    ordered = sorted(
        routes,
        key=lambda route: (
            route.get("transport_round", 0),
            min(
                (
                    planned_hops[
                        _hop_key(
                            route["id"],
                            edge["link"],
                            edge["from"],
                            edge["to"],
                        )
                    ]["continuous_ratio"]
                    for _depth, edge in _route_hops(route)
                ),
                default=float("inf"),
            )
            if planned_hops is not None
            else 0.0,
            -max((depth for depth, _ in _route_hops(route)), default=0),
            route["net"],
            route["id"],
        ),
    )
    active_rounds = sorted(
        {route.get("transport_round", 0) for route in routes}
    )
    return ordered, active_rounds


def build_tdm_schedule(
    routes: Mapping[str, Any],
    platform: Platform,
    ratio_plan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if routes.get("schema") != SYSTEM_ROUTES_SCHEMA:
        raise ValidationError(
            f"routes.schema: expected {SYSTEM_ROUTES_SCHEMA!r}, "
            f"got {routes.get('schema')!r}"
        )
    constraints = normalize_route_constraints(
        routes.get("constraints"),
        platform,
    )
    frame_slots = constraints["frame_slots"]
    _, arcs, capacity_records = build_directed_graph(platform, constraints)
    links = _link_by_id(platform)
    planned_hops = None
    planned_round_one_ready = None
    if ratio_plan is not None:
        from .tdm_ratio import (
            ratio_plan_by_hop,
            validate_tdm_ratio_plan,
        )

        validate_tdm_ratio_plan(routes, platform, ratio_plan)
        planned_hops = ratio_plan_by_hop(ratio_plan)
        planned_round_one_ready = ratio_plan[
            "round_barrier_legalization"
        ]["source_ready_slot"]
    slot_fill: Dict[str, List[int]] = {
        key: [0] * frame_slots for key in capacity_records
    }
    next_available: Dict[str, List[int]] = {
        key: list(range(frame_slots + 1)) for key in capacity_records
    }
    planned_occupancy: Dict[str, Set[Tuple[int, int]]] = defaultdict(set)

    def first_available_slot(capacity_key: str, start: int) -> int:
        parents = next_available[capacity_key]
        slot = start
        while parents[slot] != slot:
            parents[slot] = parents[parents[slot]]
            slot = parents[slot]
        root = slot
        slot = start
        while parents[slot] != slot:
            successor = parents[slot]
            parents[slot] = root
            slot = successor
        return root

    entries: List[Dict[str, Any]] = []
    demand_completions: List[Dict[str, Any]] = []

    raw_routes = routes.get("routes")
    if not isinstance(raw_routes, list):
        raise ValidationError("routes.routes: expected an array")
    ordered_routes, active_rounds = _round_order(
        raw_routes, planned_hops
    )
    completion_by_round: Dict[int, int] = {}
    entry_index = 0
    for route in ordered_routes:
        transport_round = route.get("transport_round", 0)
        prior_completions = [
            completion
            for round_index, completion in completion_by_round.items()
            if round_index < transport_round
        ]
        source_ready_slot = max(
            (
                completion + COMBINATIONAL_SETTLE_SLOTS
                for completion in prior_completions
            ),
            default=0,
        )
        if (
            transport_round == 1
            and planned_round_one_ready is not None
            and source_ready_slot > planned_round_one_ready
        ):
            raise ValidationError(
                "TDM ratio schedule exceeded its legalized round barrier: "
                f"actual={source_ready_slot}, "
                f"planned={planned_round_one_ready}"
            )
        arrival_by_node = {route["source"]: source_ready_slot - 1}
        for depth, edge in _route_hops(route):
            arc_key = (edge["link"], edge["from"], edge["to"])
            if arc_key not in arcs:
                raise ValidationError(
                    f"TDM route {route['id']!r} uses illegal edge {arc_key}"
                )
            arc = arcs[arc_key]
            link = links[edge["link"]]
            ready_slot = (
                source_ready_slot
                if edge["from"] == route["source"]
                else arrival_by_node[edge["from"]] + 1
            )
            latest_exclusive = frame_slots - link.latency_cycles
            plan_hop = (
                planned_hops.get(
                    _hop_key(
                        route["id"],
                        edge["link"],
                        edge["from"],
                        edge["to"],
                    )
                )
                if planned_hops is not None
                else None
            )
            if planned_hops is not None and plan_hop is None:
                raise ValidationError(
                    f"TDM ratio plan is missing demand {route['id']!r} "
                    f"edge {arc_key}"
                )
            if plan_hop is None:
                slot = (
                    frame_slots
                    if ready_slot >= frame_slots
                    else first_available_slot(
                        arc["capacity_key"],
                        ready_slot,
                    )
                )
                lane = (
                    slot_fill[arc["capacity_key"]][slot]
                    if slot < frame_slots
                    else 0
                )
            else:
                lane = plan_hop["lane"]
                ratio = plan_hop["discrete_ratio"]
                ratio_window_end = min(
                    latest_exclusive,
                    ready_slot + ratio,
                )
                slot = ready_slot
                while (
                    slot < ratio_window_end
                    and (slot, lane)
                    in planned_occupancy[arc["capacity_key"]]
                ):
                    slot += 1
            if slot >= latest_exclusive:
                raise ValidationError(
                    f"TDM scheduling is infeasible for demand {route['id']!r} "
                    f"edge {arc_key}: ready={ready_slot}, "
                    f"frame_slots={frame_slots}, "
                    f"latency={link.latency_cycles}"
                )
            if plan_hop is not None and slot >= ready_slot + ratio:
                raise ValidationError(
                    f"TDM ratio schedule is infeasible for demand "
                    f"{route['id']!r} edge {arc_key}: ready={ready_slot}, "
                    f"ratio={ratio}, lane={lane}"
                )
            if plan_hop is None:
                slot_fill[arc["capacity_key"]][slot] += 1
                if (
                    slot_fill[arc["capacity_key"]][slot]
                    == link.data_lanes_per_direction
                ):
                    next_available[arc["capacity_key"]][slot] = (
                        first_available_slot(
                            arc["capacity_key"],
                            slot + 1,
                        )
                    )
            else:
                planned_occupancy[arc["capacity_key"]].add(
                    (slot, lane)
                )
            arrival_slot = slot + link.latency_cycles
            arrival_by_node[edge["to"]] = arrival_slot
            entry = {
                    "id": f"s{entry_index:06d}",
                    "demand": route["id"],
                    "net": route["net"],
                    "hop": depth,
                    "link": edge["link"],
                    "from": edge["from"],
                    "to": edge["to"],
                    "capacity_key": arc["capacity_key"],
                    "slot": slot,
                    "lane": lane,
                    "ready_slot": ready_slot,
                    "arrival_slot": arrival_slot,
                }
            if plan_hop is not None:
                entry.update(
                    {
                        "ratio_plan_hop": plan_hop["index"],
                        "continuous_ratio": plan_hop[
                            "continuous_ratio"
                        ],
                        "tdm_ratio": plan_hop["discrete_ratio"],
                        "ratio_wait_slots": slot - ready_slot,
                    }
                )
            entries.append(entry)
            entry_index += 1

        missing = sorted(set(route["sinks"]) - set(arrival_by_node))
        if missing:
            raise ValidationError(
                f"TDM route {route['id']!r} did not schedule sinks {missing}"
            )
        completion_slot = max(
            arrival_by_node[sink] for sink in route["sinks"]
        )
        completion_by_round[transport_round] = max(
            completion_slot,
            completion_by_round.get(transport_round, completion_slot),
        )
        demand_completions.append(
            {
                "demand": route["id"],
                "net": route["net"],
                "transport_round": transport_round,
                "source_ready_slot": source_ready_slot,
                "completion_slot": completion_slot,
            }
        )

    entries.sort(
        key=lambda entry: (
            entry["slot"],
            entry["capacity_key"],
            entry["lane"],
            entry["demand"],
        )
    )
    domain_schedules = _domain_schedule_records(
        platform,
        constraints,
        entries,
    )
    metrics = {
        "demands": len(raw_routes),
        "scheduled_bit_hops": len(entries),
        "frame_slots": frame_slots,
        "completion_slot": max(
            (
                completion["completion_slot"]
                for completion in demand_completions
            ),
            default=0,
        ),
        "max_domain_utilization": max(
            (domain["utilization"] for domain in domain_schedules),
            default=0.0,
        ),
        "transport_rounds": len(active_rounds),
        "round_barriers": max(0, len(active_rounds) - 1),
        "max_transport_round": max(active_rounds, default=0),
        "combinational_settle_slots": COMBINATIONAL_SETTLE_SLOTS,
        "collisions": 0,
    }
    if ratio_plan is not None:
        metrics.update(
            {
                "ratio_constrained_hops": len(entries),
                "max_tdm_ratio": max(
                    entry["tdm_ratio"] for entry in entries
                ),
                "maximum_ratio_wait_slots": max(
                    entry["ratio_wait_slots"] for entry in entries
                ),
            }
        )
    return {
        "schema": TDM_SCHEDULE_SCHEMA,
        "design": routes.get("design"),
        "platform": platform.name,
        "provider": (
            TDM_ACADEMIC_SCHEDULE_PROVIDER
            if ratio_plan is not None
            else TDM_BASELINE_PROVIDER
        ),
        **(
            {
                "ratio_assignment": {
                    "schema": ratio_plan["schema"],
                    "provider": ratio_plan["provider"],
                    "configuration": ratio_plan["configuration"],
                    "metrics": ratio_plan["metrics"],
                    "round_barrier_legalization": ratio_plan[
                        "round_barrier_legalization"
                    ],
                }
            }
            if ratio_plan is not None
            else {}
        ),
        "route_constraints": constraints,
        "routes": [
            {
                "id": route["id"],
                "net": route["net"],
                "source": route["source"],
                "sinks": list(route["sinks"]),
                "transport_round": route.get("transport_round", 0),
                "tree_edges": list(route["tree_edges"]),
            }
            for route in sorted(raw_routes, key=lambda item: item["id"])
        ],
        "entries": entries,
        "demand_completions": sorted(
            demand_completions, key=lambda item: item["demand"]
        ),
        "domain_schedules": domain_schedules,
        "metrics": metrics,
    }


def _domain_schedule_records(
    platform: Platform,
    constraints: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    _, _, capacities = build_directed_graph(platform, constraints)
    links = _link_by_id(platform)
    count_by_key: Dict[str, int] = defaultdict(int)
    for entry in entries:
        count_by_key[entry["capacity_key"]] += 1
    records = []
    for key in sorted(capacities):
        capacity = capacities[key]
        lanes = links[capacity["link"]].data_lanes_per_direction
        scheduled = count_by_key[key]
        records.append(
            {
                "key": key,
                "link": capacity["link"],
                "direction": capacity["direction"],
                "lanes": lanes,
                "frame_slots": constraints["frame_slots"],
                "scheduled_bit_hops": scheduled,
                "capacity_bit_hops": lanes * constraints["frame_slots"],
                "utilization": (
                    scheduled
                    / (lanes * constraints["frame_slots"])
                ),
            }
        )
    return records


def _expected_hops(
    routes: Mapping[str, Any],
) -> Dict[HopKey, Tuple[Mapping[str, Any], int]]:
    expected = {}
    for route in routes["routes"]:
        for depth, edge in _route_hops(route):
            key = _hop_key(
                route["id"], edge["link"], edge["from"], edge["to"]
            )
            expected[key] = (route, depth)
    return expected


def validate_tdm_schedule(
    routes: Mapping[str, Any],
    platform: Platform,
    schedule: Mapping[str, Any],
    ratio_plan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if schedule.get("schema") != TDM_SCHEDULE_SCHEMA:
        raise ValidationError(
            f"schedule.schema: expected {TDM_SCHEDULE_SCHEMA!r}, "
            f"got {schedule.get('schema')!r}"
        )
    constraints = normalize_route_constraints(
        schedule.get("route_constraints"),
        platform,
    )
    if constraints != normalize_route_constraints(
        routes.get("constraints"), platform
    ):
        raise ValidationError(
            "schedule.route_constraints does not match routes"
        )
    frame_slots = constraints["frame_slots"]
    _, arcs, _ = build_directed_graph(platform, constraints)
    links = _link_by_id(platform)
    expected = _expected_hops(routes)
    academic_schedule = (
        schedule.get("provider") == TDM_ACADEMIC_SCHEDULE_PROVIDER
    )
    extended_schedule = schedule.get("provider") in {
        TDM_BASELINE_PROVIDER,
        TDM_ACADEMIC_SCHEDULE_PROVIDER,
    }
    planned_hops = None
    if academic_schedule:
        if ratio_plan is None:
            raise ValidationError(
                "academic TDM schedule validation requires ratio_plan"
            )
        from .tdm_ratio import (
            ratio_plan_by_hop,
            validate_tdm_ratio_plan,
        )

        validate_tdm_ratio_plan(routes, platform, ratio_plan)
        expected_ratio_assignment = {
            "schema": ratio_plan["schema"],
            "provider": ratio_plan["provider"],
            "configuration": ratio_plan["configuration"],
            "metrics": ratio_plan["metrics"],
            "round_barrier_legalization": ratio_plan[
                "round_barrier_legalization"
            ],
        }
        if schedule.get("ratio_assignment") != expected_ratio_assignment:
            raise ValidationError(
                "schedule.ratio_assignment does not match ratio plan"
            )
        planned_hops = ratio_plan_by_hop(ratio_plan)
    elif ratio_plan is not None:
        raise ValidationError(
            "ratio_plan was supplied for a non-academic TDM schedule"
        )
    expected_route_metadata = []
    for route in sorted(routes["routes"], key=lambda item: item["id"]):
        record = {
            "id": route["id"],
            "net": route["net"],
            "source": route["source"],
            "sinks": list(route["sinks"]),
            "tree_edges": list(route["tree_edges"]),
        }
        if extended_schedule:
            record["transport_round"] = route.get("transport_round", 0)
        expected_route_metadata.append(record)
    if schedule.get("routes") != expected_route_metadata:
        raise ValidationError("schedule.routes does not match system routes")

    raw_entries = schedule.get("entries")
    if not isinstance(raw_entries, list):
        raise ValidationError("schedule.entries: expected an array")
    entries_by_hop: Dict[HopKey, Mapping[str, Any]] = {}
    occupancy: Dict[str, Set[Tuple[int, int]]] = defaultdict(set)
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ValidationError(f"schedule.entries[{index}]: expected an object")
        key = _hop_key(
            entry.get("demand"),
            entry.get("link"),
            entry.get("from"),
            entry.get("to"),
        )
        if key not in expected:
            raise ValidationError(
                f"schedule.entries[{index}]: unexpected route hop {key}"
            )
        if key in entries_by_hop:
            raise ValidationError(
                f"schedule.entries[{index}]: duplicate route hop {key}"
            )
        entries_by_hop[key] = entry
        route, depth = expected[key]
        if entry.get("net") != route["net"] or entry.get("hop") != depth:
            raise ValidationError(
                f"schedule.entries[{index}]: net/hop does not match route"
            )
        arc = arcs[(entry["link"], entry["from"], entry["to"])]
        if entry.get("capacity_key") != arc["capacity_key"]:
            raise ValidationError(
                f"schedule.entries[{index}].capacity_key: incorrect"
            )
        link = links[entry["link"]]
        slot = entry.get("slot")
        lane = entry.get("lane")
        if (
            isinstance(slot, bool)
            or not isinstance(slot, int)
            or slot < 0
            or slot >= frame_slots
        ):
            raise ValidationError(
                f"schedule.entries[{index}].slot: out of range"
            )
        if (
            isinstance(lane, bool)
            or not isinstance(lane, int)
            or lane < 0
            or lane >= link.data_lanes_per_direction
        ):
            raise ValidationError(
                f"schedule.entries[{index}].lane: out of range"
            )
        collision = (slot, lane)
        if collision in occupancy[arc["capacity_key"]]:
            raise ValidationError(
                f"schedule collision in {arc['capacity_key']!r} at "
                f"slot={slot}, lane={lane}"
            )
        occupancy[arc["capacity_key"]].add(collision)
        if academic_schedule:
            planned = planned_hops[key]
            ready_value = entry.get("ready_slot")
            if (
                isinstance(ready_value, bool)
                or not isinstance(ready_value, int)
            ):
                raise ValidationError(
                    f"schedule.entries[{index}].ready_slot: "
                    "expected an integer"
                )
            expected_ratio_fields = {
                "ratio_plan_hop": planned["index"],
                "continuous_ratio": planned["continuous_ratio"],
                "tdm_ratio": planned["discrete_ratio"],
                "ratio_wait_slots": slot - ready_value,
            }
            for field, value in expected_ratio_fields.items():
                if entry.get(field) != value:
                    raise ValidationError(
                        f"schedule.entries[{index}].{field}: "
                        "does not match ratio plan"
                    )
            if lane != planned["lane"]:
                raise ValidationError(
                    f"schedule.entries[{index}].lane: "
                    "does not match ratio plan"
                )
            if entry["ratio_wait_slots"] >= entry["tdm_ratio"]:
                raise ValidationError(
                    f"schedule.entries[{index}]: "
                    "wait exceeds TDM ratio window"
                )
        expected_arrival = slot + link.latency_cycles
        if entry.get("arrival_slot") != expected_arrival:
            raise ValidationError(
                f"schedule.entries[{index}].arrival_slot: expected "
                f"{expected_arrival}"
            )
        if expected_arrival >= frame_slots:
            raise ValidationError(
                f"schedule.entries[{index}]: arrival exceeds frame"
            )
    if set(entries_by_hop) != set(expected):
        missing = sorted(set(expected) - set(entries_by_hop))
        raise ValidationError(
            f"schedule.entries: route-hop coverage is incomplete {missing[:8]}"
        )

    ordered_routes, active_rounds = _round_order(routes["routes"])
    completion_by_round: Dict[int, int] = {}
    completions = []
    for route in ordered_routes:
        transport_round = route.get("transport_round", 0)
        prior_completions = [
            completion
            for round_index, completion in completion_by_round.items()
            if round_index < transport_round
        ]
        source_ready_slot = max(
            (
                completion + COMBINATIONAL_SETTLE_SLOTS
                for completion in prior_completions
            ),
            default=0,
        )
        arrival_by_node = {route["source"]: source_ready_slot - 1}
        for depth, edge in _route_hops(route):
            del depth
            entry = entries_by_hop[
                _hop_key(
                    route["id"],
                    edge["link"],
                    edge["from"],
                    edge["to"],
                )
            ]
            ready = (
                source_ready_slot
                if edge["from"] == route["source"]
                else arrival_by_node[edge["from"]] + 1
            )
            if entry.get("ready_slot") != ready:
                raise ValidationError(
                    f"schedule demand {route['id']!r}: ready-slot mismatch"
                )
            if entry["slot"] < ready:
                raise ValidationError(
                    f"schedule demand {route['id']!r}: precedence violation"
                )
            arrival_by_node[edge["to"]] = entry["arrival_slot"]
        missing_sinks = sorted(set(route["sinks"]) - set(arrival_by_node))
        if missing_sinks:
            raise ValidationError(
                f"schedule demand {route['id']!r}: missing sinks "
                f"{missing_sinks}"
            )
        completion_slot = max(
            arrival_by_node[sink] for sink in route["sinks"]
        )
        completion_by_round[transport_round] = max(
            completion_slot,
            completion_by_round.get(transport_round, completion_slot),
        )
        completion = {
            "demand": route["id"],
            "net": route["net"],
            "completion_slot": completion_slot,
        }
        if extended_schedule:
            completion.update(
                {
                    "transport_round": transport_round,
                    "source_ready_slot": source_ready_slot,
                }
            )
        completions.append(completion)
    completions.sort(key=lambda item: item["demand"])
    if schedule.get("demand_completions") != completions:
        raise ValidationError(
            "schedule.demand_completions does not match recomputed values"
        )

    expected_domains = _domain_schedule_records(
        platform, constraints, raw_entries
    )
    if schedule.get("domain_schedules") != expected_domains:
        raise ValidationError(
            "schedule.domain_schedules does not match recomputed occupancy"
        )
    expected_metrics = {
        "demands": len(routes["routes"]),
        "scheduled_bit_hops": len(expected),
        "frame_slots": frame_slots,
        "completion_slot": max(
            (item["completion_slot"] for item in completions),
            default=0,
        ),
        "max_domain_utilization": max(
            (domain["utilization"] for domain in expected_domains),
            default=0.0,
        ),
        "collisions": 0,
    }
    if extended_schedule:
        expected_metrics.update(
            {
                "transport_rounds": len(active_rounds),
                "round_barriers": max(0, len(active_rounds) - 1),
                "max_transport_round": max(active_rounds, default=0),
                "combinational_settle_slots": COMBINATIONAL_SETTLE_SLOTS,
            }
        )
    if academic_schedule:
        expected_metrics.update(
            {
                "ratio_constrained_hops": len(expected),
                "max_tdm_ratio": max(
                    entry["tdm_ratio"] for entry in raw_entries
                ),
                "maximum_ratio_wait_slots": max(
                    entry["ratio_wait_slots"] for entry in raw_entries
                ),
            }
        )
    if schedule.get("metrics") != expected_metrics:
        raise ValidationError(
            "schedule.metrics does not match independently recomputed metrics"
        )
    return {
        "status": "pass",
        **expected_metrics,
        "routed_sinks": sum(
            len(route["sinks"]) for route in routes["routes"]
        ),
    }


def reconstruct_tdm_schedule_timing(
    routes: Mapping[str, Any],
    platform: Platform,
    schedule: Mapping[str, Any],
) -> Dict[str, Any]:
    """Reconstruct scheduled transport delay on every imported STA path.

    This deliberately uses the concrete slot assignment rather than a TDM
    ratio bound, so baseline and academic schedules are evaluated with the
    same timing model.
    """
    from .tdm_ratio import _normalized_slack, _prepare_model

    model = _prepare_model(routes, platform)
    entries = {}
    for entry in schedule["entries"]:
        key = _hop_key(
            entry["demand"],
            entry["link"],
            entry["from"],
            entry["to"],
        )
        if key in entries:
            raise ValidationError(
                f"schedule timing reconstruction found duplicate hop {key}"
            )
        entries[key] = entry

    records = []
    for timing_path in model["timing_paths"]:
        delay_ns = timing_path["fixed_delay_ns"]
        for hop_index in timing_path["hops"]:
            hop = model["hops"][hop_index]
            key = _hop_key(
                hop["demand"],
                hop["link"],
                hop["from"],
                hop["to"],
            )
            if key not in entries:
                raise ValidationError(
                    "schedule timing reconstruction is missing routed hop "
                    f"{key}"
                )
            entry = entries[key]
            wait_slots = entry["slot"] - entry["ready_slot"]
            if wait_slots < 0:
                raise ValidationError(
                    "schedule timing reconstruction found a negative wait "
                    f"for hop {key}"
                )
            delay_ns += (
                hop["base_delay_ns"]
                + hop["beta_ns"] * wait_slots
            )
        slack_ns = timing_path["clock_period_ns"] - delay_ns
        normalized_slack = _normalized_slack(
            timing_path["clock_period_ns"],
            slack_ns,
            model["normalization"],
        )
        records.append(
            {
                "path": timing_path["id"],
                "delay_ns": delay_ns,
                "slack_ns": slack_ns,
                "normalized_slack": normalized_slack,
            }
        )

    if not records:
        raise ValidationError(
            "schedule timing reconstruction has no timing paths"
        )
    records.sort(
        key=lambda record: (
            record["normalized_slack"],
            record["path"],
        )
    )
    worst = records[0]
    normalized = sorted(
        record["normalized_slack"] for record in records
    )
    return {
        "status": "pass",
        "timing_paths": len(records),
        "worst_path": worst["path"],
        "worst_delay_ns": worst["delay_ns"],
        "worst_slack_ns": worst["slack_ns"],
        "worst_normalized_slack": worst["normalized_slack"],
        "negative_slack_paths": sum(
            record["slack_ns"] < 0.0 for record in records
        ),
        "p01_normalized_slack": normalized[len(normalized) // 100],
        "median_normalized_slack": normalized[len(normalized) // 2],
    }


def simulate_tdm_schedule(
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    frames: int = 16,
) -> Dict[str, Any]:
    if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
        raise ValidationError("TDM simulation frames: expected a positive integer")
    entries_by_slot: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for entry in schedule["entries"]:
        entries_by_slot[entry["slot"]].append(entry)
    demands = {
        route["id"]: route for route in routes["routes"]
    }
    trace = hashlib.sha256()
    delivered = 0

    for frame in range(frames):
        node_values: Dict[Tuple[str, str], int] = {}
        source_values: Dict[str, int] = {}
        for demand_id, route in demands.items():
            digest = hashlib.sha256(
                f"{frame}:{demand_id}:{route['net']}".encode("utf-8")
            ).digest()
            value = digest[0] & 1
            source_values[demand_id] = value
            node_values[(demand_id, route["source"])] = value

        arrivals: Dict[int, List[Tuple[Mapping[str, Any], int]]] = defaultdict(
            list
        )
        for slot in range(schedule["metrics"]["frame_slots"]):
            for entry in sorted(
                entries_by_slot.get(slot, []),
                key=lambda item: (item["hop"], item["id"]),
            ):
                source_key = (entry["demand"], entry["from"])
                if source_key not in node_values:
                    raise ValidationError(
                        f"TDM simulation: data unavailable for {entry['id']!r}"
                    )
                arrivals[entry["arrival_slot"]].append(
                    (entry, node_values[source_key])
                )
            for entry, value in sorted(
                arrivals.get(slot, []),
                key=lambda item: (item[0]["hop"], item[0]["id"]),
            ):
                node_values[(entry["demand"], entry["to"])] = value

        for demand_id, route in sorted(demands.items()):
            expected = source_values[demand_id]
            for sink in route["sinks"]:
                actual = node_values.get((demand_id, sink))
                if actual != expected:
                    raise ValidationError(
                        f"TDM simulation frame {frame}: demand {demand_id!r} "
                        f"sink {sink!r} expected {expected}, got {actual!r}"
                    )
                trace.update(
                    f"{frame}:{demand_id}:{sink}:{actual}\n".encode("utf-8")
                )
                delivered += 1
    return {
        "status": "pass",
        "frames": frames,
        "demands": len(demands),
        "delivered_sink_values": delivered,
        "trace_sha256": trace.hexdigest(),
    }


def schedule_to_tsv(schedule: Mapping[str, Any]) -> str:
    lines = [
        "entry\tdemand\tnet\tlink\tfrom\tto\tslot\tlane\tready\tarrival"
    ]
    for entry in schedule["entries"]:
        lines.append(
            "\t".join(
                str(entry[field])
                for field in (
                    "id",
                    "demand",
                    "net",
                    "link",
                    "from",
                    "to",
                    "slot",
                    "lane",
                    "ready_slot",
                    "arrival_slot",
                )
            )
        )
    return "\n".join(lines) + "\n"


def build_transport_manifest(
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    platform: Platform,
) -> Dict[str, Any]:
    demand_index = {
        route["id"]: index
        for index, route in enumerate(
            sorted(routes["routes"], key=lambda item: item["id"])
        )
    }
    endpoints = []
    for fpga in platform.fpgas:
        tx_entries = [
            entry["id"]
            for entry in schedule["entries"]
            if entry["from"] == fpga.id
        ]
        rx_entries = [
            entry["id"]
            for entry in schedule["entries"]
            if entry["to"] == fpga.id
        ]
        endpoints.append(
            {
                "fpga": fpga.id,
                "tx_entries": sorted(tx_entries),
                "rx_entries": sorted(rx_entries),
            }
        )
    return {
        "schema": "emuflow.transport-manifest/v1",
        "design": schedule["design"],
        "platform": platform.name,
        "frame_slots": schedule["metrics"]["frame_slots"],
        "demand_index": dict(sorted(demand_index.items())),
        "endpoints": endpoints,
        "rtl_primitives": [
            "rtl/transport/emuflow_tdm_link.sv",
            "rtl/transport/emuflow_frame_barrier.sv",
        ],
    }


def schedule_to_systemverilog_testbench(
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    platform: Platform,
    frames: int,
) -> str:
    ordered_routes = sorted(routes["routes"], key=lambda item: item["id"])
    demand_index = {
        route["id"]: index for index, route in enumerate(ordered_routes)
    }
    fpga_index = {
        fpga.id: index for index, fpga in enumerate(platform.fpgas)
    }
    links = _link_by_id(platform)
    channel_keys = sorted(
        {entry["capacity_key"] for entry in schedule["entries"]}
    )
    channel_index = {
        key: index for index, key in enumerate(channel_keys)
    }
    channel_link = {}
    for entry in schedule["entries"]:
        channel_link[entry["capacity_key"]] = links[entry["link"]]

    lines = [
        "`timescale 1ns/1ps",
        "module transport_schedule_tb;",
        f"  localparam integer DEMANDS = {len(ordered_routes)};",
        f"  localparam integer FPGAS = {len(platform.fpgas)};",
        f"  localparam integer FRAME_SLOTS = {schedule['metrics']['frame_slots']};",
        f"  localparam integer FRAMES = {frames};",
        "  reg clk = 1'b0;",
        "  reg reset = 1'b1;",
        "  reg [DEMANDS-1:0] source_bits;",
        "  reg [DEMANDS-1:0] node_value [0:FPGAS-1];",
        "  integer frame_index;",
        "  integer slot_index;",
        "  integer demand_loop;",
        "  integer fpga_loop;",
        "  always #5 clk = ~clk;",
    ]
    for key in channel_keys:
        index = channel_index[key]
        link = channel_link[key]
        lanes = link.data_lanes_per_direction
        lines.extend(
            [
                f"  reg [{lanes - 1}:0] tx_data_{index};",
                f"  reg [{lanes - 1}:0] tx_valid_{index};",
                f"  wire [{lanes - 1}:0] rx_data_{index};",
                f"  wire [{lanes - 1}:0] rx_valid_{index};",
                "  emuflow_tdm_link #(",
                f"    .LANES({lanes}),",
                f"    .LATENCY({link.latency_cycles})",
                f"  ) channel_{index} (",
                "    .clk(clk), .reset(reset),",
                f"    .tx_data(tx_data_{index}),",
                f"    .tx_valid(tx_valid_{index}),",
                f"    .rx_data(rx_data_{index}),",
                f"    .rx_valid(rx_valid_{index})",
                "  );",
            ]
        )

    lines.extend(
        [
            "  task drive_slot(input integer current_slot);",
            "  begin",
        ]
    )
    for key in channel_keys:
        index = channel_index[key]
        lines.extend(
            [
                f"    tx_data_{index} = '0;",
                f"    tx_valid_{index} = '0;",
            ]
        )
    lines.append("    case (current_slot)")
    entries_by_slot: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for entry in schedule["entries"]:
        entries_by_slot[entry["slot"]].append(entry)
    for slot in sorted(entries_by_slot):
        lines.append(f"      {slot}: begin")
        for entry in sorted(entries_by_slot[slot], key=lambda item: item["id"]):
            channel = channel_index[entry["capacity_key"]]
            demand = demand_index[entry["demand"]]
            source = fpga_index[entry["from"]]
            lines.append(
                f"        tx_data_{channel}[{entry['lane']}] = "
                f"node_value[{source}][{demand}];"
            )
            lines.append(
                f"        tx_valid_{channel}[{entry['lane']}] = 1'b1;"
            )
        lines.append("      end")
    lines.extend(["      default: begin end", "    endcase", "  end", "  endtask"])

    arrivals_by_slot: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
    for entry in schedule["entries"]:
        arrivals_by_slot[entry["arrival_slot"]].append(entry)
    lines.extend(
        [
            "  task capture_slot(input integer current_slot);",
            "  begin",
            "    case (current_slot)",
        ]
    )
    for slot in sorted(arrivals_by_slot):
        lines.append(f"      {slot}: begin")
        for entry in sorted(
            arrivals_by_slot[slot],
            key=lambda item: (item["hop"], item["id"]),
        ):
            channel = channel_index[entry["capacity_key"]]
            demand = demand_index[entry["demand"]]
            sink = fpga_index[entry["to"]]
            lines.extend(
                [
                    f"        if (!rx_valid_{channel}[{entry['lane']}]) "
                    f"$fatal(1, \"missing valid for {entry['id']}\");",
                    f"        node_value[{sink}][{demand}] = "
                    f"rx_data_{channel}[{entry['lane']}];",
                ]
            )
        lines.append("      end")
    lines.extend(["      default: begin end", "    endcase", "  end", "  endtask"])

    lines.extend(
        [
            "  initial begin",
            "    source_bits = '0;",
        ]
    )
    for key in channel_keys:
        index = channel_index[key]
        lines.extend(
            [f"    tx_data_{index} = '0;", f"    tx_valid_{index} = '0;"]
        )
    lines.extend(
        [
            "    repeat (3) @(posedge clk);",
            "    reset = 1'b0;",
            "    for (frame_index = 0; frame_index < FRAMES; "
            "frame_index = frame_index + 1) begin",
            "      for (demand_loop = 0; demand_loop < DEMANDS; "
            "demand_loop = demand_loop + 1)",
            "        source_bits[demand_loop] = "
            "(frame_index + demand_loop) & 1;",
            "      for (fpga_loop = 0; fpga_loop < FPGAS; "
            "fpga_loop = fpga_loop + 1)",
            "        node_value[fpga_loop] = '0;",
        ]
    )
    for route in ordered_routes:
        demand = demand_index[route["id"]]
        source = fpga_index[route["source"]]
        lines.append(
            f"      node_value[{source}][{demand}] = source_bits[{demand}];"
        )
    lines.extend(
        [
            "      for (slot_index = 0; slot_index < FRAME_SLOTS; "
            "slot_index = slot_index + 1) begin",
            "        drive_slot(slot_index);",
            "        @(posedge clk); #1;",
            "        capture_slot(slot_index);",
            "      end",
        ]
    )
    for route in ordered_routes:
        demand = demand_index[route["id"]]
        for sink_id in route["sinks"]:
            sink = fpga_index[sink_id]
            lines.append(
                f"      if (node_value[{sink}][{demand}] !== "
                f"source_bits[{demand}]) "
                f"$fatal(1, \"delivery mismatch {route['id']}->{sink_id}\");"
            )
    lines.extend(
        [
            "    end",
            f"    $display(\"EMUFLOW_TDM_RTL_SIM status=pass frames=%0d "
            f"demands={len(ordered_routes)} entries={len(schedule['entries'])}\", "
            "FRAMES);",
            "    $finish;",
            "  end",
            "endmodule",
            "",
        ]
    )
    return "\n".join(lines)
