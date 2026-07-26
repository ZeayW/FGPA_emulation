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
        self.assertIn("; flatten; opt_clean; check;", script)
        self.assertIn('write_json "build/counter.json"', script)

    def test_logic_only_policy_disables_hard_mapping(self) -> None:
        script = build_yosys_script(
            [Path("rtl/design.v")],
            top="design",
            output=Path("build/design.json"),
            policy="logic-only",
        )
        for option in (
            "-nocarry",
            "-nowidelut",
            "-nodsp",
            "-nobram",
            "-nolutram",
            "-nosrl",
        ):
            self.assertIn(option, script)
        self.assertIn("techmap -map", script)
        self.assertIn("logic_only_map.v", script)

    def test_optional_mapped_verilog_preserves_names(self) -> None:
        script = build_yosys_script(
            [Path("rtl/design.v")],
            top="design",
            output=Path("build/design.json"),
            verilog_output=Path("build/design.v"),
        )
        self.assertIn("setattr -set keep 1 c:*", script)
        self.assertIn("setattr -set dont_touch 1 c:*", script)
        self.assertIn('write_verilog -norename "build/design.v"', script)
        self.assertNotIn("write_verilog -noattr", script)

    def test_unknown_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(EmuFlowError, "synthesis policy"):
            build_yosys_script(
                [Path("rtl/design.v")],
                top="design",
                output=Path("build/design.json"),
                policy="magic",
            )

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
