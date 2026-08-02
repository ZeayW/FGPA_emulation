"""Exact small-instance oracles for Phase 5 ratio legalization."""

from __future__ import annotations

import functools
import math
from collections import defaultdict, deque
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .errors import ValidationError
from .platform import Platform


def exact_discrete_ratio_legalization(
    continuous: Sequence[float],
    directions: Sequence[int],
    *,
    lanes: int,
    allowed_ratios: Sequence[int],
    displacement_bound: float,
) -> Dict[str, Any]:
    """Exhaustively solve TODAES 2020 Equation (18) on one domain.

    Signals are ordered by direction and continuous ratio as justified by the
    exchange argument in the paper.  Every group represents one physical
    lane, has one direction and ratio, and contains no more signals than that
    ratio.  The objective is minimum total displacement under a fixed optimal
    maximum-displacement bound.
    """

    if len(continuous) != len(directions) or not continuous:
        raise ValidationError("TDM oracle input vectors must be non-empty")
    if lanes <= 0 or displacement_bound < 0.0:
        raise ValidationError("TDM oracle capacity/bound is invalid")
    allowed = sorted(set(allowed_ratios))
    if not allowed or allowed[0] <= 0:
        raise ValidationError("TDM oracle allowed ratios are invalid")
    order = sorted(
        range(len(continuous)),
        key=lambda index: (
            directions[index],
            continuous[index],
            index,
        ),
    )

    @functools.lru_cache(maxsize=None)
    def solve(position: int, remaining: int) -> Tuple[float, Tuple[Any, ...]]:
        if position == len(order):
            return 0.0, ()
        if remaining == 0:
            return math.inf, ()
        best = (math.inf, ())
        first = order[position]
        for ratio in allowed:
            if (
                abs(continuous[first] - ratio)
                > displacement_bound + 1.0e-9
            ):
                continue
            displacement = 0.0
            for end in range(
                position,
                min(len(order), position + ratio),
            ):
                signal = order[end]
                if directions[signal] != directions[first]:
                    break
                delta = abs(continuous[signal] - ratio)
                if delta > displacement_bound + 1.0e-9:
                    break
                displacement += delta
                suffix_cost, suffix = solve(end + 1, remaining - 1)
                candidate = (
                    displacement + suffix_cost,
                    ((position, end + 1, ratio), *suffix),
                )
                if candidate[0] < best[0] - 1.0e-9 or (
                    abs(candidate[0] - best[0]) <= 1.0e-9
                    and candidate[1] < best[1]
                ):
                    best = candidate
        return best

    cost, groups = solve(0, lanes)
    if not math.isfinite(cost):
        raise ValidationError("TDM oracle found no legal ratio assignment")
    discrete = [0] * len(order)
    lane_by_signal = [-1] * len(order)
    for lane, (start, end, ratio) in enumerate(groups):
        for position in range(start, end):
            signal = order[position]
            discrete[signal] = ratio
            lane_by_signal[signal] = lane
    return {
        "total_displacement": cost,
        "maximum_displacement": max(
            abs(value - discrete[index])
            for index, value in enumerate(continuous)
        ),
        "discrete_ratios": discrete,
        "lanes": lane_by_signal,
        "groups": [list(group) for group in groups],
    }


def _normalized_slack(
    period: float,
    slack: float,
    normalization: Mapping[str, float],
) -> float:
    if slack >= 0.0:
        return (
            slack
            * period
            / (
                normalization["positive_slack_scale_ns"]
                * normalization["max_clock_period_ns"]
            )
        )
    return slack / (
        normalization["negative_slack_scale_ns"] * period
    )


