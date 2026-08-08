import copy
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.chimew_bank_channel import (
    CHIMEW_BANK_CHANNEL_INPUT_PROVIDER,
    CHIMEW_BANK_CHANNEL_INPUT_SCHEMA,
)
from emuflow.chimew_phase6 import (
    CHIMEW_ELECTRICAL_MAP_PROVIDER,
    CHIMEW_ELECTRICAL_MAP_SCHEMA,
    CHIMEW_PHASE6_BINDING_PROVIDER,
    build_chimew_phase6_pin_plan,
    run_chimew_phase6_adapter,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.pin_planning import CHIMEW_PIN_PLAN_PROVIDER, validate_pin_plan
from emuflow.platform import Platform


ROOT = Path(__file__).resolve().parents[1]


class ChimewPhase6AdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.executable = Path(cls.temporary_directory.name) / "chimew-assignment"
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O2",
                str(ROOT / "src/native/chimew_bank_channel_assigner.cpp"),
                "-o",
                str(cls.executable),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.platform_document = {
            "schema": "emuflow.boarddb/v1",
            "platform": {
                "name": "chimew_two_fpga",
                "kind": "hardware",
                "description": "electrical adapter fixture",
            },
            "fpgas": [
                {
                    "id": fpga,
                    "part": "fixture",
                    "utilization_limit": 1.0,
                    "capacity": {"lut": 100},
                }
                for fpga in ("A", "B")
            ],
            "links": [
                {
                    "id": "AB_link",
                    "endpoints": ["A", "B"],
                    "direction": "full_duplex",
                    "mode": "parallel",
                    "data_lanes_per_direction": 2,
                    "fabric_clock_mhz": 100.0,
                    "latency_cycles": 1,
                },
            ],
        }
        self.platform = Platform.from_dict(self.platform_document)
        self.schedule = {
            "schema": "emuflow.tdm-schedule/v1",
            "design": "chimew_fixture",
            "platform": self.platform.name,
            "metrics": {"frame_slots": 4},
            "entries": [
                {
                    "id": "s0",
                    "link": "AB_link",
                    "from": "A",
                    "to": "B",
                    "tdm_ratio": 4,
                    "lane": 0,
                    "slot": 0,
                },
                {
                    "id": "s1",
                    "link": "AB_link",
                    "from": "B",
                    "to": "A",
                    "tdm_ratio": 4,
                    "lane": 0,
                    "slot": 0,
                },
            ],
        }
        self.assignment_input = {
            "schema": CHIMEW_BANK_CHANNEL_INPUT_SCHEMA,
            "provider": CHIMEW_BANK_CHANNEL_INPUT_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.platform.name,
            "coordinate_system": "physical-site-xy",
            "cost_quantization_per_site": 1000,
            "provenance": {
                "producer": "fixture-lookahead",
                "producer_version": "1",
                "grouping_sha256": "a" * 64,
                "placement_sha256": "b" * 64,
                "architecture_sha256": "c" * 64,
            },
            "domains": [{"id": "AB", "fpga_a": "A", "fpga_b": "B"}],
            "bank_pairs": [
                {
                    "id": "bank0",
                    "domain": "AB",
                    "bank_a": {"id": "A0", "x": 0.0, "y": 0.0},
                    "bank_b": {"id": "B0", "x": 100.0, "y": 0.0},
                    "channels": [
                        {
                            "id": f"channel{lane}",
                            "order": lane,
                            "pin_a": {"x": 0.0, "y": float(lane * 50)},
                            "pin_b": {"x": 100.0, "y": float(lane * 50)},
                        }
                        for lane in range(2)
                    ],
                }
            ],
            "groups": [
                {
                    "id": "group_ab",
                    "domain": "AB",
                    "kind": "tdm_group",
                    "direction": "a_to_b",
                    "members": [
                        {
                            "id": "s0",
                            "fanout": {"x": 0.0, "y": 10.0},
                            "fanins": [{"x": 100.0, "y": 10.0}],
                        }
                    ],
                },
                {
                    "id": "group_ba",
                    "domain": "AB",
                    "kind": "tdm_group",
                    "direction": "b_to_a",
                    "members": [
                        {
                            "id": "s1",
                            "fanout": {"x": 100.0, "y": 50.0},
                            "fanins": [{"x": 0.0, "y": 50.0}],
                        }
                    ],
                },
            ],
            "metrics": {
                "groups": 2,
                "signals": 2,
                "fanins": 2,
                "bank_pairs": 1,
                "channels": 2,
            },
        }
        self.electrical_map = {
            "schema": CHIMEW_ELECTRICAL_MAP_SCHEMA,
            "provider": CHIMEW_ELECTRICAL_MAP_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.platform.name,
            "provenance": {
                "producer": "fixture-bsp",
                "producer_version": "1",
                "boarddb_sha256": "d" * 64,
                "package_pin_inventory_sha256": "e" * 64,
            },
            "fpga_y_bounds": [
                {"fpga": "A", "y_min": 0.0, "y_max": 100.0},
                {"fpga": "B", "y_min": 0.0, "y_max": 100.0},
            ],
            "channels": [
                {
                    "chimew_channel": f"channel{lane}",
                    "link": "AB_link",
                    "physical_lane": lane,
                    "bank_a": "A0",
                    "bank_b": "B0",
                    "package_pin_a": f"A{lane}",
                    "package_pin_b": f"B{lane}",
                    "iostandard": "LVCMOS18",
                    "supported_iostandards": ["LVCMOS18"],
                    "bank_voltage": 1.8,
                    "electrical_class": "single_ended_parallel",
                    "reserved": False,
                }
                for lane in range(2)
            ],
            "metrics": {"channels": 2, "package_pins": 4, "concrete_lanes": 2},
        }

    def test_certified_assignment_becomes_a_valid_phase6_pin_plan(self) -> None:
        result = build_chimew_phase6_pin_plan(
            self.schedule,
            self.platform,
            self.assignment_input,
            self.electrical_map,
            executable=str(self.executable),
            region_count=4,
        )
        repeated = build_chimew_phase6_pin_plan(
            self.schedule,
            self.platform,
            self.assignment_input,
            self.electrical_map,
            executable=str(self.executable),
            region_count=4,
        )
        self.assertEqual(result, repeated)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["provider"], CHIMEW_PHASE6_BINDING_PROVIDER)
        self.assertEqual(result["pin_plan"]["provider"], CHIMEW_PIN_PLAN_PROVIDER)
        self.assertEqual(
            result["electrical_binding"]["integration_status"],
            "phase6-pin-plan",
        )
        self.assertEqual(
            result["electrical_binding"]["metrics"]["package_pin_collisions"], 0
        )
        self.assertEqual(
            validate_pin_plan(
                self.schedule,
                self.platform,
                result["position_hints"],
                result["pin_plan"],
            )["status"],
            "pass",
        )
        plan_by_id = {
            entry["schedule_entry"]: entry for entry in result["pin_plan"]["entries"]
        }
        for schedule_entry in self.schedule["entries"]:
            self.assertEqual(
                plan_by_id[schedule_entry["id"]]["logical_lane"],
                schedule_entry["lane"],
            )

    def test_duplicate_concrete_lane_is_rejected(self) -> None:
        electrical_map = copy.deepcopy(self.electrical_map)
        electrical_map["channels"][1]["physical_lane"] = 0
        with self.assertRaisesRegex(ValidationError, "concrete lane"):
            build_chimew_phase6_pin_plan(
                self.schedule,
                self.platform,
                self.assignment_input,
                electrical_map,
                executable=str(self.executable),
            )

    def test_path_adapter_emits_phase6_consumable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "schedule": root / "schedule.json",
                "platform": root / "platform.json",
                "assignment": root / "assignment.json",
                "electrical": root / "electrical.json",
            }
            for key, document in (
                ("schedule", self.schedule),
                ("platform", self.platform_document),
                ("assignment", self.assignment_input),
            ):
                write_json(paths[key], document)
            electrical_map = copy.deepcopy(self.electrical_map)
            electrical_map["provenance"]["boarddb_sha256"] = hashlib.sha256(
                paths["platform"].read_bytes()
            ).hexdigest()
            write_json(paths["electrical"], electrical_map)
            report = run_chimew_phase6_adapter(
                paths["schedule"],
                paths["platform"],
                paths["assignment"],
                paths["electrical"],
                root / "out",
                executable=str(self.executable),
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                read_json(root / "out" / "adapter_report.json"), report
            )
            for name in report["artifacts"].values():
                self.assertTrue((root / "out" / name).is_file())

    def test_out_of_bounds_physical_placement_is_rejected(self) -> None:
        assignment = copy.deepcopy(self.assignment_input)
        assignment["groups"][0]["members"][0]["fanout"]["y"] = 101.0
        with self.assertRaisesRegex(ValidationError, "outside FPGA bounds"):
            build_chimew_phase6_pin_plan(
                self.schedule,
                self.platform,
                assignment,
                self.electrical_map,
                executable=str(self.executable),
            )

    def test_bank_voltage_must_match_selected_iostandard(self) -> None:
        electrical_map = copy.deepcopy(self.electrical_map)
        electrical_map["channels"][0]["bank_voltage"] = 2.5
        with self.assertRaisesRegex(ValidationError, "voltage/IOSTANDARD"):
            build_chimew_phase6_pin_plan(
                self.schedule,
                self.platform,
                self.assignment_input,
                electrical_map,
                executable=str(self.executable),
            )


if __name__ == "__main__":
    unittest.main()
