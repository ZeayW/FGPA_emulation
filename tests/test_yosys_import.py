import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from emuflow.io import read_json, write_json
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

    def test_scopeinfo_metadata_is_not_a_physical_instance(self) -> None:
        source = read_json(ROOT / "examples/yosys/counter.json")
        source["modules"]["counter"]["cells"]["debug_scope"] = {
            "type": "$scopeinfo",
            "parameters": {"TYPE": "module"},
            "attributes": {"module": "counter"},
            "port_directions": {},
            "connections": {},
        }
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "counter-with-scopeinfo.json"
            write_json(path, source)
            ir = import_yosys_json(path, top="counter", clocks=["clk"])

        self.assertNotIn(
            "$scopeinfo", {instance["type"] for instance in ir.value["instances"]}
        )
        self.assertEqual(
            ir.resource_totals().to_dict(include_zeros=False),
            {"ff": 4, "lut": 4},
        )


if __name__ == "__main__":
    unittest.main()
