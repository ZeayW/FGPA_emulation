import copy
import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.io import read_json
from emuflow.ir import EmuIR
from emuflow.partition import (
    assign_clusters,
    build_clusters,
    normalize_partition_constraints,
    validate_partition_artifacts,
)
from emuflow.phase3 import run_phase3
from emuflow.platform import Platform
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


class Phase3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        self.platform = Platform.load(PLATFORM_PATH)

    def _artifacts(self):
        constraints = normalize_partition_constraints(
            None,
            self.ir,
            self.platform,
        )
        clusters = build_clusters(self.ir, constraints)
        assignment = assign_clusters(
            self.ir,
            self.platform,
            clusters,
            constraints,
            seed=7,
        )
        return constraints, clusters, assignment

    def test_pipeline_writes_valid_forced_two_fpga_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            ir_path = output / "design.emuir.json"
            ir_path.write_text(
                json.dumps(self.ir.to_dict()), encoding="utf-8"
            )
            report = run_phase3(
                ir_path=ir_path,
                platform_path=PLATFORM_PATH,
                output_dir=output / "phase3",
                seed=11,
                provider="greedy",
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["validation"]["instances"], 8)
            self.assertEqual(report["validation"]["used_fpgas"], 2)
            self.assertEqual(report["validation"]["illegal_cuts"], 0)
            self.assertGreater(report["validation"]["cut_nets"], 0)
            for filename in (
                "clusters.json",
                "constraints.normalized.json",
                "assignment.json",
                "phase3_report.json",
            ):
                self.assertTrue((output / "phase3" / filename).is_file())

    def test_assignment_is_reproducible_for_fixed_seed(self) -> None:
        constraints, clusters, first = self._artifacts()
        second = assign_clusters(
            self.ir,
            self.platform,
            clusters,
            constraints,
            seed=7,
        )
        self.assertEqual(first, second)

    def test_group_and_fixed_constraints_hold(self) -> None:
        constraints = normalize_partition_constraints(
            {
                "schema": "emuflow.partition-constraints/v1",
                "groups": [
                    {
                        "id": "low_bits",
                        "patterns": ["q_reg[[]0]", "q_reg[[]1]"],
                    }
                ],
                "fixed": [
                    {
                        "fpga": "fpga1",
                        "patterns": ["q_reg[[]0]"],
                    }
                ],
                "min_used_fpgas": 2,
                "balance_tolerance": 0.25,
            },
            self.ir,
            self.platform,
        )
        clusters = build_clusters(self.ir, constraints)
        assignment = assign_clusters(
            self.ir,
            self.platform,
            clusters,
            constraints,
            seed=3,
        )
        validation = validate_partition_artifacts(
            self.ir,
            self.platform,
            clusters,
            assignment,
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(
            assignment["instance_assignment"]["q_reg[0]"], "fpga1"
        )
        self.assertEqual(
            assignment["instance_assignment"]["q_reg[1]"], "fpga1"
        )

    def test_missing_instance_is_rejected(self) -> None:
        _, clusters, assignment = self._artifacts()
        broken = copy.deepcopy(assignment)
        broken["instance_assignment"].pop("q_reg[0]")
        with self.assertRaisesRegex(ValidationError, "exact coverage failed"):
            validate_partition_artifacts(
                self.ir,
                self.platform,
                clusters,
                broken,
            )

    def test_cluster_split_is_rejected(self) -> None:
        _, clusters, assignment = self._artifacts()
        broken = copy.deepcopy(assignment)
        cluster = next(
            cluster
            for cluster in clusters["clusters"]
            if len(cluster["instances"]) > 1
        )
        instance_id = cluster["instances"][0]
        old_fpga = broken["instance_assignment"][instance_id]
        broken["instance_assignment"][instance_id] = (
            "fpga1" if old_fpga == "fpga0" else "fpga0"
        )
        with self.assertRaisesRegex(ValidationError, "spans FPGAs"):
            validate_partition_artifacts(
                self.ir,
                self.platform,
                clusters,
                broken,
            )

    def test_infeasible_capacity_is_rejected(self) -> None:
        platform_value = self.platform.to_dict()
        for fpga in platform_value["fpgas"]:
            fpga["utilization_limit"] = 1.0
            fpga["capacity"]["lut"] = 1
            fpga["capacity"]["ff"] = 1
        platform = Platform.from_dict(platform_value)
        constraints = normalize_partition_constraints(
            None,
            self.ir,
            platform,
        )
        clusters = build_clusters(self.ir, constraints)
        with self.assertRaisesRegex(ValidationError, "cannot fit any FPGA"):
            assign_clusters(
                self.ir,
                platform,
                clusters,
                constraints,
            )


if __name__ == "__main__":
    unittest.main()
