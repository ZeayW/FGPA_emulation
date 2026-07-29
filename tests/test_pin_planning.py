import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.pin_planning import (
    PIN_PLAN_PROVIDER,
    SIGNAL_POSITION_HINTS_SCHEMA,
    build_pin_plan,
    validate_pin_plan,
)
from emuflow.platform import Platform


ROOT = Path(__file__).resolve().parents[1]


class PlacementAwarePinPlanningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.executable = (
            Path(cls.temporary_directory.name) / "emuflow_pin_planner"
        )
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                "-O2",
                str(
                    ROOT
                    / "src/native/placement_aware_pin_planner.cpp"
                ),
                "-o",
                str(cls.executable),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.platform = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {"name": "pin_fixture", "kind": "virtual"},
                "fpgas": [
                    {
                        "id": fpga,
                        "part": "test",
                        "utilization_limit": 1.0,
                        "capacity": {"lut": 100, "ff": 100},
                    }
                    for fpga in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link0",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "abstract",
                        "data_lanes_per_direction": 2,
                        "fabric_clock_mhz": 250,
                        "latency_cycles": 1,
                    }
                ],
            }
        )
        self.schedule = {
            "design": "pin_fixture",
            "platform": "pin_fixture",
            "entries": [
                {
                    "id": f"entry{index}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 3,
                    "slot": index % 3,
                    "lane": index // 3,
                }
                for index in range(6)
            ],
        }
        self.positions = {
            "schema": SIGNAL_POSITION_HINTS_SCHEMA,
            "design": "pin_fixture",
            "platform": "pin_fixture",
            "provider": "openparf-lookahead-centroid-v1",
            "region_count": 3,
            "metrics": {
                "signals": 6,
                "endpoint_centroid_fallbacks": 0,
            },
            "entries": [
                {
                    "schedule_entry": f"entry{index}",
                    "source_y": 0.1 if index < 3 else 0.9,
                    "sink_y": 0.15 if index < 3 else 0.85,
                    "source_region": 0 if index < 3 else 2,
                    "sink_region": 0 if index < 3 else 2,
                    "source_fallback": False,
                    "sink_fallback": False,
                }
                for index in range(6)
            ],
        }

    def test_grouping_and_exact_pin_assignment_are_reproducible(self) -> None:
        first = build_pin_plan(
            self.schedule,
            self.platform,
            self.positions,
            executable=str(self.executable),
            refinement_iterations=20,
        )
        second = build_pin_plan(
            self.schedule,
            self.platform,
            self.positions,
            executable=str(self.executable),
            refinement_iterations=20,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["provider"], PIN_PLAN_PROVIDER)
        self.assertEqual(first["metrics"]["groups"], 2)
        validation = validate_pin_plan(
            self.schedule, self.platform, self.positions, first
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(
            validation["physical_lane_slot_collisions"], 0
        )
        low_pins = {
            item["physical_lane"]
            for item in first["entries"]
            if int(item["schedule_entry"][5:]) < 3
        }
        high_pins = {
            item["physical_lane"]
            for item in first["entries"]
            if int(item["schedule_entry"][5:]) >= 3
        }
        self.assertEqual(low_pins, {0})
        self.assertEqual(high_pins, {1})

    def test_lane_slot_collision_is_rejected(self) -> None:
        plan = build_pin_plan(
            self.schedule,
            self.platform,
            self.positions,
            executable=str(self.executable),
        )
        broken = copy.deepcopy(plan)
        by_id = {
            item["schedule_entry"]: item for item in broken["entries"]
        }
        by_id["entry3"]["physical_lane"] = by_id["entry0"][
            "physical_lane"
        ]
        with self.assertRaisesRegex(ValidationError, "collision"):
            validate_pin_plan(
                self.schedule, self.platform, self.positions, broken
            )

    def test_minimum_legal_group_count_exceeding_lanes_is_rejected(
        self,
    ) -> None:
        constrained = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "pin_fixture",
                    "kind": "virtual",
                },
                "fpgas": [
                    {
                        "id": fpga,
                        "part": "test",
                        "utilization_limit": 1.0,
                        "capacity": {"lut": 100, "ff": 100},
                    }
                    for fpga in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link0",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "abstract",
                        "data_lanes_per_direction": 1,
                        "fabric_clock_mhz": 250,
                        "latency_cycles": 1,
                    }
                ],
            }
        )
        with self.assertRaisesRegex(
            EmuFlowError, "minimum legal TDM grouping"
        ):
            build_pin_plan(
                self.schedule,
                constrained,
                self.positions,
                executable=str(self.executable),
            )


if __name__ == "__main__":
    unittest.main()
