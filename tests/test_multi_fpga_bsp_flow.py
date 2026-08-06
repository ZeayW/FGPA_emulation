import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import ValidationError
from emuflow.io import write_json
from emuflow.multi_fpga_bsp_flow import (
    run_multi_fpga_bsp_flow,
    validate_multi_fpga_bsp_flow_report,
)
from emuflow.platform import Platform


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platforms/virtual/academic_vtr_2fpga_p2p.json"


class MultiFpgaBspFlowTest(unittest.TestCase):
    def test_orchestrates_checked_bsp_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            flow = root / "flow"
            output = root / "bsp"
            platform = Platform.load(PLATFORM)
            (flow / "tdm").mkdir(parents=True)
            (flow / "split").mkdir()
            write_json(
                flow / "multi-fpga-flow-report.json",
                {"status": "pass"},
            )
            write_json(
                flow / "tdm/schedule.json",
                {"design": "counter", "platform": platform.name},
            )
            write_json(
                flow / "split/phase6_report.json",
                {"design": "counter", "platform": platform.name},
            )
            for fpga in platform.fpgas:
                fpga_root = flow / "split" / fpga.id
                fpga_root.mkdir()
                write_json(fpga_root / "virtual_anchors.json", {})
                write_json(fpga_root / "transport.json", {})
                (fpga_root / "transport_schedule.sv").write_text(
                    "module transport; endmodule\n", encoding="utf-8"
                )
            (flow / "split/virtual_runtime_controller.sv").write_text(
                "module controller; endmodule\n", encoding="utf-8"
            )
            provider = root / "provider.json"
            runtime_provider = root / "runtime-provider.json"
            yosys = root / "yosys"
            for path in (provider, runtime_provider, yosys):
                path.write_text("{}\n", encoding="utf-8")

            phase6b = {
                "status": "pass",
                "design": "counter",
                "platform": platform.name,
            }
            runtime_sync = {"status": "pass"}
            phase6c = {
                "status": "pass",
                "design": "counter",
                "platform": platform.name,
                "hardware_release_status": "blocked_on_board_proof",
            }
            elaboration = {
                "status": "pass",
                "design": "counter",
                "platform": platform.name,
                "tool": {"name": "yosys"},
                "validation": {"elaboration_failures": 0},
            }
            with (
                patch(
                    "emuflow.multi_fpga_bsp_flow.run_phase6b",
                    return_value=phase6b,
                ),
                patch(
                    "emuflow.multi_fpga_bsp_flow.run_runtime_sync_materialization",
                    return_value=runtime_sync,
                ),
                patch(
                    "emuflow.multi_fpga_bsp_flow.run_phase6c",
                    return_value=phase6c,
                ),
                patch(
                    "emuflow.multi_fpga_bsp_flow.run_serial_phy_elaboration",
                    return_value=elaboration,
                ),
                patch(
                    "emuflow.multi_fpga_flow.validate_multi_fpga_flow_report",
                    return_value={
                        "status": "pass",
                        "design": "counter",
                        "platform": platform.name,
                    },
                ),
            ):
                report = run_multi_fpga_bsp_flow(
                    flow_root=flow,
                    platform_path=PLATFORM,
                    phy_provider_path=provider,
                    runtime_sync_provider_path=runtime_provider,
                    output_dir=output,
                    yosys_executable=yosys,
                )
            self.assertEqual(report["summary"]["status"], "pass")
            self.assertEqual(report["summary"]["elaboration_tool"], "yosys")
            self.assertFalse(
                report["validation"]["hardware_release_authorized"]
            )
            self.assertTrue(
                (output / "multi-fpga-bsp-flow-report.json").is_file()
            )

            broken = copy.deepcopy(report)
            broken["stages"]["phase6c"]["platform"] = "wrong"
            with self.assertRaisesRegex(ValidationError, "identity disagrees"):
                validate_multi_fpga_bsp_flow_report(broken)

    def test_requires_exactly_one_elaboration_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValidationError, "exactly one"):
                run_multi_fpga_bsp_flow(
                    flow_root=Path(temporary_directory) / "flow",
                    platform_path=PLATFORM,
                    phy_provider_path=Path(temporary_directory) / "provider",
                    runtime_sync_provider_path=(
                        Path(temporary_directory) / "runtime-provider"
                    ),
                    output_dir=Path(temporary_directory) / "out",
                )


if __name__ == "__main__":
    unittest.main()
