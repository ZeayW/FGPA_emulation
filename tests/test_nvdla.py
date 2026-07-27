import tempfile
import unittest
from pathlib import Path

from scripts.benchmarks.nvdla_ram_stubs import generate


class NvdlaRamStubTest(unittest.TestCase):
    def test_parameter_and_port_order_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nv_ram_demo.v").write_text(
                """
module nv_ram_demo (
  clk,
  addr,
  dout
);
parameter FORCE_CONTENTION_ASSERTION_RESET_ACTIVE=1'b0;
input clk;
input [7:0] addr;
output [31:0] dout;
endmodule
""",
                encoding="utf-8",
            )
            output = root / "stubs.v"
            self.assertEqual(generate(root, output), (1, 0))
            text = output.read_text(encoding="utf-8")
            self.assertIn('(* black_box = "yes" *)', text)
            self.assertIn(
                "module nv_ram_demo #(parameter "
                "FORCE_CONTENTION_ASSERTION_RESET_ACTIVE=1'b0)",
                text,
            )
            self.assertLess(text.index("input clk"), text.index("input [7:0] addr"))
            self.assertLess(
                text.index("input [7:0] addr"), text.index("output [31:0] dout")
            )

    def test_logic_companion_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nv_ram_demo_logic.v").write_text(
                "module nv_ram_demo_logic; endmodule\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "no NVDLA SRAM wrappers"):
                generate(root, root / "stubs.v")

    def test_selected_rws_wrapper_gets_register_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nv_ram_rws_32x64.v").write_text(
                """
module nv_ram_rws_32x64 (clk, ra, re, dout, wa, we, di, pwrbus_ram_pd);
parameter FORCE_CONTENTION_ASSERTION_RESET_ACTIVE=1'b0;
input clk;
input [4:0] ra;
input re;
output [63:0] dout;
input [4:0] wa;
input we;
input [63:0] di;
input [31:0] pwrbus_ram_pd;
endmodule
""",
                encoding="utf-8",
            )
            output = root / "models.v"
            self.assertEqual(
                generate(root, output, r"nv_ram_rws_32x64"),
                (1, 1),
            )
            text = output.read_text(encoding="utf-8")
            self.assertNotIn('black_box = "yes"', text)
            self.assertIn('ram_style = "registers"', text)
            self.assertIn("reg [63:0] mem [0:31];", text)
            self.assertIn("always @(posedge clk)", text)
            self.assertIn("dout_reg <= mem[ra];", text)


if __name__ == "__main__":
    unittest.main()
