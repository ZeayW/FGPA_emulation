import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.io import write_json
from emuflow.vtr_eblif import emit_vtr_eblif, validate_vtr_eblif_report


def _endpoint(instance, port, bit=0):
    return {"instance": instance, "port": port, "bit": bit}


def _instance(name, cell_type, parameters=None, constants=None):
    return {
        "id": name,
        "name": name,
        "type": cell_type,
        "resources": {},
        "parameters": parameters or {},
        "attributes": {},
        "constant_connections": constants or [],
    }


class VtrEblifTest(unittest.TestCase):
    def test_top_port_aliases_may_share_a_packed_io_block(self) -> None:
        report = {
            "schema": "emuflow.vtr-eblif-report/v1",
            "status": "pass",
            "source_instances": 0,
            "source_inventory": {},
            "memory_atom_expansion": 0,
            "ff_control_luts": 0,
            "emitted_atoms": 0,
            "source_sha256": "a" * 64,
            "output_sha256": "b" * 64,
            "top_ports": [
                {
                    "port": "alias_a",
                    "bit": 0,
                    "direction": "input",
                    "net": "shared_net",
                    "packed_block": "shared_net",
                },
                {
                    "port": "alias_b",
                    "bit": 0,
                    "direction": "input",
                    "net": "shared_net",
                    "packed_block": "shared_net",
                },
            ],
        }

        self.assertEqual(validate_vtr_eblif_report(report)["top_ports"], 2)

    def test_lowering_preserves_logic_and_expands_word_memory(self) -> None:
        instances = [
            _instance("lut", "LUT2", {"INIT": "1000"}),
            _instance(
                "ff",
                "FDRE",
                {"INIT": "0"},
                [
                    {"port": "CE", "bit": 0, "value": "1"},
                    {"port": "R", "bit": 0, "value": "0"},
                ],
            ),
            _instance(
                "mul",
                "VTR_MULTIPLY",
                {"A_WIDTH": 1, "B_WIDTH": 1, "Y_WIDTH": 1},
            ),
            _instance(
                "ram",
                "VTR_SP_RAM",
                {"ADDR_WIDTH": 1, "DATA_WIDTH": 2, "DEPTH": 2},
            ),
        ]
        nets = [
            {
                "id": "a",
                "name": "a",
                "drivers": [_endpoint(None, "a")],
                "sinks": [
                    _endpoint("lut", "I0"),
                    _endpoint("mul", "a"),
                    _endpoint("ram", "addr"),
                ],
                "fanout": 3,
                "cut_class": "primary_input",
            },
            {
                "id": "b",
                "name": "b",
                "drivers": [_endpoint(None, "b")],
                "sinks": [
                    _endpoint("lut", "I1"),
                    _endpoint("mul", "b"),
                    _endpoint("ram", "data", 0),
                    _endpoint("ram", "data", 1),
                ],
                "fanout": 4,
                "cut_class": "primary_input",
            },
            {
                "id": "lut_o",
                "name": "lut_o",
                "drivers": [_endpoint("lut", "O")],
                "sinks": [_endpoint("ff", "D")],
                "fanout": 1,
                "cut_class": "combinational",
            },
            {
                "id": "clk",
                "name": "clk",
                "drivers": [_endpoint(None, "clk")],
                "sinks": [
                    _endpoint("ff", "C"),
                    _endpoint("ram", "clk"),
                ],
                "fanout": 2,
                "cut_class": "clock",
            },
            {
                "id": "we",
                "name": "we",
                "drivers": [_endpoint(None, "we")],
                "sinks": [_endpoint("ram", "we")],
                "fanout": 1,
                "cut_class": "primary_input",
            },
            {
                "id": "q",
                "name": "q",
                "drivers": [_endpoint("ff", "Q")],
                "sinks": [_endpoint(None, "q")],
                "fanout": 1,
                "cut_class": "register_output",
            },
        ]
        value = {
            "schema": "emuflow.emuir/v1",
            "design": {
                "name": "partition",
                "top": "partition",
                "source_format": "test",
            },
            "ports": [
                {
                    "id": name,
                    "name": name,
                    "direction": direction,
                    "width": 1,
                    "clock": name == "clk",
                    "reset": False,
                }
                for name, direction in (
                    ("a", "input"),
                    ("b", "input"),
                    ("clk", "input"),
                    ("we", "input"),
                    ("q", "output"),
                )
            ],
            "instances": instances,
            "nets": nets,
            "clocks": [
                {
                    "id": "clk",
                    "name": "clk",
                    "source_port": "clk",
                    "period_ns": None,
                }
            ],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "partition.json"
            output = root / "partition.eblif"
            write_json(source, value)
            report = emit_vtr_eblif(source, output, root / "report.json")
            text = output.read_text(encoding="utf-8")

        self.assertEqual(report["source_instances"], 4)
        self.assertEqual(report["memory_atom_expansion"], 1)
        self.assertEqual(report["ff_control_luts"], 1)
        self.assertEqual(report["emitted_atoms"], 6)
        self.assertEqual(text.count(".subckt single_port_ram "), 2)
        self.assertEqual(text.count(".subckt multiply "), 1)
        self.assertIn(".latch ", text)
        self.assertEqual(report["validation"]["status"], "pass")

    def test_unsupported_primitive_is_rejected(self) -> None:
        value = {
            "schema": "emuflow.emuir/v1",
            "design": {"name": "bad", "top": "bad", "source_format": "test"},
            "ports": [],
            "instances": [_instance("bad", "CARRY8")],
            "nets": [],
            "clocks": [],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.json"
            write_json(source, value)
            with self.assertRaisesRegex(ValidationError, "CARRY8"):
                emit_vtr_eblif(source, Path(temporary) / "bad.eblif")


if __name__ == "__main__":
    unittest.main()
