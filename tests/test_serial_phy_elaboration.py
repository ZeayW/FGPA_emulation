import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.serial_phy_elaboration import build_yosys_elaboration_script


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


if __name__ == "__main__":
    unittest.main()
