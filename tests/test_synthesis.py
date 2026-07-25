import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError
from emuflow.synthesis import build_yosys_script


class SynthesisTest(unittest.TestCase):
    def test_xcup_script_is_board_independent(self) -> None:
        script = build_yosys_script(
            [Path("rtl/counter.sv")],
            top="counter",
            output=Path("build/counter.json"),
            family="xcup",
        )
        self.assertIn("read_verilog -sv", script)
        self.assertIn("synth_xilinx -family xcup", script)
        self.assertIn("-top counter", script)
        self.assertNotIn('-top "counter"', script)
        self.assertIn("-noiopad -noclkbuf", script)
        self.assertIn('write_json "build/counter.json"', script)

    def test_missing_sources_are_rejected(self) -> None:
        with self.assertRaisesRegex(EmuFlowError, "at least one RTL source"):
            build_yosys_script(
                [],
                top="counter",
                output=Path("build/counter.json"),
            )

    def test_unsafe_top_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(EmuFlowError, "simple Verilog module name"):
            build_yosys_script(
                [Path("rtl/counter.sv")],
                top="counter; delete",
                output=Path("build/counter.json"),
            )


if __name__ == "__main__":
    unittest.main()