def exact_single_round_slot_schedule(
    routes: Mapping[str, Any],
    platform: Platform,
    ratio_plan: Mapping[str, Any],
    *,
    max_hops: int = 12,
) -> Dict[str, Any]:
    """Exhaustively solve a compact time-expanded lane/slot schedule.

    The oracle covers the single-round case with fixed legal lanes and ratios.
    It enforces lane collision, tree precedence, link latency, the ratio wait
    window, and frame arrival.  Multi-round exact scheduling is intentionally
    a separate gate because its global barrier couples all route completions.
    """

    from .routing import normalize_route_constraints
    from .tdm_ratio import validate_tdm_ratio_plan

    validate_tdm_ratio_plan(routes, platform, ratio_plan)
    if any(route.get("transport_round", 0) != 0 for route in routes["routes"]):
        raise ValidationError("slot oracle currently supports one round")
    hops = list(ratio_plan["hops"])
    if len(hops) > max_hops:
        raise ValidationError(
            f"slot oracle supports at most {max_hops} routed hops"
        )
    constraints = normalize_route_constraints(
        routes.get("constraints"), platform
    )
    frame_slots = constraints["frame_slots"]
    link_by_id = {link.id: link for link in platform.links}
    hop_by_key = {
        (hop["demand"], hop["link"], hop["from"], hop["to"]): hop
        for hop in hops
    }
    parent = {}
    depth = {}
    for route in routes["routes"]:
        incoming = {}
        outgoing = defaultdict(list)
        for edge in route["tree_edges"]:
            key = (
                route["id"],
                edge["link"],
                edge["from"],
                edge["to"],
            )
            hop = hop_by_key[key]
            incoming[edge["to"]] = hop["index"]
            outgoing[edge["from"]].append(hop)
        queue = deque([(route["source"], 0)])
        while queue:
            node, node_depth = queue.popleft()
            for hop in sorted(
                outgoing[node], key=lambda item: item["index"]
            ):
                parent[hop["index"]] = incoming.get(node)
                depth[hop["index"]] = node_depth
                queue.append((hop["to"], node_depth + 1))
    if set(parent) != {hop["index"] for hop in hops}:
        raise ValidationError("slot oracle route trees are incomplete")

    ordered = sorted(
        (hop["index"] for hop in hops),
        key=lambda index: (depth[index], index),
    )
    hop_by_index = {hop["index"]: hop for hop in hops}
    occupancy = set()
    slot_by_hop: Dict[int, int] = {}
    ready_by_hop: Dict[int, int] = {}
    best = None
    explored = 0

    def score_complete() -> Tuple[Any, ...]:
        worst = float("inf")
        for path in ratio_plan["timing_paths"]:
            delay = path["fixed_delay_ns"]
            for hop_index in path["hops"]:
                hop = hop_by_index[hop_index]
                wait = slot_by_hop[hop_index] - ready_by_hop[hop_index]
                delay += hop["base_delay_ns"] + hop["beta_ns"] * wait
            slack = path["clock_period_ns"] - delay
            worst = min(
                worst,
                _normalized_slack(
                    path["clock_period_ns"],
                    slack,
                    ratio_plan["normalization"],
                ),
            )
        completion = max(
            slot_by_hop[index]
            + link_by_id[hop_by_index[index]["link"]].latency_cycles
            for index in slot_by_hop
        )
        total_wait = sum(
            slot_by_hop[index] - ready_by_hop[index]
            for index in slot_by_hop
        )
        slots = tuple(slot_by_hop[index] for index in sorted(slot_by_hop))
        return worst, -completion, -total_wait, tuple(-slot for slot in slots)

    def search(position: int) -> None:
        nonlocal best, explored
        if position == len(ordered):
            explored += 1
            score = score_complete()
            if best is None or score > best[0]:
                best = (score, dict(slot_by_hop), dict(ready_by_hop))
            return
        index = ordered[position]
        hop = hop_by_index[index]
        parent_index = parent[index]
        ready = (
            0
            if parent_index is None
            else slot_by_hop[parent_index]
            + link_by_id[hop_by_index[parent_index]["link"]].latency_cycles
            + 1
        )
        latest = min(
            ready + hop["discrete_ratio"],
            frame_slots
            - link_by_id[hop["link"]].latency_cycles,
        )
        key_prefix = (hop["domain"], hop["lane"])
        for slot in range(ready, latest):
            occupancy_key = (*key_prefix, slot)
            if occupancy_key in occupancy:
                continue
            occupancy.add(occupancy_key)
            slot_by_hop[index] = slot
            ready_by_hop[index] = ready
            search(position + 1)
            del slot_by_hop[index]
            del ready_by_hop[index]
            occupancy.remove(occupancy_key)

    search(0)
    if best is None:
        raise ValidationError("slot oracle found no legal schedule")
    score, slots, ready = best
    return {
        "worst_normalized_slack": score[0],
        "completion_slot": -score[1],
        "total_wait_slots": -score[2],
        "slot_by_hop": slots,
        "ready_by_hop": ready,
        "enumerated_schedules": explored,
    }
