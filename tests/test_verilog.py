import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from emuflow.io import write_json
from emuflow.verilog import emit_mapped_verilog, mapped_verilog
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

    def test_unknown_primitive_init_is_omitted(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        instances = ir.value["instances"]
        ff = next(item for item in instances if item["type"].startswith("FD"))
        ff.setdefault("parameters", {})["INIT"] = "x"
        with TemporaryDirectory() as temp:
            ir_path = Path(temp) / "counter.emuir.json"
            output = Path(temp) / "mapped.v"
            report_path = Path(temp) / "report.json"
            write_json(ir_path, ir.value)
            report = emit_mapped_verilog(
                ir_path,
                output,
                report_path,
            )
            text = output.read_text(encoding="utf-8")
        self.assertNotIn(".\\INIT (1'bx)", text)
        self.assertEqual(report["omitted_unknown_init_parameters"], 1)


if __name__ == "__main__":
    unittest.main()
