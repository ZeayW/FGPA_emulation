import copy
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.multi_fpga_flow import (
    run_multi_fpga_flow,
    validate_multi_fpga_flow_report,
)
from tests.native_build import tlr_router


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = (
    ROOT / "platforms/virtual/academic_vtr_2fpga_p2p.json"
)


class MultiFpgaFlowTest(unittest.TestCase):
    def test_checked_board_independent_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "multi"
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                frame_slots=32,
                equivalence_cycles=8,
            )
            self.assertEqual(report["summary"]["used_fpgas"], 2)
            self.assertEqual(report["summary"]["instances"], 8)
            self.assertEqual(report["summary"]["equivalence_mismatches"], 0)
            self.assertEqual(
                report["stages"]["frontend"]["synthesis"]["mode"],
                "provided-yosys-json",
            )
            for relative in (
                "multi-fpga-flow-report.json",
                "frontend/phase1/design.emuir.json",
                "partition/assignment.json",
                "system-route/routes.json",
                "tdm/schedule.json",
                "split/manifest.json",
                "split/fpga0/netlist.json",
                "split/fpga1/netlist.json",
            ):
                self.assertTrue((output / relative).is_file(), relative)

    def test_report_rejects_cross_stage_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=Path(temporary_directory) / "multi",
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                frame_slots=32,
                equivalence_cycles=2,
            )
            broken = copy.deepcopy(report)
            broken["stages"]["tdm"]["platform"] = "different"
            with self.assertRaisesRegex(
                ValidationError, "platform identity disagrees"
            ):
                validate_multi_fpga_flow_report(broken)

    def test_nonempty_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            (output / "keep").write_text("user data", encoding="utf-8")
            with self.assertRaisesRegex(EmuFlowError, "empty directory"):
                run_multi_fpga_flow(
                    platform_path=PLATFORM,
                    output_dir=output,
                    yosys_json=ROOT / "examples/yosys/counter.json",
                    top="counter",
                )


if __name__ == "__main__":
    unittest.main()
