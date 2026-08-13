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
    CHIMEW_SCHEDULE_RATIO_PROVIDER,
    CHIMEW_TIMING_GUARD_PROVIDER,
    _oracle_groups,
    build_chimew_initial_groups,
    materialize_chimew_schedule_ratios,
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

    def test_timing_guard_preserves_complete_frozen_lane(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        for index, entry in enumerate(schedule["entries"]):
            entry["lane"] = index // 2
            entry["slot"] = index % 2
        result = build_chimew_initial_groups(
            schedule,
            self.crossings,
            executable=str(self.executable),
            protected_entries={"s0"},
        )
        groups = {item["schedule_entry"]: item for item in result["entries"]}
        self.assertEqual(groups["s0"]["group"], groups["s1"]["group"])
        self.assertEqual(groups["s0"]["timing_guard_lane"], groups["s1"]["timing_guard_lane"])
        self.assertNotIn("timing_guard_lane", groups["s2"])
        self.assertEqual(result["timing_guard"]["protected_lane_groups"], 1)
        self.assertEqual(result["timing_guard"]["protected_entries"], 2)

    def test_empty_timing_guard_keeps_legacy_assignment(self) -> None:
        legacy = build_chimew_initial_groups(
            self.schedule, self.crossings, executable=str(self.executable)
        )
        guarded_api = build_chimew_initial_groups(
            self.schedule,
            self.crossings,
            executable=str(self.executable),
            protected_entries=set(),
        )
        self.assertEqual(legacy, guarded_api)

    def test_guarded_mixed_ratio_domain_order_matches_native(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        schedule["entries"][0].update({"lane": 0, "slot": 0, "tdm_ratio": 2})
        schedule["entries"][1].update({"lane": 0, "slot": 1, "tdm_ratio": 2})
        for index, entry in enumerate(schedule["entries"][2:], start=2):
            entry.update({"lane": index, "slot": 0})
        result = build_chimew_initial_groups(
            schedule,
            self.crossings,
            executable=str(self.executable),
            protected_entries={"s0"},
        )
        groups = {item["schedule_entry"]: item["group"] for item in result["entries"]}
        self.assertEqual(groups["s0"], groups["s1"])
        self.assertEqual(result["metrics"]["oracle_disagreements"], 0)

    def test_schedule_seals_timing_guard_for_pipeline_replay(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        for index, entry in enumerate(schedule["entries"]):
            entry.update({"lane": index // 2, "slot": index % 2})
        schedule["chimew_timing_guard"] = {
            "provider": CHIMEW_TIMING_GUARD_PROVIDER,
            "scope": "EmuFlow extension, not a Chimew paper claim",
            "source_sha256": "a" * 64,
            "maximum_weight": 10.0,
            "protected_entries": ["s0"],
        }
        implicit = build_chimew_initial_groups(
            schedule, self.crossings, executable=str(self.executable)
        )
        explicit = build_chimew_initial_groups(
            schedule,
            self.crossings,
            executable=str(self.executable),
            protected_entries={"s0"},
        )
        self.assertEqual(implicit, explicit)
        invalid = copy.deepcopy(schedule)
        invalid["chimew_timing_guard"]["protected_entries"] = ["s0", "s0"]
        with self.assertRaisesRegex(ValidationError, "entries are invalid"):
            build_chimew_initial_groups(
                invalid, self.crossings, executable=str(self.executable)
            )

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

    def test_frozen_slots_never_collide_inside_a_group(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        for index, entry in enumerate(schedule["entries"]):
            entry["slot"] = index % 2
        result = build_chimew_initial_groups(
            schedule, self.crossings, executable=str(self.executable)
        )
        slots_by_group = {}
        source = {entry["id"]: entry for entry in schedule["entries"]}
        for item in result["entries"]:
            slots = slots_by_group.setdefault(item["group"], set())
            slot = source[item["schedule_entry"]]["slot"]
            self.assertNotIn(slot, slots)
            slots.add(slot)
        self.assertEqual(result["metrics"]["groups"], 3)

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

    def test_encoding_buckets_match_exhaustive_selection_trace(self) -> None:
        randomizer = random.Random(918)
        for count in range(1, 80):
            entries = [
                {
                    "id": f"e{index:03d}",
                    "link": f"l{index % 2}",
                    "from": "a",
                    "to": "b",
                    "tdm_ratio": 1 + index % 7,
                }
                for index in range(count)
            ]
            encodings = {
                entry["id"]: randomizer.randrange(64) for entry in entries
            }

            def exhaustive():
                from collections import Counter, defaultdict

                buckets = defaultdict(list)
                for index, entry in enumerate(entries):
                    buckets[
                        ((entry["link"], entry["from"], entry["to"]), entry["tdm_ratio"])
                    ].append(index)
                assignment = {}
                group_count = crossing_bits = 0
                for (_, ratio), indices in sorted(buckets.items()):
                    multiplicity = Counter(encodings[entries[index]["id"]] for index in indices)
                    remaining = sorted(
                        indices,
                        key=lambda index: (
                            -bin(encodings[entries[index]["id"]]).count("1"),
                            -encodings[entries[index]["id"]],
                            index,
                        ),
                    )
                    while remaining:
                        target = encodings[entries[remaining[0]]["id"]]
                        members = []
                        while remaining and len(members) < ratio:
                            def key(index):
                                encoding = encodings[entries[index]["id"]]
                                category = 0 if encoding == target else 1 if encoding | target == target else 2
                                return (
                                    category,
                                    bin(encoding ^ target).count("1") if category == 2 else 0,
                                    -bin(encoding).count("1"),
                                    multiplicity[encoding],
                                    -encoding,
                                    index,
                                )

                            selected = min(remaining, key=key)
                            encoding = encodings[entries[selected]["id"]]
                            target |= encoding
                            members.append(selected)
                            multiplicity[encoding] -= 1
                            remaining.remove(selected)
                        for index in members:
                            assignment[entries[index]["id"]] = group_count
                        crossing_bits += bin(target).count("1")
                        group_count += 1
                return assignment, group_count, crossing_bits

            self.assertEqual(_oracle_groups(entries, encodings), exhaustive())

    def test_large_repeated_encoding_bucket_is_scalable(self) -> None:
        count = 20_000
        schedule = {
            "design": "large-repeated-encoding",
            "platform": "two_fpga",
            "entries": [
                {
                    "id": f"s{index:05d}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 64,
                }
                for index in range(count)
            ],
        }
        crossings = {
            "schema": CHIMEW_CROSSING_SCHEMA,
            "design": schedule["design"],
            "platform": schedule["platform"],
            "provider": CHIMEW_CROSSING_PROVIDER,
            "slls_per_fpga": 2,
            "provenance": {
                "producer": "scale-fixture",
                "producer_version": "1",
                "routing_sha256": "c" * 64,
            },
            "metrics": {
                "signals": count,
                "physical_sll_crossings": count,
            },
            "entries": [
                {
                    "schedule_entry": entry["id"],
                    "source_slls": [index % 2],
                    "sink_slls": [],
                    "encoding": 1 << (index % 2),
                }
                for index, entry in enumerate(schedule["entries"])
            ],
        }
        result = build_chimew_initial_groups(
            schedule, crossings, executable=str(self.executable)
        )
        self.assertEqual(result["metrics"]["signals"], count)
        self.assertEqual(result["metrics"]["groups"], (count + 63) // 64)
        self.assertEqual(result["metrics"]["oracle_disagreements"], 0)

    def test_lane_occupancy_materializes_explicit_adapter_ratios(self) -> None:
        schedule = {
            "schema": "emuflow.tdm-schedule/v1",
            "design": "implicit-ratios",
            "platform": "two_fpga",
            "entries": [
                {"id": "a0", "link": "l", "from": "A", "to": "B", "lane": 0, "slot": 0},
                {"id": "a1", "link": "l", "from": "A", "to": "B", "lane": 0, "slot": 2},
                {"id": "a2", "link": "l", "from": "A", "to": "B", "lane": 1, "slot": 0},
                {"id": "b0", "link": "l", "from": "B", "to": "A", "lane": 0, "slot": 0},
            ],
        }
        result = materialize_chimew_schedule_ratios(schedule)
        ratios = {entry["id"]: entry["tdm_ratio"] for entry in result["entries"]}
        self.assertEqual(ratios, {"a0": 2, "a1": 2, "a2": 1, "b0": 1})
        self.assertEqual(
            result["chimew_ratio_materialization"]["provider"],
            CHIMEW_SCHEDULE_RATIO_PROVIDER,
        )
        self.assertEqual(
            result["chimew_ratio_materialization"]["direction_lane_groups"], 3
        )
        self.assertNotIn("tdm_ratio", schedule["entries"][0])
        self.assertEqual(result, materialize_chimew_schedule_ratios(schedule))

    def test_ratio_materialization_rejects_ambiguous_inputs(self) -> None:
        schedule = {
            "entries": [
                {"id": "a0", "link": "l", "from": "A", "to": "B", "lane": 0, "slot": 0},
                {"id": "a1", "link": "l", "from": "A", "to": "B", "lane": 0, "slot": 0},
            ]
        }
        with self.assertRaisesRegex(ValidationError, "collision"):
            materialize_chimew_schedule_ratios(schedule)
        schedule["entries"][1]["slot"] = 1
        schedule["entries"][0]["tdm_ratio"] = 2
        with self.assertRaisesRegex(ValidationError, "mixes"):
            materialize_chimew_schedule_ratios(schedule)


if __name__ == "__main__":
    unittest.main()
