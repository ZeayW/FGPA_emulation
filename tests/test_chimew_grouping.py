import copy
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.chimew_grouping import (
    CHIMEW_CROSSING_PROVIDER,
    CHIMEW_CROSSING_SCHEMA,
    CHIMEW_GROUPING_PROVIDER,
    build_chimew_initial_groups,
    validate_chimew_crossings,
)
from emuflow.errors import ValidationError


ROOT = Path(__file__).resolve().parents[1]


class ChimewGroupingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.executable = Path(cls.temporary_directory.name) / "chimew-grouper"
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O2",
                str(ROOT / "src/native/chimew_signal_grouper.cpp"),
                "-o",
                str(cls.executable),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.schedule = {
            "design": "chimew_fixture",
            "platform": "two_fpga",
            "entries": [
                {
                    "id": f"s{index}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 3,
                }
                for index in range(5)
            ],
        }
        crossings = [
            ([0], [1], 9),
            ([0], [], 1),
            ([], [1], 8),
            ([1], [0], 6),
            ([1], [], 2),
        ]
        self.crossings = {
            "schema": CHIMEW_CROSSING_SCHEMA,
            "design": "chimew_fixture",
            "platform": "two_fpga",
            "provider": CHIMEW_CROSSING_PROVIDER,
            "slls_per_fpga": 2,
            "provenance": {
                "producer": "fixture-physical-router",
                "producer_version": "1",
                "routing_sha256": "a" * 64,
            },
            "metrics": {"signals": 5, "physical_sll_crossings": 7},
            "entries": [
                {
                    "schedule_entry": f"s{index}",
                    "source_slls": source,
                    "sink_slls": sink,
                    "encoding": encoding,
                }
                for index, (source, sink, encoding) in enumerate(crossings)
            ],
        }

    def test_algorithm1_native_and_independent_replay_agree(self) -> None:
        result = build_chimew_initial_groups(
            self.schedule, self.crossings, executable=str(self.executable)
        )
        self.assertEqual(result["provider"], CHIMEW_GROUPING_PROVIDER)
        self.assertEqual(result["status"], "standalone_paper_kernel")
        self.assertEqual(result["integration_status"], "not-a-phase6-pin-plan")
        self.assertEqual(result["metrics"]["groups"], 2)
        self.assertEqual(result["metrics"]["group_physical_sll_crossings"], 4)
        groups = {
            entry["schedule_entry"]: entry["group"]
            for entry in result["entries"]
        }
        self.assertEqual(groups["s0"], groups["s1"])
        self.assertEqual(groups["s0"], groups["s2"])
        self.assertEqual(groups["s3"], groups["s4"])
        self.assertNotEqual(groups["s0"], groups["s3"])

    def test_normalized_region_substitute_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.crossings)
        invalid["provider"] = "openparf-lookahead-centroid-v1"
        with self.assertRaisesRegex(ValidationError, "physical SLL"):
            validate_chimew_crossings(self.schedule, invalid)

    def test_encoding_and_provenance_are_independently_checked(self) -> None:
        invalid_encoding = copy.deepcopy(self.crossings)
        invalid_encoding["entries"][0]["encoding"] ^= 1
        with self.assertRaisesRegex(ValidationError, "independently derived"):
            validate_chimew_crossings(self.schedule, invalid_encoding)
        invalid_digest = copy.deepcopy(self.crossings)
        invalid_digest["provenance"]["routing_sha256"] = "opaque"
        with self.assertRaisesRegex(ValidationError, "SHA-256"):
            validate_chimew_crossings(self.schedule, invalid_digest)

    def test_domains_and_ratios_never_share_a_group(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        schedule["entries"][4]["from"] = "fpga1"
        schedule["entries"][4]["to"] = "fpga0"
        result = build_chimew_initial_groups(
            schedule, self.crossings, executable=str(self.executable)
        )
        groups = {item["schedule_entry"]: item["group"] for item in result["entries"]}
        self.assertNotIn(groups["s4"], {groups[f"s{index}"] for index in range(4)})

    def test_random_small_inputs_preserve_capacity_and_coverage(self) -> None:
        randomizer = random.Random(17)
        count = 80
        schedule = {
            "design": "random",
            "platform": "two_fpga",
            "entries": [
                {
                    "id": f"r{index:03d}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 4,
                }
                for index in range(count)
            ],
        }
        raw = []
        crossings = 0
        for index in range(count):
            source = [bit for bit in range(4) if randomizer.randrange(3) == 0]
            sink = [bit for bit in range(4) if randomizer.randrange(3) == 0]
            encoding = sum(1 << bit for bit in source) | sum(
                1 << (4 + bit) for bit in sink
            )
            crossings += bin(encoding).count("1")
            raw.append(
                {
                    "schedule_entry": f"r{index:03d}",
                    "source_slls": source,
                    "sink_slls": sink,
                    "encoding": encoding,
                }
            )
        document = {
            "schema": CHIMEW_CROSSING_SCHEMA,
            "design": "random",
            "platform": "two_fpga",
            "provider": CHIMEW_CROSSING_PROVIDER,
            "slls_per_fpga": 4,
            "provenance": {
                "producer": "fixture-physical-router",
                "producer_version": "1",
                "routing_sha256": "b" * 64,
            },
            "metrics": {"signals": count, "physical_sll_crossings": crossings},
            "entries": raw,
        }
        result = build_chimew_initial_groups(
            schedule, document, executable=str(self.executable)
        )
        self.assertEqual(len(result["entries"]), count)
        sizes = {}
        for entry in result["entries"]:
            sizes[entry["group"]] = sizes.get(entry["group"], 0) + 1
        self.assertTrue(all(size <= 4 for size in sizes.values()))
        self.assertEqual(result["metrics"]["oracle_disagreements"], 0)


if __name__ == "__main__":
    unittest.main()
