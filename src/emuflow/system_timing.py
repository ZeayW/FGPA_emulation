"""Unified timing closure across placed/routed FPGA partitions and links."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

from .boundary_timing import validate_boundary_timing_database
from .errors import ValidationError
from .logic_segment_timing import validate_logic_segment_timing
from .platform import Platform
from .tdm import reconstruct_tdm_schedule_timing_paths


SYSTEM_TIMING_SCHEMA = "emuflow.system-timing/v1"


def _finite_number(value: Any, context: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValidationError(f"{context} must be a finite number")
    return float(value)


def _physical_delay_database(
    physical_summary: Mapping[str, Any],
) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for item in physical_summary["fpgas"]:
        fpga = item["fpga"]
        raw_delays = item.get("clock_domain_delays_ns", {})
        if not isinstance(raw_delays, dict):
            raise ValidationError(
                f"physical summary {fpga}.clock_domain_delays_ns "
                "must be an object"
            )

        def delay(domain: str) -> float:
            if domain not in raw_delays:
                raise ValidationError(
                    f"physical summary {fpga} lacks the {domain} physical "
                    "delay required for unified system timing"
                )
            value = _finite_number(
                raw_delays[domain],
                f"physical summary {fpga}.{domain} delay",
            )
            if value < 0.0:
                raise ValidationError(
                    f"physical summary {fpga}.{domain} delay must be "
                    "non-negative"
                )
            return value

        result[fpga] = {
            "dut": delay("dut"),
            # The physical result exposes the maximum constrained crossing
            # delay. Use it for both launch and capture interfaces; this is a
            # conservative bound until endpoint-specific timing is exported.
            "cross": delay("cross"),
        }
    return result


def _logic_partition_sequence(
    transitions: List[Mapping[str, Any]],
) -> tuple[List[str], int]:
    if not transitions:
        raise ValidationError(
            "system timing path has no logical cut transitions"
        )
    sequence: List[str] = []
    discontinuities = 0
    for index, transition in enumerate(transitions):
        source = transition.get("from")
        sink = transition.get("to")
        if not isinstance(source, str) or not isinstance(sink, str):
            raise ValidationError(
                f"system timing transition {index} is invalid"
            )
        if not sequence:
            sequence.extend((source, sink))
        elif sequence[-1] == source:
            sequence.append(sink)
        else:
            # Compressed STA records can combine conservative representatives
            # whose selected multicast sinks do not form one exact endpoint
            # chain. Retain both physical segments and record the loss of path
            # exactness rather than silently omitting either delay.
            discontinuities += 1
            sequence.extend((source, sink))
    return sequence, discontinuities


def _endpoint_delay_database(
    physical_summary: Mapping[str, Any],
) -> Optional[Dict[str, float]]:
    raw_timing = physical_summary.get("boundary_timing")
    if raw_timing is None:
        return None
    identities = physical_summary.get("boundary_identities")
    if not isinstance(raw_timing, dict) or not isinstance(identities, dict):
        raise ValidationError("physical boundary timing/identity maps are invalid")
    if set(raw_timing) != set(identities):
        raise ValidationError("physical boundary timing FPGA coverage disagrees")
    result: Dict[str, float] = {}
    for fpga, database in raw_timing.items():
        validate_boundary_timing_database(database, identities[fpga])
        for endpoint in database["endpoints"]:
            endpoint_id = endpoint["id"]
            if endpoint_id in result:
                raise ValidationError(
                    f"duplicate physical boundary timing endpoint {endpoint_id!r}"
                )
            result[endpoint_id] = _finite_number(
                endpoint["delay_ns"],
                f"physical boundary endpoint {endpoint_id}",
            )
    return result


def _logic_segment_database(
    physical_summary: Mapping[str, Any],
) -> Optional[Dict[str, Dict[str, List[Mapping[str, Any]]]]]:
    raw = physical_summary.get("logic_segment_timing")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("physical logic segment timing map is invalid")
    result: Dict[str, Dict[str, List[Mapping[str, Any]]]] = {}
    segment_ids = set()
    for fpga, database in raw.items():
        validation = validate_logic_segment_timing(database)
        if validation["fpga"] != fpga:
            raise ValidationError(
                "physical logic segment timing FPGA identity disagrees"
            )
        for segment in database["segments"]:
            segment_id = segment["id"]
            if segment_id in segment_ids:
                raise ValidationError(
                    f"duplicate physical logic segment {segment_id!r}"
                )
            segment_ids.add(segment_id)
            result.setdefault(segment["system_path"], {}).setdefault(
                segment["member_path"], []
            ).append(segment)
    return result


def build_system_timing(
    runtime: Mapping[str, Any],
    routes: Mapping[str, Any],
    schedule: Mapping[str, Any],
    phase5_report: Mapping[str, Any],
    physical_summary: Mapping[str, Any],
    platform: Platform,
) -> Dict[str, Any]:
    """Compose P&R delay and concrete TDM/link delay on every STA path.

    The schedule/link component is path-exact for the Phase-4 routed sink
    selected by the timing model. Routed endpoint measurements are used when
    the physical backend provides complete BoundaryTimingDB coverage. Exact
    logic segments replace the TX endpoint measurements they subsume; the
    remaining RX/interface stages and the measured logic-stage chain are then
    composed without assuming that either component is a separable pure-logic
    delay. Backends without complete segment coverage retain conservative
    per-partition post-route maxima.
    """
    records = reconstruct_tdm_schedule_timing_paths(
        routes, platform, schedule
    )
    if not records:
        raise ValidationError("system timing has no cross-FPGA timing paths")
    reported = phase5_report.get("timing_validation")
    if not isinstance(reported, dict) or reported.get("status") != "pass":
        raise ValidationError("Phase 5 has no passing scheduled timing")
    reconstructed_worst = min(
        records, key=lambda record: (record["normalized_slack"], record["path"])
    )
    for field, expected in (
        ("worst_delay_ns", reconstructed_worst["delay_ns"]),
        ("worst_slack_ns", reconstructed_worst["slack_ns"]),
    ):
        actual = _finite_number(reported.get(field), f"Phase 5 {field}")
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1.0e-8):
            raise ValidationError(
                f"Phase 5 {field} does not match schedule reconstruction"
            )

    delays = _physical_delay_database(physical_summary)
    endpoint_delays = _endpoint_delay_database(physical_summary)
    logic_segments = _logic_segment_database(physical_summary)
    expected_fpgas = {fpga.id for fpga in platform.fpgas}
    if set(delays) != expected_fpgas:
        raise ValidationError(
            "system timing physical delay database does not cover BoardDB"
        )
    virtual_period = _finite_number(
        runtime["virtual_dut_clock"]["nominal_period_ns"],
        "runtime virtual period",
    )
    system_paths = []
    discontinuous_paths = 0
    exact_logic_paths = 0
    for record in records:
        transitions = record["cut_transitions"]
        partitions, discontinuities = _logic_partition_sequence(transitions)
        discontinuous_paths += discontinuities > 0
        unknown = sorted(set(partitions) - set(delays))
        if unknown:
            raise ValidationError(
                f"system timing path {record['path']} uses unknown FPGAs "
                f"{unknown}"
            )
        local_delay = sum(delays[fpga]["dut"] for fpga in partitions)
        scheduled_hops = record["scheduled_hops"]
        if endpoint_delays is None:
            interface_delay = sum(
                delays[hop["from"]]["cross"]
                + delays[hop["to"]]["cross"]
                for hop in scheduled_hops
            )
            interface_model = "per-partition-interface-maxima-upper-bound"
        else:
            endpoint_ids = [
                endpoint_id
                for hop in scheduled_hops
                for endpoint_id in (
                    hop["tx_endpoint"],
                    hop["rx_endpoint"],
                )
            ]
            missing = sorted(set(endpoint_ids) - set(endpoint_delays))
            if missing:
                raise ValidationError(
                    f"system timing path {record['path']} lacks endpoint "
                    f"timing for {missing[:10]}"
                )
            interface_delay = sum(
                endpoint_delays[endpoint_id] for endpoint_id in endpoint_ids
            )
            interface_model = "routed-endpoint-exact"
        logic_model = "per-partition-maximum-upper-bound"
        selected_member = None
        logic_exact = False
        physical_delay = local_delay + interface_delay
        if logic_segments is not None and endpoint_delays is not None:
            member_ids = record.get(
                "compressed_path_ids", [record["path"]]
            )
            member_segments = logic_segments.get(record["path"], {})
            candidates = []
            for member in member_ids:
                segments = member_segments.get(member, [])
                roles = [segment["kind"] for segment in segments]
                cut_indices = [segment["cut_index"] for segment in segments]
                if (
                    len(segments) != len(record["cut_nets"]) + 1
                    or roles.count("launch") != 1
                    or roles.count("capture") != 1
                    or roles.count("transition")
                    != len(record["cut_nets"]) - 1
                    or sorted(cut_indices)
                    != list(range(len(record["cut_nets"]) + 1))
                ):
                    candidates = []
                    break
                replacements = [
                    segment["replace_tx_endpoint"]
                    for segment in segments
                    if segment["replace_tx_endpoint"] is not None
                ]
                if (
                    len(replacements) != len(record["cut_nets"])
                    or len(replacements) != len(set(replacements))
                    or any(item not in endpoint_delays for item in replacements)
                ):
                    raise ValidationError(
                        f"system timing path {record['path']} logic segment "
                        "replacement coverage is invalid"
                    )
                replaced_interface = sum(
                    endpoint_delays[item] for item in replacements
                )
                unreplaced_interface = interface_delay - replaced_interface
                if unreplaced_interface < -1.0e-8:
                    raise ValidationError(
                        f"system timing path {record['path']} has invalid "
                        "endpoint replacement accounting"
                    )
                unreplaced_interface = max(0.0, unreplaced_interface)
                segment_delay = sum(
                    float(segment["delay_ns"]) for segment in segments
                )
                composite = unreplaced_interface + segment_delay
                candidates.append(
                    (
                        composite,
                        member,
                        segment_delay,
                        unreplaced_interface,
                    )
                )
            if candidates and len(candidates) == len(member_ids):
                (
                    physical_delay,
                    selected_member,
                    local_delay,
                    interface_delay,
                ) = max(candidates)
                interface_model = "routed-endpoint-exact-unreplaced-stages"
                logic_model = "routed-staging-chain-exact"
                logic_exact = True
                exact_logic_paths += 1
        total_delay = physical_delay + record["transport_delay_ns"]
        target_period = record["clock_period_ns"]
        system_paths.append(
            {
                "path": record["path"],
                "clock_domain": record["clock_domain"],
                "target_period_ns": target_period,
                "virtual_period_ns": virtual_period,
                "logical_fpga_sequence": partitions,
                "logical_cut_transitions": transitions,
                "routed_hops": record["routed_hops"],
                "preplacement_fixed_delay_ns": record[
                    "preplacement_fixed_delay_ns"
                ],
                "physical_logic_delay_bound_ns": local_delay,
                "physical_interface_delay_bound_ns": interface_delay,
                "physical_interface_model": interface_model,
                "physical_logic_model": logic_model,
                "physical_logic_member_path": selected_member,
                "physical_routed_stage_delay_bound_ns": physical_delay,
                "scheduled_link_tdm_delay_ns": record[
                    "transport_delay_ns"
                ],
                "system_delay_bound_ns": total_delay,
                "target_clock_slack_bound_ns": target_period - total_delay,
                "runtime_clock_slack_bound_ns": virtual_period - total_delay,
                "partition_chain_exact": discontinuities == 0,
                "physical_logic_segments_exact": logic_exact,
            }
        )

    target_worst = min(
        system_paths,
        key=lambda path: (path["target_clock_slack_bound_ns"], path["path"]),
    )
    runtime_worst = min(
        system_paths,
        key=lambda path: (path["runtime_clock_slack_bound_ns"], path["path"]),
    )
    maximum_delay = max(
        path["system_delay_bound_ns"] for path in system_paths
    )
    runtime_wns = runtime_worst["runtime_clock_slack_bound_ns"]
    return {
        "schema": SYSTEM_TIMING_SCHEMA,
        "status": "pass" if runtime_wns >= 0.0 else "fail",
        "design": runtime["design"],
        "platform": platform.name,
        "qualification": (
            "staging-aware-physical-plus-concrete-link-tdm"
            if exact_logic_paths == len(system_paths)
            else (
                "hybrid-staging-aware-and-partition-maxima-plus-concrete-"
                "link-tdm"
                if exact_logic_paths > 0
                else (
                    "partition-logic-maxima-plus-endpoint-exact-interface-"
                    "plus-concrete-link-tdm"
                    if endpoint_delays is not None
                    else (
                        "conservative-partition-physical-maxima-plus-"
                        "concrete-link-tdm"
                    )
                )
            )
        ),
        "path_exactness": {
            "scheduled_link_tdm": True,
            "physical_boundary_endpoints": endpoint_delays is not None,
            "physical_logic_segments": exact_logic_paths == len(system_paths),
            "physical_model": (
                "routed-staging-chain-exact"
                if exact_logic_paths == len(system_paths)
                else (
                    "hybrid-routed-staging-chain-and-partition-maxima"
                    if exact_logic_paths > 0
                    else (
                        "partition-logic-maxima-and-endpoint-exact-interface"
                        if endpoint_delays is not None
                        else "per-partition-and-interface-maxima-upper-bound"
                    )
                )
            ),
            "endpoint_exact_logic_paths": exact_logic_paths,
            "fallback_logic_paths": len(system_paths) - exact_logic_paths,
            "discontinuous_compressed_paths": discontinuous_paths,
        },
        "physical_source": {
            "provider": physical_summary.get("provider"),
            "qualification": physical_summary.get("qualification"),
        },
        "target_clock": {
            "closure_gate": False,
            "worst_path": target_worst["path"],
            "worst_slack_bound_ns": target_worst[
                "target_clock_slack_bound_ns"
            ],
            "negative_slack_paths": sum(
                path["target_clock_slack_bound_ns"] < 0.0
                for path in system_paths
            ),
        },
        "runtime_clock": {
            "closure_gate": True,
            "period_ns": virtual_period,
            "frequency_mhz": 1000.0 / virtual_period,
            "worst_path": runtime_worst["path"],
            "worst_slack_bound_ns": runtime_wns,
            "negative_slack_paths": sum(
                path["runtime_clock_slack_bound_ns"] < 0.0
                for path in system_paths
            ),
            "minimum_safe_period_bound_ns": maximum_delay,
            "maximum_safe_frequency_bound_mhz": 1000.0 / maximum_delay,
        },
        "summary": {
            "timing_paths": len(system_paths),
            "maximum_system_delay_bound_ns": maximum_delay,
        },
        "paths": system_paths,
    }
