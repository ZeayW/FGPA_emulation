import copy
import json
import tempfile
import unittest
from pathlib import Path

from emuflow.equivalence import simulate_partition_equivalence
from emuflow.errors import ValidationError
from emuflow.netlist import (
    build_split_artifacts,
    validate_split_artifacts,
)
from emuflow.partition import (
    assign_clusters,
    build_clusters,
    normalize_partition_constraints,
)
from emuflow.phase6 import run_phase6, validate_phase6
from emuflow.platform import Platform
from emuflow.routing import normalize_route_constraints, route_system
from emuflow.tdm import build_tdm_schedule
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


class Phase6Test(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        self.platform = Platform.load(PLATFORM_PATH)
        constraints = normalize_partition_constraints(
            None, self.ir, self.platform
        )
        clusters = build_clusters(self.ir, constraints)
        self.assignment = assign_clusters(
            self.ir,
            self.platform,
            clusters,
            constraints,
            seed=20260727,
        )
        route_constraints = normalize_route_constraints(
            None, self.platform, frame_slots=32
        )
        self.routes = route_system(
            self.assignment, self.platform, route_constraints
        )
        self.schedule = build_tdm_schedule(self.routes, self.platform)

    def test_split_exact_coverage_lane_agreement_and_equivalence(self) -> None:
        artifacts = build_split_artifacts(
            self.ir, self.assignment, self.schedule, self.platform
        )
        validation = validate_split_artifacts(
            self.ir,
            self.assignment,
            self.schedule,
            self.platform,
            artifacts,
        )
        equivalence = simulate_partition_equivalence(
            self.ir, self.assignment, self.schedule, cycles=12
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["instances"], 8)
        self.assertEqual(
            validation["transport_endpoints"],
            2 * validation["scheduled_hops"],
        )
        self.assertEqual(equivalence["status"], "pass")
        self.assertEqual(equivalence["mismatches"], 0)
        self.assertEqual(equivalence["cycles"], 12)

    def test_phase6_writes_and_independently_reloads_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "ir.json"
            assignment_path = root / "assignment.json"
            schedule_path = root / "schedule.json"
            ir_path.write_text(json.dumps(self.ir.to_dict()), encoding="utf-8")
            assignment_path.write_text(
                json.dumps(self.assignment), encoding="utf-8"
            )
            schedule_path.write_text(
                json.dumps(self.schedule), encoding="utf-8"
            )
            output = root / "phase6"
            report = run_phase6(
                ir_path,
                assignment_path,
                schedule_path,
                PLATFORM_PATH,
                output,
                equivalence_cycles=8,
            )
            self.assertEqual(report["status"], "pass")
            validation = validate_phase6(
                ir_path,
                assignment_path,
                schedule_path,
                PLATFORM_PATH,
                output / "manifest.json",
            )
            self.assertEqual(validation["status"], "pass")
            for filename in (
                "manifest.json",
                "lane_map.json",
                "phase6_report.json",
                "virtual_runtime_controller.sv",
                "fpga0/netlist.json",
                "fpga0/transport.json",
                "fpga0/transport_schedule.sv",
                "fpga0/virtual_anchors.json",
                "fpga0/virtual_anchors.xdc.template",
                "fpga1/netlist.json",
                "fpga1/transport.json",
                "fpga1/transport_schedule.sv",
                "fpga1/virtual_anchors.json",
                "fpga1/virtual_anchors.xdc.template",
            ):
                self.assertTrue((output / filename).is_file(), filename)

    def test_lane_map_corruption_is_rejected(self) -> None:
        artifacts = build_split_artifacts(
            self.ir, self.assignment, self.schedule, self.platform
        )
        broken = copy.deepcopy(artifacts)
        broken["lane_map"]["entries"][0]["lane"] += 1
        with self.assertRaisesRegex(ValidationError, "lane_map"):
            validate_split_artifacts(
                self.ir,
                self.assignment,
                self.schedule,
                self.platform,
                broken,
            )

    def test_yosys_constant_connections_are_retained(self) -> None:
        constants = [
            item
            for instance in self.ir.value["instances"]
            for item in instance["constant_connections"]
        ]
        self.assertTrue(constants)
        self.assertTrue(
            all(item["value"] in {"0", "1", "x", "z"} for item in constants)
        )


if __name__ == "__main__":
    unittest.main()
