import unittest
from pathlib import Path

from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]


class YosysImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples/yosys/counter.json",
            top="counter",
            clocks=["clk"],
        )

    def test_resource_classification(self) -> None:
        totals = self.ir.resource_totals()
        self.assertEqual(totals.lut, 4)
        self.assertEqual(totals.ff, 4)
        self.assertEqual(totals.other, 0)

    def test_cut_classification(self) -> None:
        classes = {net["cut_class"] for net in self.ir.value["nets"]}
        self.assertIn("clock", classes)
        self.assertIn("reset", classes)
        self.assertIn("register_output", classes)
        self.assertIn("register_input", classes)
        register_inputs = {
            net["id"]
            for net in self.ir.value["nets"]
            if net["cut_class"] == "register_input"
        }
        self.assertEqual(
            register_inputs,
            {"next_q[0]", "next_q[1]", "next_q[2]", "next_q[3]"},
        )
        net_ids = {net["id"] for net in self.ir.value["nets"]}
        self.assertTrue({"q[0]", "q[1]", "q[2]", "q[3]"}.issubset(net_ids))

    def test_stats(self) -> None:
        stats = self.ir.stats()
        self.assertEqual(stats["instances"], 8)
        self.assertEqual(stats["resource_totals"], {"ff": 4, "lut": 4})
        self.assertEqual(stats["clocks"], 1)


if __name__ == "__main__":
    unittest.main()
