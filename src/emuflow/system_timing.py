"""Unified timing closure across placed/routed FPGA partitions and links."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping

from .errors import ValidationError
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
    selected by the timing model. The current physical component is a safe
    partition-level upper bound: it sums the maximum P&R DUT delay for every
    logical FPGA segment and the maximum P&R clock-crossing delay at both
    ends of every cut. Endpoint-specific physical back-annotation can later
    replace this component without changing the public report shape.
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
        interface_delay = sum(
            delays[transition["from"]]["cross"]
            + delays[transition["to"]]["cross"]
            for transition in transitions
        )
        physical_delay = local_delay + interface_delay
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
                "scheduled_link_tdm_delay_ns": record[
                    "transport_delay_ns"
                ],
                "system_delay_bound_ns": total_delay,
                "target_clock_slack_bound_ns": target_period - total_delay,
                "runtime_clock_slack_bound_ns": virtual_period - total_delay,
                "partition_chain_exact": discontinuities == 0,
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
            "conservative-partition-physical-maxima-plus-concrete-link-tdm"
        ),
        "path_exactness": {
            "scheduled_link_tdm": True,
            "physical_boundary_endpoints": False,
            "physical_model": "per-partition-and-interface-maxima-upper-bound",
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
