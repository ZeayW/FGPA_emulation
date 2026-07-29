import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.io import write_json
from emuflow.partition import PARTITION_ASSIGNMENT_SCHEMA
from emuflow.sta import (
    VIVADO_STA_TSV_HEADER,
    import_vivado_sta_tsv,
    write_vivado_cut_net_map,
)
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]


class StaAdapterTest(unittest.TestCase):
    def test_cut_map_and_vivado_tsv_import_preserve_names(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        cut_net = next(
            net["id"]
            for net in ir.value["nets"]
            if net["cut_class"] == "register_output"
        )
        assignment = {
            "schema": PARTITION_ASSIGNMENT_SCHEMA,
            "design": "counter",
            "platform": "fixture",
            "cut_nets": [
                {
                    "net": cut_net,
                    "source_fpgas": ["a"],
                    "sink_fpgas": ["b"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "ir.json"
            assignment_path = root / "assignment.json"
            map_path = root / "cut-map.tsv"
            input_path = root / "vivado.tsv"
            output_path = root / "timing.json"
            write_json(ir_path, ir.value)
            write_json(assignment_path, assignment)
            report = write_vivado_cut_net_map(
                ir_path, assignment_path, map_path
            )
            self.assertEqual(report["cut_nets"], 1)
            map_fields = map_path.read_text(encoding="utf-8").splitlines()[
                1
            ].split("\t")
            self.assertEqual(
                bytes.fromhex(map_fields[1]).decode("utf-8"), cut_net
            )
            input_path.write_text(
                VIVADO_STA_TSV_HEADER
                + "\n"
                + "\t".join(
                    [
                        "path with spaces".encode().hex(),
                        "clk[0]".encode().hex(),
                        "10.0",
                        "-0.25",
                        "9.75",
                        cut_net.encode().hex(),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            imported = import_vivado_sta_tsv(
                input_path, assignment_path, output_path
            )
            artifact = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(imported["paths"], 1)
        self.assertEqual(artifact["paths"][0]["id"], "path with spaces")
        self.assertEqual(artifact["paths"][0]["clock_domain"], "clk[0]")
        self.assertEqual(artifact["paths"][0]["cut_nets"], [cut_net])

    def test_unknown_cut_net_is_rejected(self) -> None:
        assignment = {
            "schema": PARTITION_ASSIGNMENT_SCHEMA,
            "design": "test",
            "cut_nets": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignment_path = root / "assignment.json"
            input_path = root / "vivado.tsv"
            write_json(assignment_path, assignment)
            input_path.write_text(
                VIVADO_STA_TSV_HEADER
                + "\n"
                + "\t".join(
                    [
                        "p0".encode().hex(),
                        "clk".encode().hex(),
                        "10",
                        "0",
                        "1",
                        "missing".encode().hex(),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "unknown cut nets"):
                import_vivado_sta_tsv(
                    input_path, assignment_path, root / "out.json"
                )


if __name__ == "__main__":
    unittest.main()
