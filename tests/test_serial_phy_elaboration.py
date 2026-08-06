import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.serial_phy_elaboration import (
    build_vivado_elaboration_tcl,
    build_yosys_elaboration_script,
)


class SerialPhyElaborationTest(unittest.TestCase):
    def test_builds_checked_yosys_script_and_quotes_paths(self) -> None:
        script = build_yosys_elaboration_script(
            [Path("provider source.sv"), Path("wrapper.sv")],
            "emuflow_partition_shell_mps4_1",
        )
        self.assertIn('read_verilog -sv "provider source.sv" wrapper.sv', script)
        self.assertIn(
            "hierarchy -check -top emuflow_partition_shell_mps4_1", script
        )
        self.assertIn("check -assert", script)
        self.assertTrue(script.endswith("stat"))

    def test_rejects_empty_source_or_top(self) -> None:
        with self.assertRaisesRegex(ValidationError, "source list"):
            build_yosys_elaboration_script([], "top")
        with self.assertRaisesRegex(ValidationError, "top"):
            build_yosys_elaboration_script([Path("provider.sv")], "")

    def test_builds_vivado_part_specific_black_box_gate(self) -> None:
        script = build_vivado_elaboration_tcl(
            [Path("provider source.sv"), Path("wrapper.sv")],
            "emuflow_partition_shell_mps4_1",
            "xcvu13p-fhga2104-1-e",
            Path("utilization.rpt"),
            {
                "channel_primitive": "GTYE4_CHANNEL",
                "common_primitive": "GTYE4_COMMON",
                "reference_clock_primitive": "IBUFDS_GTE4",
            },
            38,
            1,
            [Path("mps4_1.gt_sites.xdc")],
            [f"GTYE4_CHANNEL_X0Y{index}" for index in range(38)],
            10,
            [f"GTYE4_COMMON_X0Y{index}" for index in range(10)],
            [f"serial_wrapper/quad_0_phy/channel_{index}.gty_channel" for index in range(38)],
            [f"serial_wrapper/quad_{index}_phy/gty_common" for index in range(10)],
        )
        self.assertIn("create_project -in_memory", script)
        self.assertIn("xcvu13p-fhga2104-1-e", script)
        self.assertIn(
            "synth_design -mode out_of_context -flatten_hierarchy none", script
        )
        self.assertIn("IS_BLACKBOX == 1", script)
        self.assertIn("cells=[llength [get_cells -hier]]", script)
        self.assertIn("black_boxes=[llength $black_boxes]", script)
        self.assertIn("REF_NAME == GTYE4_CHANNEL", script)
        self.assertIn("channel_primitive_count", script)
        self.assertIn("REF_NAME == GTYE4_COMMON", script)
        self.assertIn("common_primitive_count", script)
        self.assertIn("expected=10", script)
        self.assertIn("expected=38", script)
        self.assertIn("read_xdc $constraint", script)
        self.assertIn("mps4_1.gt_sites.xdc", script)
        self.assertIn("set actual_channel_locs [lsort", script)
        self.assertIn("channel_locs=$actual_channel_locs", script)
        self.assertIn("common_locs=$actual_common_locs", script)
        self.assertIn("channel_cells=$actual_channel_cells", script)
        self.assertIn("common_cells=$actual_common_cells", script)
        self.assertIn("exit 10", script)
        self.assertIn("exit 6", script)

    def test_rejects_inconsistent_vivado_gt_contract(self) -> None:
        with self.assertRaisesRegex(ValidationError, "LOC inventory"):
            build_vivado_elaboration_tcl(
                [Path("provider.sv")],
                "top",
                "xcvu13p-fhga2104-1-e",
                Path("report.rpt"),
                {
                    "channel_primitive": "GTYE4_CHANNEL",
                    "reference_clock_primitive": "IBUFDS_GTE4",
                },
                expected_channel_primitives=2,
                expected_channel_locs=["GTYE4_CHANNEL_X0Y0"],
            )
        with self.assertRaisesRegex(ValidationError, "hierarchy inventory"):
            build_vivado_elaboration_tcl(
                [Path("provider.sv")],
                "top",
                "xcvu13p-fhga2104-1-e",
                Path("report.rpt"),
                {
                    "channel_primitive": "GTYE4_CHANNEL",
                    "reference_clock_primitive": "IBUFDS_GTE4",
                },
                expected_channel_primitives=2,
                constraint_sources=[Path("gt.xdc")],
                expected_channel_locs=[
                    "GTYE4_CHANNEL_X0Y0",
                    "GTYE4_CHANNEL_X0Y1",
                ],
                expected_channel_cells=["only_one_cell"],
            )


if __name__ == "__main__":
    unittest.main()
