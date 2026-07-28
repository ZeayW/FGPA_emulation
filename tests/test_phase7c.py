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
    runtime_controller_testbench,
    runtime_timing_xdc,
    validate_physical_summary,
    validate_virtual_runtime,
    virtual_runtime_controller_to_systemverilog,
)
from emuflow.tdm import TDM_SCHEDULE_SCHEMA


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
                    "arrival_slot": 6,
                }
            ],
            "metrics": {
                "frame_slots": 32,
                "completion_slot": 6,
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
        )
        self.assertEqual(qor["status"], "pass")
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
        with self.assertRaisesRegex(ValidationError, "optimization_cells"):
            validate_physical_summary(broken, runtime, self.platform)
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

    def test_phase7c_writes_generated_then_physically_closed_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = {}
            for name, value in {
                "schedule": self.schedule,
                **self.reports,
                "platform": self.platform.to_dict(),
                "physical": self._physical_summary(),
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
            closed = run_phase7c(
                paths["schedule"],
                paths["platform"],
                paths["phase3"],
                paths["phase4"],
                paths["phase5"],
                paths["phase6"],
                root / "closed",
                physical_summary_path=paths["physical"],
            )
            self.assertEqual(closed["status"], "pass")
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
