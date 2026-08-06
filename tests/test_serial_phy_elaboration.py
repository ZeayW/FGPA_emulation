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
                "reference_clock_primitive": "IBUFDS_GTE4",
            },
            38,
            1,
        )
        self.assertIn("create_project -in_memory", script)
        self.assertIn("xcvu13p-fhga2104-1-e", script)
        self.assertIn("synth_design -rtl -mode out_of_context", script)
        self.assertIn("IS_BLACKBOX == 1", script)
        self.assertIn("cells=[llength [get_cells -hier]]", script)
        self.assertIn("black_boxes=[llength $black_boxes]", script)
        self.assertIn("REF_NAME == GTYE4_CHANNEL", script)
        self.assertIn("channel_primitive_count", script)
        self.assertIn("expected=38", script)


if __name__ == "__main__":
    unittest.main()
