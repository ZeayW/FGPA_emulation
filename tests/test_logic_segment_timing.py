import tempfile
import unittest
from pathlib import Path

from emuflow.io import write_json
from emuflow.ir import EmuIR
from emuflow.logic_segment_timing import (
    _vivado_object,
    _vpr_atom_pin,
    import_vivado_logic_segment_timing,
    import_vpr_logic_segment_timing,
)


class LogicSegmentTimingTest(unittest.TestCase):
    def test_vivado_pin_mapping_covers_logic_ff_and_memory_endpoints(self):
        ir = EmuIR(
            {
                "schema": "emuflow.emuir/v1",
                "design": {
                    "name": "dut__fpga0",
                    "top": "dut__fpga0",
                    "source_format": "test",
                },
                "ports": [
                    {
                        "id": "input_bus",
                        "name": "input_bus",
                        "direction": "input",
                        "width": 2,
                        "clock": False,
                    }
                ],
                "instances": [
                    {
                        "id": "lut",
                        "name": "lut",
                        "type": "$lut",
                        "resources": {"lut": 1},
                        "parameters": {"WIDTH": 2, "LUT": "4'b1000"},
                        "attributes": {},
                        "constant_connections": [],
                    },
                    {
                        "id": "ff",
                        "name": "ff",
                        "type": "$_DFF_P_",
                        "resources": {"ff": 1},
                        "parameters": {},
                        "attributes": {},
                        "constant_connections": [],
                    },
                    {
                        "id": "ram",
                        "name": "ram",
                        "type": "VTR_DP_RAM",
                        "resources": {"bram": 1},
                        "parameters": {"ADDR_WIDTH": 4, "DATA_WIDTH": 8},
                        "attributes": {},
                        "constant_connections": [],
                    },
                ],
                "nets": [],
                "clocks": [],
                "warnings": [],
            }
        )
        instances = {item["id"]: item for item in ir.value["instances"]}
        pins = {
            "lut": {("A", 0), ("A", 1), ("Y", 0)},
            "ff": {("C", 0), ("D", 0), ("Q", 0)},
            "ram": {
                *(("addr1", bit) for bit in range(4)),
                ("out2", 3),
            },
        }
        self.assertEqual(
            _vivado_object(
                ir,
                {"instance": "lut", "port": "A", "bit": 1},
                pins,
                instances,
            ),
            ("pin", "lut/I1"),
        )
        self.assertEqual(
            _vivado_object(
                ir,
                {"instance": "ff", "port": "D", "bit": 0},
                pins,
                instances,
            ),
            ("pin", "ff/D"),
        )
        self.assertEqual(
            _vivado_object(
                ir,
                {"instance": "ff", "port": "C", "bit": 0},
                pins,
                instances,
            ),
            ("pin", "ff/C"),
        )
        self.assertEqual(
            _vivado_object(
                ir,
                {"instance": "ram", "port": "addr1", "bit": 3},
                pins,
                instances,
            ),
            ("pin", "ram/addr1[3]"),
        )
        self.assertEqual(
            _vivado_object(
                ir,
                {
                    "object": "ram/memory_reg_bram_0/CLKBWRCLK",
                    "instance": "ram",
                    "port": "out2",
                    "bit": 3,
                },
                pins,
                instances,
            ),
            ("pin", "ram/memory_reg_bram_0/CLKBWRCLK"),
        )
        self.assertEqual(
            _vivado_object(
                ir,
                {"instance": None, "port": "input_bus", "bit": 1},
            ),
            ("port", "input_bus[1]"),
        )

    def test_vpr_pin_mapping_covers_logic_ff_and_memory_endpoints(self):
        ir = EmuIR(
            {
                "schema": "emuflow.emuir/v1",
                "design": {
                    "name": "dut__fpga0",
                    "top": "dut__fpga0",
                    "source_format": "test",
                },
                "ports": [],
                "instances": [
                    {
                        "id": "lut",
                        "name": "lut",
                        "type": "$lut",
                        "resources": {"lut": 1},
                        "parameters": {"WIDTH": 2, "LUT": "4'b1000"},
                        "attributes": {},
                        "constant_connections": [],
                    },
                    {
                        "id": "ff",
                        "name": "ff",
                        "type": "$_DFF_P_",
                        "resources": {"ff": 1},
                        "parameters": {},
                        "attributes": {},
                        "constant_connections": [],
                    },
                    {
                        "id": "ram",
                        "name": "ram",
                        "type": "VTR_DP_RAM",
                        "resources": {"bram": 1},
                        "parameters": {"ADDR_WIDTH": 4, "DATA_WIDTH": 8},
                        "attributes": {},
                        "constant_connections": [],
                    },
                ],
                "nets": [],
                "clocks": [],
                "warnings": [],
            }
        )
        index = {"lut": 0, "ff": 1, "ram": 2}
        self.assertEqual(
            _vpr_atom_pin(
                ir, index, {"instance": "lut", "port": "A", "bit": 1}
            ),
            "i0.in[1]",
        )
        self.assertEqual(
            _vpr_atom_pin(
                ir, index, {"instance": "ff", "port": "D", "bit": 0}
            ),
            "i1.D[0]",
        )
        self.assertEqual(
            _vpr_atom_pin(
                ir,
                index,
                {"instance": "ram", "port": "addr1", "bit": 3},
            ),
            "i2__bit0.addr1[3]",
        )

    def test_import_is_identity_and_coverage_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity_path = root / "identity.json"
            write_json(
                identity_path,
                {
                    "schema": "emuflow.logic-segment-identity/v1",
                    "status": "pass",
                    "design": "dut",
                    "platform": "board",
                    "fpga": "fpga0",
                    "provider": "test",
                    "coverage": {
                        "segments": 1,
                        "system_paths": 1,
                        "member_paths": 1,
                        "unsupported_member_paths": 0,
                    },
                    "unsupported_member_paths": [],
                    "segments": [
                        {
                            "id": "logic0",
                            "kind": "launch",
                            "system_path": "path0",
                            "member_path": "member0",
                            "cut_index": 0,
                            "fpga": "fpga0",
                            "replace_tx_endpoint": "tx0",
                            "start_pin": "i0.Q[0]",
                            "end_pin": "out:n0.outpad[0]",
                        }
                    ],
                },
            )
            raw = root / "timing.tsv"
            raw.write_text(
                "endpoint\tkind\tdelay_ns\tstart_pin\tend_pin\n"
                "logic0\tlaunch\t2.5\ti0.Q[0]\tout:n0.outpad[0]\n",
                encoding="utf-8",
            )
            output = root / "timing.json"
            report = import_vpr_logic_segment_timing(
                raw, identity_path, output
            )
            self.assertEqual(report["segments"], 1)
            self.assertEqual(report["maximum_delay_ns"], 2.5)

    def test_vivado_import_is_identity_and_coverage_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity_path = root / "identity.json"
            segment = {
                "id": "logic0",
                "kind": "capture",
                "system_path": "path0",
                "member_path": "member0",
                "cut_index": 1,
                "fpga": "fpga0",
                "replace_tx_endpoint": None,
                "start_pin": "rx_shadow/Q",
                "end_pin": "dut_reg/D",
                "start_object_kind": "pin",
                "end_object_kind": "pin",
            }
            write_json(
                identity_path,
                {
                    "schema": "emuflow.logic-segment-identity/v1",
                    "status": "pass",
                    "design": "dut",
                    "platform": "board",
                    "fpga": "fpga0",
                    "provider": "test",
                    "coverage": {
                        "segments": 1,
                        "system_paths": 1,
                        "member_paths": 1,
                        "unsupported_member_paths": 0,
                    },
                    "unsupported_member_paths": [],
                    "segments": [segment],
                },
            )
            raw = root / "timing.tsv"
            raw.write_text(
                "endpoint_hex\tkind\tdelay_ns\tstart_object_hex\t"
                "end_object_hex\n"
                + "\t".join(
                    (
                        segment["id"].encode().hex(),
                        segment["kind"],
                        "3.25",
                        segment["start_pin"].encode().hex(),
                        segment["end_pin"].encode().hex(),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "timing.json"
            report = import_vivado_logic_segment_timing(
                raw, identity_path, output
            )
            self.assertEqual(report["segments"], 1)
            self.assertEqual(report["maximum_delay_ns"], 3.25)


if __name__ == "__main__":
    unittest.main()
