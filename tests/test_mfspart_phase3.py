import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.multi_fpga_flow import run_multi_fpga_flow
from emuflow.phase3 import run_phase3
from emuflow.yosys import import_yosys_json
from tests.native_build import tlr_router


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platforms/virtual/xcvu3p_2fpga_p2p.json"


class MFSPartPhase3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a C++17 compiler is required")
        cls.temporary_directory = tempfile.TemporaryDirectory()
        native_bin = Path(cls.temporary_directory.name) / "bin"
        native_bin.mkdir()
        cls.executables = {}
        for name in ("coarsener", "initializer", "refiner", "legalizer"):
            executable = (
                native_bin / f"emuflow_mfspart_{name}"
            )
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Wpedantic",
                    "-Werror",
                    str(ROOT / f"src/native/mfspart_{name}.cpp"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            cls.executables[name] = str(executable)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_counter_runs_serial_mfspart_through_common_phase3_contract(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples/yosys/counter.json", top="counter", clocks=["clk"]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "counter.emuir.json"
            ir_path.write_text(json.dumps(ir.to_dict()), encoding="utf-8")
            report = run_phase3(
                ir_path,
                PLATFORM,
                root / "phase3",
                seed=23,
                provider="mfspart",
                mfspart_coarsener=self.executables["coarsener"],
                mfspart_initializer=self.executables["initializer"],
                mfspart_refiner=self.executables["refiner"],
                mfspart_legalizer=self.executables["legalizer"],
            )
            assignment = json.loads(
                (root / "phase3/assignment.json").read_text(encoding="utf-8")
            )
            self.assertTrue((root / "phase3/mfspart/hierarchy.json").is_file())
            self.assertTrue(
                (root / "phase3/mfspart/initial_partition.json").is_file()
            )
            self.assertTrue(
                (root / "phase3/mfspart/uncoarsening.json").is_file()
            )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["validation"]["status"], "pass")
        self.assertEqual(report["validation"]["instances"], 8)
        self.assertEqual(report["validation"]["used_fpgas"], 2)
        self.assertEqual(
            assignment["provider"], "mfspart-serial-paper-reproduction-v1"
        )
        self.assertEqual(
            set(assignment["cluster_assignment"]),
            {f"c{index:06d}" for index in range(8)},
        )

    def test_phase3_cli_selects_mfspart_provider(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples/yosys/counter.json", top="counter", clocks=["clk"]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "counter.emuir.json"
            ir_path.write_text(json.dumps(ir.to_dict()), encoding="utf-8")
            command = [
                sys.executable,
                "-c",
                "from emuflow.cli import main; raise SystemExit(main())",
                "phase3",
                "--ir",
                str(ir_path),
                "--platform",
                str(PLATFORM),
                "--out",
                str(root / "phase3"),
                "--provider",
                "mfspart",
                "--mfspart-coarsener",
                self.executables["coarsener"],
                "--mfspart-initializer",
                self.executables["initializer"],
                "--mfspart-refiner",
                self.executables["refiner"],
                "--mfspart-legalizer",
                self.executables["legalizer"],
            ]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["provider"], "mfspart-serial-paper-reproduction-v1")

    def test_min_used_legalizer_handles_intentionally_loose_balance(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples/yosys/counter.json", top="counter", clocks=["clk"]
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "counter.emuir.json"
            ir_path.write_text(json.dumps(ir.to_dict()), encoding="utf-8")
            report = run_phase3(
                ir_path,
                PLATFORM,
                root / "phase3",
                provider="mfspart",
                min_used_fpgas=2,
                balance_tolerance=10.0,
                mfspart_coarsener=self.executables["coarsener"],
                mfspart_initializer=self.executables["initializer"],
                mfspart_refiner=self.executables["refiner"],
                mfspart_legalizer=self.executables["legalizer"],
            )
            assignment = json.loads(
                (root / "phase3/assignment.json").read_text(encoding="utf-8")
            )
        self.assertEqual(report["validation"]["used_fpgas"], 2)
        self.assertGreater(
            assignment["provider_metadata"]["min_used_legalization_moves"], 0
        )

    def test_counter_runs_affected_multi_fpga_flow_with_mfspart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = {
                "EMUFLOW_NATIVE_ROOT": self.temporary_directory.name,
            }
            with patch.dict(os.environ, environment):
                report = run_multi_fpga_flow(
                    platform_path=PLATFORM,
                    output_dir=root / "flow",
                    yosys_json=ROOT / "examples/yosys/counter.json",
                    top="counter",
                    clocks=["clk"],
                    partition_provider="mfspart",
                    router=str(tlr_router()),
                    frame_slots=32,
                    equivalence_cycles=2,
                )
            self.assertTrue((root / "flow/tdm/schedule.json").is_file())
            self.assertTrue((root / "flow/runtime/qor_report.json").is_file())
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["used_fpgas"], 2)
        self.assertEqual(report["summary"]["equivalence_mismatches"], 0)


if __name__ == "__main__":
    unittest.main()
