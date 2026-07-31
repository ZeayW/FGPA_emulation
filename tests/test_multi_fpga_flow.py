import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.multi_fpga_flow import (
    run_multi_fpga_flow,
    validate_multi_fpga_flow_report,
)
from tests.native_build import tdm_ratio_optimizer, tlr_router


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
                report["runtime"]["validation"]["status"], "pass"
            )
            self.assertEqual(report["summary"]["frame_slots"], 32)
            self.assertEqual(
                report["stages"]["frontend"]["synthesis"]["mode"],
                "provided-yosys-json",
            )
            persisted = json.loads(
                (output / "multi-fpga-flow-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                validate_multi_fpga_flow_report(persisted)["status"],
                "pass",
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
                "runtime/runtime_contract.json",
                "runtime/qor_report.json",
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

    def test_timing_driven_pipeline_connects_sta_through_tdm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_sta = root / "sta"
            fake_sta.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path

rows = Path(os.environ["EMUFLOW_STA_NET_MAP"]).read_text().splitlines()[1:]
header = (
    "path_id_hex\\tclock_domain_hex\\tclock_period_ns\\t"
    "slack_ns\\tfixed_delay_ns\\tpath_nets_hex"
)
records = [header]
clock = "clk".encode().hex()
for index, row in enumerate(rows):
    _, emuir_hex = row.split("\\t")
    path_id = f"path-{index}".encode().hex()
    records.append(
        f"{path_id}\\t{clock}\\t10\\t9.5\\t0.5\\t{emuir_hex}"
    )
Path(os.environ["EMUFLOW_STA_OUTPUT"]).write_text(
    "\\n".join(records) + "\\n"
)
""",
                encoding="utf-8",
            )
            fake_sta.chmod(fake_sta.stat().st_mode | stat.S_IXUSR)
            output = root / "multi"
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                timing_driven=True,
                clock_periods={"clk": 10.0},
                opensta=str(fake_sta),
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                frame_slots=32,
                optimize_frame_slots=True,
                equivalence_cycles=2,
            )
            self.assertEqual(report["timing"]["status"], "pass")
            self.assertFalse(
                report["timing"]["partition_weights_applied"]
            )
            self.assertGreater(
                report["timing"]["cut_path_projection"][
                    "projected_paths"
                ],
                0,
            )
            self.assertIn(
                "timing_validation", report["stages"]["tdm"]
            )
            self.assertLess(
                report["frame_search"]["selected_frame_slots"], 32
            )
            self.assertEqual(
                report["summary"]["frame_slots"],
                report["frame_search"]["selected_frame_slots"],
            )
            broken = copy.deepcopy(report)
            broken["frame_search"]["selected_frame_slots"] = 31
            with self.assertRaisesRegex(
                ValidationError, "selected frame-search candidate"
            ):
                validate_multi_fpga_flow_report(broken)
            for relative in (
                "timing/path-database.json",
                "timing/partition-net-weights.json",
                "timing/cut-timing-paths.json",
                "frame-search/frame-search-report.json",
                "runtime/runtime_contract.json",
            ):
                self.assertTrue((output / relative).is_file(), relative)

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
