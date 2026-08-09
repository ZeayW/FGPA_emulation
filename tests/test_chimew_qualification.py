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
    evaluate_chimew_bank_channel_assignment,
)
from emuflow.cli import main
from emuflow.chimew_grouping import (
    CHIMEW_CROSSING_PROVIDER,
    CHIMEW_CROSSING_SCHEMA,
    build_chimew_initial_groups,
)
from emuflow.chimew_qualification import (
    CHIMEW_QUALIFICATION_PROVIDER,
    build_chimew_phase6_qualification,
    canonical_sha256,
    validate_chimew_phase6_qualification,
)
from emuflow.chimew_phase6 import (
    CHIMEW_ELECTRICAL_MAP_PROVIDER,
    CHIMEW_ELECTRICAL_MAP_SCHEMA,
)
from emuflow.chimew_pipeline import CHIMEW_PIPELINE_PROVIDER
from emuflow.chimew_refinement import (
    CHIMEW_POSITION_PROVIDER,
    CHIMEW_POSITION_SCHEMA,
    refine_chimew_groups,
)
from emuflow.chimew_rudy import (
    CHIMEW_RUDY_INPUT_PROVIDER,
    CHIMEW_RUDY_INPUT_SCHEMA,
    evaluate_chimew_rudy,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json


ROOT = Path(__file__).resolve().parents[1]


class ChimewQualificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.executables = {}
        for source, name in (
            ("chimew_signal_grouper.cpp", "grouper"),
            ("chimew_position_refiner.cpp", "refiner"),
            ("chimew_rudy.cpp", "rudy"),
            ("chimew_bank_channel_assigner.cpp", "assigner"),
        ):
            executable = Path(cls.temporary_directory.name) / name
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
            cls.executables[name] = str(executable)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.schedule = {
            "design": "qualification_fixture",
            "platform": "two_fpga",
            "entries": [
                {
                    "id": "s0",
                    "link": "AB",
                    "from": "A",
                    "to": "B",
                    "tdm_ratio": 1,
                    "lane": 0,
                    "slot": 0,
                },
                {
                    "id": "s1",
                    "link": "AB",
                    "from": "B",
                    "to": "A",
                    "tdm_ratio": 1,
                    "lane": 0,
                    "slot": 0,
                },
            ],
        }
        self.crossings = {
            "schema": CHIMEW_CROSSING_SCHEMA,
            "provider": CHIMEW_CROSSING_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.schedule["platform"],
            "slls_per_fpga": 2,
            "provenance": {
                "producer": "fixture-router",
                "producer_version": "1",
                "routing_sha256": "a" * 64,
            },
            "metrics": {"signals": 2, "physical_sll_crossings": 2},
            "entries": [
                {
                    "schedule_entry": "s0",
                    "source_slls": [0],
                    "sink_slls": [],
                    "encoding": 1,
                },
                {
                    "schedule_entry": "s1",
                    "source_slls": [1],
                    "sink_slls": [],
                    "encoding": 2,
                },
            ],
        }
        self.positions = {
            "schema": CHIMEW_POSITION_SCHEMA,
            "provider": CHIMEW_POSITION_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.schedule["platform"],
            "coordinate_system": "physical-site-y",
            "provenance": {
                "producer": "fixture-lookahead",
                "producer_version": "1",
                "placement_sha256": "b" * 64,
            },
            "metrics": {"signals": 2},
            "entries": [
                {"schedule_entry": "s0", "source_y": 10.0},
                {"schedule_entry": "s1", "source_y": 50.0},
            ],
        }
        self.initial = build_chimew_initial_groups(
            self.schedule, self.crossings, executable=self.executables["grouper"]
        )
        self.refined = refine_chimew_groups(
            self.schedule,
            self.crossings,
            self.initial,
            self.positions,
            executable=self.executables["refiner"],
        )
        self.rudy_input = {
            "schema": CHIMEW_RUDY_INPUT_SCHEMA,
            "provider": CHIMEW_RUDY_INPUT_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.schedule["platform"],
            "coordinate_system": "physical-site-xy",
            "degenerate_bbox_policy": "reject",
            "wire_pitch_per_layer": 1.0,
            "max_utilization": 1.0,
            "provenance": {
                "producer": "fixture-lookahead",
                "producer_version": "1",
                "placement_sha256": "b" * 64,
                "netlist_sha256": "d" * 64,
                "architecture_sha256": "c" * 64,
            },
            "grid": {
                "origin_x": 0.0,
                "origin_y": 0.0,
                "bin_width": 100.0,
                "bin_height": 100.0,
                "columns": 1,
                "rows": 1,
                "capacities": [1000.0],
            },
            "metrics": {"nets": 1, "pins": 2},
            "nets": [
                {
                    "id": "n0",
                    "pins": [{"x": 10.0, "y": 10.0}, {"x": 90.0, "y": 90.0}],
                }
            ],
        }
        self.rudy_report = evaluate_chimew_rudy(
            self.rudy_input, executable=self.executables["rudy"]
        )
        group_by_signal = {
            record["schedule_entry"]: record["group"]
            for record in self.refined["entries"]
        }
        self.bank_input = {
            "schema": CHIMEW_BANK_CHANNEL_INPUT_SCHEMA,
            "provider": CHIMEW_BANK_CHANNEL_INPUT_PROVIDER,
            "design": self.schedule["design"],
            "platform": self.schedule["platform"],
            "coordinate_system": "physical-site-xy",
            "cost_quantization_per_site": 1000,
            "provenance": {
                "producer": "fixture-lookahead",
                "producer_version": "1",
                "grouping_sha256": canonical_sha256(self.refined),
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
                            "id": f"channel{index}",
                            "order": index,
                            "pin_a": {"x": 0.0, "y": float(10 + 40 * index)},
                            "pin_b": {"x": 100.0, "y": float(10 + 40 * index)},
                        }
                        for index in range(2)
                    ],
                }
            ],
            "groups": [
                {
                    "id": f"group{group_by_signal[signal]}",
                    "domain": "AB",
                    "kind": "tdm_group",
                    "direction": direction,
                    "members": [
                        {
                            "id": signal,
                            "fanout": {"x": source_x, "y": source_y},
                            "fanins": [{"x": sink_x, "y": source_y}],
                        }
                    ],
                }
                for signal, direction, source_x, sink_x, source_y in (
                    ("s0", "a_to_b", 0.0, 100.0, 10.0),
                    ("s1", "b_to_a", 100.0, 0.0, 50.0),
                )
            ],
            "metrics": {
                "groups": 2,
                "signals": 2,
                "fanins": 2,
                "bank_pairs": 1,
                "channels": 2,
            },
        }
        self.bank_report = evaluate_chimew_bank_channel_assignment(
            self.bank_input, executable=self.executables["assigner"]
        )

    def _build(self):
        return build_chimew_phase6_qualification(
            self.schedule,
            self.crossings,
            self.initial,
            self.positions,
            self.refined,
            self.rudy_input,
            self.rudy_report,
            self.bank_input,
            self.bank_report,
        )

    def test_complete_chain_is_sealed_and_replayable(self) -> None:
        certificate = self._build()
        self.assertEqual(certificate["provider"], CHIMEW_QUALIFICATION_PROVIDER)
        self.assertEqual(certificate["status"], "pass")
        self.assertEqual(certificate["metrics"]["artifact_chain_disagreements"], 0)
        validation = validate_chimew_phase6_qualification(
            certificate,
            self.schedule,
            self.crossings,
            self.initial,
            self.positions,
            self.refined,
            self.rudy_input,
            self.rudy_report,
            self.bank_input,
            self.bank_report,
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["signals"], 2)

    def test_cli_materializes_the_same_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = {
                "schedule": self.schedule,
                "crossings": self.crossings,
                "initial": self.initial,
                "positions": self.positions,
                "refined": self.refined,
                "rudy-input": self.rudy_input,
                "rudy-report": self.rudy_report,
                "assignment-input": self.bank_input,
                "assignment-report": self.bank_report,
            }
            paths = {}
            for label, document in documents.items():
                paths[label] = root / f"{label}.json"
                write_json(paths[label], document)
            output = root / "qualification.json"
            arguments = ["pin-plan", "chimew-qualify"]
            for option in (
                "schedule",
                "crossings",
                "initial",
                "positions",
                "refined",
                "rudy-input",
                "rudy-report",
                "assignment-input",
                "assignment-report",
            ):
                cli_name = {
                    "initial": "initial-grouping",
                    "refined": "refined-grouping",
                }.get(option, option)
                arguments.extend([f"--{cli_name}", str(paths[option])])
            arguments.extend(["--output", str(output)])
            self.assertEqual(main(arguments), 0)
            self.assertEqual(read_json(output), self._build())

    def test_tampering_and_cross_run_mixing_are_rejected(self) -> None:
        bad_rudy = copy.deepcopy(self.rudy_report)
        bad_rudy["bins"][0]["load"] += 1.0
        with self.assertRaisesRegex(ValidationError, "RUDY bin"):
            build_chimew_phase6_qualification(
                self.schedule,
                self.crossings,
                self.initial,
                self.positions,
                self.refined,
                self.rudy_input,
                bad_rudy,
                self.bank_input,
                self.bank_report,
            )
        bad_bank = copy.deepcopy(self.bank_input)
        bad_bank["provenance"]["grouping_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "provenance chain"):
            build_chimew_phase6_qualification(
                self.schedule,
                self.crossings,
                self.initial,
                self.positions,
                self.refined,
                self.rudy_input,
                self.rudy_report,
                bad_bank,
                evaluate_chimew_bank_channel_assignment(
                    bad_bank, executable=self.executables["assigner"]
                ),
            )

    def test_rejected_rudy_gate_cannot_qualify_phase6(self) -> None:
        rejected_input = copy.deepcopy(self.rudy_input)
        rejected_input["max_utilization"] = 0.01
        rejected_report = evaluate_chimew_rudy(
            rejected_input, executable=self.executables["rudy"]
        )
        with self.assertRaisesRegex(ValidationError, "gate did not pass"):
            build_chimew_phase6_qualification(
                self.schedule,
                self.crossings,
                self.initial,
                self.positions,
                self.refined,
                rejected_input,
                rejected_report,
                self.bank_input,
                self.bank_report,
            )

    def test_cli_runs_the_complete_phase6_pipeline_without_reoptimizing_report(self) -> None:
        platform = {
            "schema": "emuflow.boarddb/v1",
            "platform": {
                "name": self.schedule["platform"],
                "kind": "hardware",
                "description": "Chimew pipeline fixture",
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
                    "id": "AB",
                    "endpoints": ["A", "B"],
                    "direction": "full_duplex",
                    "mode": "parallel",
                    "data_lanes_per_direction": 2,
                    "fabric_clock_mhz": 100.0,
                    "latency_cycles": 1,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {}
            for label, document in {
                "schedule": self.schedule,
                "platform": platform,
                "crossings": self.crossings,
                "positions": self.positions,
                "rudy-input": self.rudy_input,
                "assignment-input": self.bank_input,
            }.items():
                paths[label] = root / f"{label}.json"
                write_json(paths[label], document)
            electrical = {
                "schema": CHIMEW_ELECTRICAL_MAP_SCHEMA,
                "provider": CHIMEW_ELECTRICAL_MAP_PROVIDER,
                "design": self.schedule["design"],
                "platform": self.schedule["platform"],
                "provenance": {
                    "producer": "fixture-bsp",
                    "producer_version": "1",
                    "boarddb_sha256": hashlib.sha256(
                        paths["platform"].read_bytes()
                    ).hexdigest(),
                    "package_pin_inventory_sha256": "e" * 64,
                },
                "fpga_y_bounds": [
                    {"fpga": fpga, "y_min": 0.0, "y_max": 100.0}
                    for fpga in ("A", "B")
                ],
                "channels": [
                    {
                        "chimew_channel": f"channel{index}",
                        "link": "AB",
                        "physical_lane": index,
                        "bank_a": "A0",
                        "bank_b": "B0",
                        "package_pin_a": f"A{index}",
                        "package_pin_b": f"B{index}",
                        "iostandard": "LVCMOS18",
                        "supported_iostandards": ["LVCMOS18"],
                        "bank_voltage": 1.8,
                        "electrical_class": "single_ended_parallel",
                        "reserved": False,
                    }
                    for index in range(2)
                ],
                "metrics": {"channels": 2, "package_pins": 4, "concrete_lanes": 2},
            }
            paths["electrical-map"] = root / "electrical-map.json"
            write_json(paths["electrical-map"], electrical)
            output = root / "pipeline"
            arguments = ["pin-plan", "chimew-run"]
            for option in (
                "schedule",
                "platform",
                "crossings",
                "positions",
                "rudy-input",
                "assignment-input",
                "electrical-map",
            ):
                arguments.extend([f"--{option}", str(paths[option])])
            for option in ("grouper", "refiner", "rudy", "assigner"):
                arguments.extend([f"--{option}", self.executables[option]])
            arguments.extend(["--out", str(output)])
            self.assertEqual(main(arguments), 0)
            report = read_json(output / "pipeline_report.json")
            self.assertEqual(report["provider"], CHIMEW_PIPELINE_PROVIDER)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["metrics"]["artifact_chain_disagreements"], 0)
            self.assertTrue(
                (output / "phase6-adapter" / "qualification_certificate.json").is_file()
            )

            direct_lane = copy.deepcopy(self.schedule)
            for entry in direct_lane["entries"]:
                entry.pop("tdm_ratio")
            write_json(paths["schedule"], direct_lane)
            direct_output = root / "direct-lane-pipeline"
            direct_arguments = list(arguments[:-2])
            direct_arguments.extend(["--out", str(direct_output)])
            self.assertEqual(main(direct_arguments), 0)
            direct_report = read_json(direct_output / "pipeline_report.json")
            self.assertEqual(direct_report["status"], "pass")


if __name__ == "__main__":
    unittest.main()
