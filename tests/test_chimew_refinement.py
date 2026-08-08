import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.chimew_grouping import (
    CHIMEW_CROSSING_PROVIDER,
    CHIMEW_CROSSING_SCHEMA,
    build_chimew_initial_groups,
)
from emuflow.chimew_refinement import (
    CHIMEW_POSITION_PROVIDER,
    CHIMEW_POSITION_SCHEMA,
    CHIMEW_REFINEMENT_PROVIDER,
    refine_chimew_groups,
    validate_chimew_positions,
)
from emuflow.errors import ValidationError


ROOT = Path(__file__).resolve().parents[1]


class ChimewRefinementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.grouper = Path(cls.temporary_directory.name) / "chimew-grouper"
        cls.refiner = Path(cls.temporary_directory.name) / "chimew-refiner"
        for source, executable in (
            ("chimew_signal_grouper.cpp", cls.grouper),
            ("chimew_position_refiner.cpp", cls.refiner),
        ):
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    str(ROOT / "src/native" / source),
                    "-o",
                    str(executable),
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.schedule = {
            "design": "refinement_fixture",
            "platform": "two_fpga",
            "entries": [
                {
                    "id": f"s{index}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 3,
                }
                for index in range(8)
            ],
        }
        encodings = [2, 2, 2, 2, 1, 1, 1, 1]
        self.crossings = {
            "schema": CHIMEW_CROSSING_SCHEMA,
            "design": "refinement_fixture",
            "platform": "two_fpga",
            "provider": CHIMEW_CROSSING_PROVIDER,
            "slls_per_fpga": 2,
            "provenance": {
                "producer": "fixture-physical-router",
                "producer_version": "1",
                "routing_sha256": "a" * 64,
            },
            "metrics": {"signals": 8, "physical_sll_crossings": 8},
            "entries": [
                {
                    "schedule_entry": f"s{index}",
                    "source_slls": [1] if encoding == 2 else [0],
                    "sink_slls": [],
                    "encoding": encoding,
                }
                for index, encoding in enumerate(encodings)
            ],
        }
        source_y = [100.0, 101.0, 0.0, 1.0, 0.0, 1.0, 100.0, 101.0]
        self.positions = {
            "schema": CHIMEW_POSITION_SCHEMA,
            "design": "refinement_fixture",
            "platform": "two_fpga",
            "provider": CHIMEW_POSITION_PROVIDER,
            "coordinate_system": "physical-site-y",
            "provenance": {
                "producer": "fixture-lookahead-placer",
                "producer_version": "1",
                "placement_sha256": "b" * 64,
            },
            "metrics": {"signals": 8},
            "entries": [
                {"schedule_entry": f"s{index}", "source_y": value}
                for index, value in enumerate(source_y)
            ],
        }
        self.initial = build_chimew_initial_groups(
            self.schedule, self.crossings, executable=str(self.grouper)
        )

    def test_native_refinement_matches_replay_and_preserves_crossings(self) -> None:
        result = refine_chimew_groups(
            self.schedule,
            self.crossings,
            self.initial,
            self.positions,
            executable=str(self.refiner),
        )
        self.assertEqual(result["provider"], CHIMEW_REFINEMENT_PROVIDER)
        self.assertEqual(result["status"], "standalone_paper_bounded_inference")
        self.assertEqual(result["integration_status"], "not-a-phase6-pin-plan")
        self.assertGreater(result["metrics"]["moved_signals"], 0)
        self.assertLessEqual(
            result["metrics"]["pairwise_source_y_after"],
            result["metrics"]["pairwise_source_y_before"],
        )
        self.assertEqual(
            result["metrics"]["group_physical_sll_crossings"],
            self.initial["metrics"]["group_physical_sll_crossings"],
        )
        self.assertEqual(result["metrics"]["oracle_disagreements"], 0)

    def test_normalized_position_substitute_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.positions)
        invalid["coordinate_system"] = "normalized-y"
        with self.assertRaisesRegex(ValidationError, "normalized"):
            validate_chimew_positions(self.schedule, invalid)

    def test_position_provenance_and_exact_coverage_are_checked(self) -> None:
        invalid_digest = copy.deepcopy(self.positions)
        invalid_digest["provenance"]["placement_sha256"] = "opaque"
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            validate_chimew_positions(self.schedule, invalid_digest)
        missing = copy.deepcopy(self.positions)
        missing["entries"].pop()
        missing["metrics"]["signals"] -= 1
        with self.assertRaisesRegex(ValidationError, "cover"):
            validate_chimew_positions(self.schedule, missing)

    def test_tampered_initial_group_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.initial)
        invalid["entries"][0]["encoding"] ^= 1
        with self.assertRaisesRegex(ValidationError, "encoding"):
            refine_chimew_groups(
                self.schedule,
                self.crossings,
                invalid,
                self.positions,
                executable=str(self.refiner),
            )


if __name__ == "__main__":
    unittest.main()
