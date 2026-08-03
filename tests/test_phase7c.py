import copy
import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.phase7c import run_phase7c
from emuflow.platform import Platform
from emuflow.runtime import (
    PHYSICAL_SUMMARY_SCHEMA,
    aggregate_qor,
    build_virtual_runtime,
    estimate_runtime_timing,
    runtime_controller_testbench,
    runtime_timing_xdc,
    validate_physical_summary,
    validate_virtual_runtime,
    virtual_runtime_controller_to_systemverilog,
)
from emuflow.tdm import TDM_SCHEDULE_SCHEMA
from emuflow.tdm import reconstruct_tdm_schedule_timing


class Phase7CTest(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "runtime_test",
                    "kind": "virtual",
                    "description": "runtime test",
                },
                "fpgas": [
                    {
                        "id": fpga,
                        "part": "xcvu3p-ffvc1517-2-e",
                        "utilization_limit": 0.75,
                        "capacity": {"lut": 100, "ff": 100},
                    }
                    for fpga in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "abstract",
                        "data_lanes_per_direction": 4,
                        "fabric_clock_mhz": 250.0,
                        "latency_cycles": 2,
                    }
                ],
            }
        )
        self.schedule = {
            "schema": TDM_SCHEDULE_SCHEMA,
            "design": "dut",
            "platform": self.platform.name,
            "entries": [
                {
                    "id": "s000000",
                    "demand": "d000000",
                    "net": "n0",
                    "transport_round": 0,
                    "hop": 0,
                    "link": "link",
                    "from": "fpga0",
                    "to": "fpga1",
                    "lane": 0,
                    "ready_slot": 4,
                    "slot": 4,
                    "arrival_slot": 6,
                }
            ],
            "metrics": {
                "frame_slots": 32,
                "completion_slot": 6,
            },
        }
        self.routes = {
            "schema": "emuflow.system-routes/v1",
            "design": "dut",
            "platform": self.platform.name,
            "constraints": {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 32,
            },
            "routes": [
                {
                    "id": "d000000",
                    "net": "n0",
                    "source": "fpga0",
                    "sinks": ["fpga1"],
                    "tree_edges": [
                        {
                            "link": "link",
                            "from": "fpga0",
                            "to": "fpga1",
                        }
                    ],
                }
            ],
            "timing": {
                "schema": "emuflow.sta-paths/v1",
                "normalization": {
                    "positive_slack_scale_ns": 20.0,
                    "negative_slack_scale_ns": 20.0,
                    "max_clock_period_ns": 20.0,
                },
                "paths": [
                    {
                        "path": "system-critical",
                        "clock_domain": "clk",
                        "clock_period_ns": 20.0,
                        "fixed_delay_ns": 2.0,
                        "cut_nets": ["n0"],
                    }
                ],
            },
        }
        self.reports = {
            "phase3": {
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "validation": {
                    "instances": 100,
                    "used_fpgas": 2,
                    "cut_nets": 4,
                    "cut_sink_endpoints": 4,
                },
            },
            "phase4": {
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "validation": {
                    "demands": 4,
                    "routed_sinks": 4,
                    "total_link_bit_hops": 4,
                    "max_link_utilization": 0.015625,
                },
            },
            "phase5": {
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "validation": {
                    "scheduled_bit_hops": 4,
                    "frame_slots": 32,
                    "completion_slot": 6,
                    "max_domain_utilization": 0.015625,
                    "collisions": 0,
                },
            },
            "phase6": {
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "equivalence": {
                    "cycles": 64,
                    "compared_state_bits": 6400,
                    "compared_output_bits": 128,
                    "mismatches": 0,
                    "trace_sha256": "0" * 64,
                },
            },
        }
        self.reports["phase5"]["timing_validation"] = (
            reconstruct_tdm_schedule_timing(
                self.routes, self.platform, self.schedule
            )
        )

    def _physical_summary(self):
        return {
            "schema": PHYSICAL_SUMMARY_SCHEMA,
            "status": "pass",
            "design": "dut",
            "platform": self.platform.name,
            "fpgas": [
                {
                    "fpga": "fpga0",
                    "original_cells": 80,
                    "transport_cells": 12,
                    "routed_cells": 92,
                    "physical_cells": 93,
                    "infrastructure_cells": 1,
                    "unrouted_nets": 0,
                    "drc_violations": 0,
                    "wns_ns": 1.25,
                    "timing": {
                        "dut_wns_ns": 120.0,
                        "fabric_wns_ns": 1.25,
                        "fabric_to_dut_wns_ns": 98.0,
                    },
                    "clock_domain_delays_ns": {
                        "dut": 8.0,
                        "fabric": 2.75,
                        "cross": 2.0,
                        "overall": 8.0,
                    },
                    "clocks": {
                        "fabric_period_ns": 4.0,
                        "dut_period_ns": 128.0,
                    },
                },
                {
                    "fpga": "fpga1",
                    "original_cells": 20,
                    "transport_cells": 8,
                    "routed_cells": 28,
                    "physical_cells": 28,
                    "infrastructure_cells": 0,
                    "unrouted_nets": 0,
                    "drc_violations": 0,
                    "wns_ns": 0.75,
                    "timing": {
                        "dut_wns_ns": 121.0,
                        "fabric_wns_ns": 0.75,
                        "fabric_to_dut_wns_ns": 98.5,
                    },
                    "clock_domain_delays_ns": {
                        "dut": 7.0,
                        "fabric": 3.25,
                        "cross": 1.5,
                        "overall": 7.0,
                    },
                    "clocks": {
                        "fabric_period_ns": 4.0,
                        "dut_period_ns": 128.0,
                    },
                },
            ],
        }

    def test_runtime_contract_has_barrier_margin_and_nominal_rate(self):
        runtime = build_virtual_runtime(self.schedule, self.platform)
        validation = validate_virtual_runtime(
            runtime, self.schedule, self.platform
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["shadow_settle_slots"], 25)
        self.assertEqual(validation["shadow_settle_ns"], 100.0)
        self.assertEqual(
            validation["nominal_virtual_frequency_mhz"], 7.8125
        )
        self.assertEqual(
            runtime["barrier"]["stall_behavior"],
            "hold-slot-and-suppress-dut-clock-enable",
        )

    def test_runtime_timing_uses_virtual_not_original_period(self):
        runtime = build_virtual_runtime(self.schedule, self.platform)
        report = copy.deepcopy(self.reports["phase5"])
        report["timing_validation"] = {
            "status": "pass",
            "worst_path": "critical",
            "worst_delay_ns": 80.0,
            "worst_slack_ns": -70.0,
            "negative_slack_paths": 1,
        }
        timing = estimate_runtime_timing(runtime, report)
        self.assertEqual(timing["status"], "pass")
        self.assertFalse(
            timing["original_clock_reference"]["closure_gate"]
        )
        self.assertEqual(
            timing["virtual_clock"]["estimated_worst_slack_ns"], 48.0
        )
        report["timing_validation"]["worst_delay_ns"] = 129.0
        self.assertEqual(
            estimate_runtime_timing(runtime, report)["status"], "fail"
        )

    def test_controller_and_timing_artifacts_encode_contract(self):
        runtime = build_virtual_runtime(self.schedule, self.platform)
        rtl = virtual_runtime_controller_to_systemverilog()
        tb = runtime_controller_testbench(
            runtime, ["fpga0", "fpga1"], frames=12
        )
        xdc = runtime_timing_xdc(runtime)
        self.assertIn("if (links_ready)", rtl)
        self.assertIn("controller 1 lost lockstep", tb)
        self.assertIn("stalled_cycles != 3", tb)
        self.assertIn("-period 4.000000000", xdc)
        self.assertIn("100.000000000", xdc)
        self.assertIn("requires-hardware-bsp", xdc)

    def test_physical_summary_and_qor_are_strictly_checked(self):
        runtime = build_virtual_runtime(self.schedule, self.platform)
        physical = self._physical_summary()
        result = validate_physical_summary(
            physical, runtime, self.platform
        )
        self.assertEqual(result["routed_cells"], 120)
        self.assertEqual(result["physical_cells"], 121)
        self.assertEqual(result["infrastructure_cells"], 1)
        self.assertEqual(result["optimization_cells"], 0)
        self.assertEqual(result["transport_cells"], 20)
        self.assertEqual(result["worst_wns_ns"], 0.75)
        self.assertEqual(result["worst_dut_wns_ns"], 120.0)
        self.assertEqual(result["worst_fabric_wns_ns"], 0.75)
        self.assertEqual(
            result["worst_fabric_to_dut_wns_ns"], 98.0
        )
        qor = aggregate_qor(
            runtime,
            self.reports["phase3"],
            self.reports["phase4"],
            self.reports["phase5"],
            self.reports["phase6"],
            physical,
            self.platform,
            routes=self.routes,
            schedule=self.schedule,
        )
        self.assertEqual(qor["status"], "pass")
        self.assertLess(
            qor["timing"]["target_clock"]["worst_slack_bound_ns"], 0.0
        )
        self.assertGreater(
            qor["timing"]["runtime_clock"]["worst_slack_bound_ns"], 0.0
        )
        self.assertEqual(
            qor["timing"]["qualification"],
            "conservative-partition-physical-maxima-plus-concrete-link-tdm",
        )
        broken = copy.deepcopy(physical)
        broken["fpgas"][0]["unrouted_nets"] = 1
        with self.assertRaisesRegex(ValidationError, "route/DRC"):
            validate_physical_summary(broken, runtime, self.platform)
        broken = copy.deepcopy(physical)
        broken["fpgas"][0]["physical_cells"] += 1
        with self.assertRaisesRegex(ValidationError, "physical cell"):
            validate_physical_summary(broken, runtime, self.platform)
        broken = copy.deepcopy(physical)
        broken["fpgas"][0]["optimization_cells"] = 2
        with self.assertRaisesRegex(ValidationError, "physical cell"):
            validate_physical_summary(broken, runtime, self.platform)
        expanded = copy.deepcopy(physical)
        expanded["fpgas"][0]["optimization_cells"] = 2
        expanded["fpgas"][0]["physical_cells"] += 2
        self.assertEqual(
            validate_physical_summary(
                expanded, runtime, self.platform
            )["optimization_cells"],
            2,
        )
        conservative = copy.deepcopy(physical)
        conservative["fpgas"][0]["clocks"]["dut_period_ns"] = 64.0
        self.assertEqual(
            validate_physical_summary(
                conservative, runtime, self.platform
            )["status"],
            "pass",
        )
        slower = copy.deepcopy(physical)
        slower["fpgas"][0]["clocks"]["dut_period_ns"] = 129.0
        with self.assertRaisesRegex(ValidationError, "slower"):
            validate_physical_summary(slower, runtime, self.platform)

    def test_qor_prefers_endpoint_exact_physical_interface_timing(self):
        physical = self._physical_summary()
        identities = {}
        timings = {}
        for fpga, endpoint_id, kind, delay in (
            ("fpga0", "__emuflow_tx_s000000", "tx", 0.4),
            ("fpga1", "__emuflow_rx_s000000", "rx", 0.6),
        ):
            identities[fpga] = {
                "schema": "emuflow.boundary-identity/v1",
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "fpga": fpga,
                "provider": "test",
                "coverage": {
                    "endpoints": 1,
                    "tx": int(kind == "tx"),
                    "rx": int(kind == "rx"),
                    "external_port_nets": 1,
                },
                "endpoints": [
                    {
                        "id": endpoint_id,
                        "kind": kind,
                        "schedule_entry": "s000000",
                    }
                ],
            }
            timings[fpga] = {
                "schema": "emuflow.boundary-timing/v1",
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "fpga": fpga,
                "provider": "test",
                "qualification": "endpoint-exact",
                "coverage": {
                    "endpoints": 1,
                    "tx": int(kind == "tx"),
                    "rx": int(kind == "rx"),
                },
                "endpoints": [
                    {
                        "id": endpoint_id,
                        "kind": kind,
                        "schedule_entry": "s000000",
                        "delay_ns": delay,
                        "start_object": "start",
                        "end_object": "end",
                        "measurement": (
                            "logical-source-to-tx-port"
                            if kind == "tx"
                            else "rx-port-to-shadow-capture"
                        ),
                    }
                ],
            }
        physical["boundary_identities"] = identities
        physical["boundary_timing"] = timings
        runtime = build_virtual_runtime(self.schedule, self.platform)
        qor = aggregate_qor(
            runtime,
            self.reports["phase3"],
            self.reports["phase4"],
            self.reports["phase5"],
            self.reports["phase6"],
            physical,
            self.platform,
            routes=self.routes,
            schedule=self.schedule,
        )
        self.assertTrue(
            qor["timing"]["path_exactness"]["physical_boundary_endpoints"]
        )
        self.assertEqual(
            qor["timing"]["paths"][0][
                "physical_interface_delay_bound_ns"
            ],
            1.0,
        )

    def test_qor_replaces_partition_maxima_with_exact_logic_segments(self):
        physical = self._physical_summary()
        identities = {}
        timings = {}
        for fpga, endpoint_id, kind, delay in (
            ("fpga0", "__emuflow_tx_s000000", "tx", 0.4),
            ("fpga1", "__emuflow_rx_s000000", "rx", 0.6),
        ):
            identities[fpga] = {
                "schema": "emuflow.boundary-identity/v1",
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "fpga": fpga,
                "provider": "test",
                "coverage": {
                    "endpoints": 1,
                    "tx": int(kind == "tx"),
                    "rx": int(kind == "rx"),
                    "external_port_nets": 1,
                },
                "endpoints": [
                    {
                        "id": endpoint_id,
                        "kind": kind,
                        "schedule_entry": "s000000",
                    }
                ],
            }
            timings[fpga] = {
                "schema": "emuflow.boundary-timing/v1",
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "fpga": fpga,
                "provider": "test",
                "qualification": "endpoint-exact",
                "coverage": {
                    "endpoints": 1,
                    "tx": int(kind == "tx"),
                    "rx": int(kind == "rx"),
                },
                "endpoints": [
                    {
                        "id": endpoint_id,
                        "kind": kind,
                        "schedule_entry": "s000000",
                        "delay_ns": delay,
                        "start_object": "start",
                        "end_object": "end",
                        "measurement": (
                            "logical-source-to-tx-port"
                            if kind == "tx"
                            else "rx-port-to-shadow-capture"
                        ),
                    }
                ],
            }
        physical["boundary_identities"] = identities
        physical["boundary_timing"] = timings
        physical["logic_segment_timing"] = {}
        for fpga, role, replacement, delay in (
            (
                "fpga0",
                "launch",
                "__emuflow_tx_s000000",
                2.0,
            ),
            ("fpga1", "capture", None, 3.0),
        ):
            segment = {
                "id": f"logic-{role}",
                "kind": role,
                "system_path": "system-critical",
                "member_path": "system-critical",
                "cut_index": 0 if role == "launch" else 1,
                "fpga": fpga,
                "replace_tx_endpoint": replacement,
                "start_pin": "start",
                "end_pin": "end",
                "delay_ns": delay,
            }
            physical["logic_segment_timing"][fpga] = {
                "schema": "emuflow.logic-segment-timing/v1",
                "status": "pass",
                "design": "dut",
                "platform": self.platform.name,
                "fpga": fpga,
                "provider": "test",
                "qualification": "endpoint-chain",
                "coverage": {
                    "segments": 1,
                    "system_paths": 1,
                    "member_paths": 1,
                    "unsupported_member_paths": 0,
                },
                "unsupported_member_paths": [],
                "segments": [segment],
            }
        runtime = build_virtual_runtime(self.schedule, self.platform)
        qor = aggregate_qor(
            runtime,
            self.reports["phase3"],
            self.reports["phase4"],
            self.reports["phase5"],
            self.reports["phase6"],
            physical,
            self.platform,
            routes=self.routes,
            schedule=self.schedule,
        )
        timing = qor["timing"]
        self.assertTrue(timing["path_exactness"]["physical_logic_segments"])
        self.assertEqual(
            timing["qualification"],
            "endpoint-chain-physical-plus-concrete-link-tdm",
        )
        self.assertAlmostEqual(
            timing["paths"][0]["physical_logic_delay_bound_ns"],
            4.6,
        )
        self.assertAlmostEqual(
            timing["paths"][0]["physical_interface_delay_bound_ns"],
            1.0,
        )

    def test_phase7c_writes_generated_then_physically_closed_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = {}
            for name, value in {
                "schedule": self.schedule,
                **self.reports,
                "platform": self.platform.to_dict(),
                "physical": self._physical_summary(),
                "routes": self.routes,
            }.items():
                paths[name] = root / f"{name}.json"
                paths[name].write_text(json.dumps(value), encoding="utf-8")
            generated = run_phase7c(
                paths["schedule"],
                paths["platform"],
                paths["phase3"],
                paths["phase4"],
                paths["phase5"],
                paths["phase6"],
                root / "generated",
            )
            self.assertEqual(generated["status"], "generated")
            self.assertIn("runtime_timing", generated)
            self.assertNotIn("system_timing", generated)
            closed = run_phase7c(
                paths["schedule"],
                paths["platform"],
                paths["phase3"],
                paths["phase4"],
                paths["phase5"],
                paths["phase6"],
                root / "closed",
                physical_summary_path=paths["physical"],
                routes_path=paths["routes"],
            )
            self.assertEqual(closed["status"], "pass")
            self.assertIn("system_timing", closed)
            for filename in closed["artifacts"].values():
                self.assertTrue((root / "closed" / filename).is_file())

    def test_heterogeneous_fabric_clocks_are_outside_v1(self):
        value = self.platform.to_dict()
        value["links"].append(
            {
                "id": "second",
                "endpoints": ["fpga0", "fpga1"],
                "direction": "full_duplex",
                "mode": "abstract",
                "data_lanes_per_direction": 1,
                "fabric_clock_mhz": 200.0,
                "latency_cycles": 1,
            }
        )
        platform = Platform.from_dict(value)
        with self.assertRaisesRegex(ValidationError, "common fabric clock"):
            build_virtual_runtime(self.schedule, platform)


if __name__ == "__main__":
    unittest.main()
