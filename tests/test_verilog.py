import unittest
from pathlib import Path

from emuflow.verilog import mapped_verilog
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]


class MappedVerilogTest(unittest.TestCase):
    def test_counter_emits_all_primitives_ports_and_constants(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        text = mapped_verilog(ir)
        self.assertIn("module \\counter ", text)
        self.assertEqual(text.count('(* KEEP = "yes"'), 8)
        self.assertIn("\\LUT2 ", text)
        self.assertIn("\\FDRE ", text)
        self.assertIn("4'b", text)
        self.assertIn("1'b", text)
        self.assertIn("endmodule", text)


if __name__ == "__main__":
    unittest.main()
