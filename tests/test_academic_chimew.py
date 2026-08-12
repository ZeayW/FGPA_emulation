from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.academic_chimew import materialize_academic_chimew_inputs
from emuflow.chimew_pipeline import (
    run_chimew_phase6_pipeline,
    validate_chimew_phase6_pipeline,
)
from emuflow.cli import _build_parser
from emuflow.errors import EmuFlowError, ValidationError
from emuflow.io import read_json, write_json
from emuflow.multi_fpga_flow import (
    PHASE6_AB_COMPARISON_SCHEMA,
    run_multi_fpga_flow,
    validate_phase6_ab_comparison,
)
from emuflow.phase1 import run_phase1
from emuflow.phase3 import run_phase3
from emuflow.phase4 import run_phase4
from emuflow.phase5 import run_phase5
from tests.native_build import tlr_router


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platforms/virtual/academic_vtr_2fpga_p2p.json"


class AcademicChimewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.native_root = tempfile.TemporaryDirectory()
        cls.executables = {}
        for source, label in (
            ("chimew_signal_grouper.cpp", "grouper"),
            ("chimew_position_refiner.cpp", "refiner"),
            ("chimew_rudy.cpp", "rudy"),
            ("chimew_bank_channel_assigner.cpp", "assigner"),
        ):
            executable = Path(cls.native_root.name) / label
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    "-pthread",
                    str(ROOT / "src/native" / source),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            cls.executables[label] = str(executable)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.native_root.cleanup()

    def _upstream(self, root: Path) -> tuple[Path, Path, Path, Path]:
        phase1 = root / "phase1"
        phase3 = root / "phase3"
        phase4 = root / "phase4"
        phase5 = root / "phase5"
        run_phase1(
            ROOT / "examples/yosys/counter.json",
            PLATFORM,
            phase1,
            top="counter",
            clocks=["clk"],
        )
        run_phase3(
            phase1 / "design.emuir.json",
            PLATFORM,
            phase3,
            provider="greedy",
        )
        run_phase4(
            phase3 / "assignment.json",
            PLATFORM,
            phase4,
            frame_slots=32,
            router=str(tlr_router()),
        )
        run_phase5(phase4 / "routes.json", PLATFORM, phase5)
        return (
            phase1 / "design.emuir.json",
            phase3 / "assignment.json",
            phase4 / "routes.json",
            phase5 / "schedule.json",
        )

    def _physical_report(self, root: Path, assignment_path: Path) -> dict:
        assignment = read_json(assignment_path)["instance_assignment"]
        records = []
        for fpga in ("fpga0", "fpga1"):
            fpga_root = root / fpga
            fpga_root.mkdir(parents=True)
            atoms = sorted(
                instance
                for instance, destination in assignment.items()
                if destination == fpga
            )
            clusters = []
            placement_lines = []
            for index, atom in enumerate(atoms):
                cluster = f"cluster_{index}"
                clusters.append({"name": cluster, "atoms": [atom]})
                placement_lines.append(
                    f"{cluster} {index + 1} {2 * index + 1} 0 0 #{index}"
                )
            placement = fpga_root / "lookahead.place"
            placement.write_text(
                "\n".join(placement_lines) + "\n", encoding="utf-8"
            )
            packed = fpga_root / "packed.json"
            write_json(packed, {"clusters": clusters})
            placement_ir = fpga_root / "placement.emuir.json"
            write_json(placement_ir, {"schema": "test"})
            write_json(fpga_root / "architecture.json", {"schema": "test"})
            records.append(
                {
                    "fpga": fpga,
                    "status": "pass",
                    "stages": {
                        "openparf_placement": {
                            "artifacts": {"vpr_placement": str(placement)}
                        },
                        "packed_contract": {"output": str(packed)},
                        "placement_ir": {"output": str(placement_ir)},
                    },
                }
            )
        return {"fpgas": records}

    def test_materialized_inputs_run_complete_source_bound_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir, assignment, routes, schedule = self._upstream(root)
            lookahead = materialize_academic_chimew_inputs(
                ir_path=ir,
                schedule_path=schedule,
                routes_path=routes,
                platform_path=PLATFORM,
                physical_report=self._physical_report(root / "physical", assignment),
                output_dir=root / "lookahead",
                grouper=self.executables["grouper"],
                refiner=self.executables["refiner"],
            )
            self.assertEqual(
                lookahead["qualification"], "academic-virtual-physical-model"
            )
            self.assertEqual(
                lookahead["metrics"]["placement_endpoint_fallbacks"], 0
            )
            artifacts = lookahead["artifacts"]
            bank_input = read_json(Path(artifacts["bank_channel_input"]["path"]))
            electrical_map = read_json(Path(artifacts["electrical_map"]["path"]))
            directions = {group["direction"] for group in bank_input["groups"]}
            self.assertEqual(directions, {"a_to_b", "b_to_a"})
            self.assertEqual(len(bank_input["domains"]), len(directions))
            self.assertNotIn(
                "either",
                {channel["direction"] for channel in electrical_map["channels"]},
            )
            self.assertEqual(len(bank_input["domains"]), 2)
            domain_ids = {domain["id"] for domain in bank_input["domains"]}
            self.assertEqual(
                domain_ids,
                {"link_0_1:a_to_b", "link_0_1:b_to_a"},
            )
            lane_directions = {
                (channel["physical_lane"], channel["direction"])
                for channel in electrical_map["channels"]
            }
            self.assertIn((0, "a_to_b"), lane_directions)
            self.assertIn((0, "b_to_a"), lane_directions)
            report = run_chimew_phase6_pipeline(
                schedule,
                PLATFORM,
                Path(artifacts["crossings"]["path"]),
                Path(artifacts["positions"]["path"]),
                Path(artifacts["rudy_input"]["path"]),
                Path(artifacts["bank_channel_input"]["path"]),
                Path(artifacts["electrical_map"]["path"]),
                root / "chimew",
                source_paths={
                    label: Path(path)
                    for label, path in lookahead["sources"].items()
                },
                grouper=self.executables["grouper"],
                refiner=self.executables["refiner"],
                rudy=self.executables["rudy"],
                assigner=self.executables["assigner"],
                region_count=4,
            )
            self.assertEqual(report["qualification_scope"], "byte-bound-source-artifacts")
            self.assertEqual(
                validate_chimew_phase6_pipeline(root / "chimew")["status"],
                "pass",
            )

    def test_comparison_validator_rejects_tampered_delta(self) -> None:
        digest = "a" * 64
        def physical(wirelength: int, critical: float, wns: float) -> dict:
            return {
                "fpgas": [
                    {
                        "critical_path_ns": critical,
                        "stages": {
                            "vpr_route": {"metrics": {"wirelength": wirelength}}
                        },
                        "physical_result": {
                            "timing": {
                                "wns_ns": wns,
                                "tns_ns": 0.0,
                                "failing_endpoints": 0,
                                "failing_endpoint_constraints": 0,
                            },
                            "closure": {
                                "unrouted_nets": 0,
                                "drc_violations": 0,
                            },
                        },
                    }
                ]
            }

        baseline_physical = physical(100, 10.0, 1.0)
        chimew_physical = physical(90, 9.0, 2.0)
        report = {
            "schema": PHASE6_AB_COMPARISON_SCHEMA,
            "status": "pass",
            "selected_provider": "chimew",
            "baseline_provider": "historical-default-static-phase6",
            "qualification": "academic-virtual-physical-model",
            "frozen_upstream": {
                "emuir_sha256": digest,
                "assignment_sha256": digest,
                "routes_sha256": digest,
                "schedule_sha256": digest,
                "platform_sha256": digest,
            },
            "baseline": {"physical": baseline_physical},
            "chimew": {"physical": chimew_physical},
            "baseline_physical": {
                "total_wirelength": 100,
                "worst_critical_path_ns": 10.0,
                "worst_wns_ns": 1.0,
                "total_tns_ns": 0.0,
                "failing_endpoints": 0,
                "failing_endpoint_constraints": 0,
                "unrouted_nets": 0,
                "drc_violations": 0,
            },
            "chimew_physical": {
                "total_wirelength": 90,
                "worst_critical_path_ns": 9.0,
                "worst_wns_ns": 2.0,
                "total_tns_ns": 0.0,
                "failing_endpoints": 0,
                "failing_endpoint_constraints": 0,
                "unrouted_nets": 0,
                "drc_violations": 0,
            },
            "physical_delta": {
                "total_wirelength": -10,
                "worst_critical_path_ns": -1.0,
                "worst_wns_ns": 1.0,
                "total_tns_ns": 0.0,
                "failing_endpoints": 0,
                "failing_endpoint_constraints": 0,
            },
            "pin_plan_metrics": {"signals": 2},
        }
        with patch(
            "emuflow.multi_fpga_flow.validate_multi_fpga_physical_report",
            return_value={"status": "pass"},
        ):
            self.assertEqual(
                validate_phase6_ab_comparison(report)["status"], "pass"
            )
            report["physical_delta"]["total_wirelength"] = -11
            with self.assertRaisesRegex(ValidationError, "deltas disagree"):
                validate_phase6_ab_comparison(report)

    def test_open_compile_defaults_to_auto_chimew_selection(self) -> None:
        arguments = _build_parser().parse_args(
            [
                "multi-fpga",
                "compile",
                "--yosys-json",
                str(ROOT / "examples/yosys/counter.json"),
                "--platform",
                str(PLATFORM),
                "--out",
                "unused",
            ]
        )
        self.assertEqual(arguments.phase6_provider, "auto")
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(EmuFlowError, "requires --physical"):
                run_multi_fpga_flow(
                    platform_path=PLATFORM,
                    output_dir=Path(temporary_directory) / "invalid",
                    yosys_json=ROOT / "examples/yosys/counter.json",
                    phase6_provider="chimew",
                )


if __name__ == "__main__":
    unittest.main()
