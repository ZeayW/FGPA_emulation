import tempfile
import unittest
from pathlib import Path

from emuflow.io import read_json, write_json
from emuflow.ir import EmuIR
from emuflow.errors import ValidationError
from emuflow.logic_segment_timing import (
    _vivado_object,
    _vpr_atom_pin,
    import_vivado_logic_segment_timing,
    import_vpr_logic_segment_timing,
)
from emuflow.local_path_timing import (
    import_vpr_local_path_timing,
    path_id_set_sha256,
    validate_local_path_timing,
)


class LogicSegmentTimingTest(unittest.TestCase):
    def test_local_path_import_is_source_bound_and_coverage_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity_path = root / "local-identity.json"
            source_ids = ["local-a", "cross-b"]
            record = {
                "id": "local-a",
                "kind": "local",
                "fpga": "fpga0",
                "clock_domain": "clk",
                "clock_period_ns": 10.0,
                "start_pin": "i0.Q[0]",
                "end_pin": "i1.D[0]",
            }
            write_json(
                identity_path,
                {
                    "schema": "emuflow.local-path-identity/v1",
                    "status": "pass",
                    "design": "dut",
                    "fpga": "fpga0",
                    "provider": "test",
                    "source": {
                        "path_database_sha256": "a" * 64,
                        "original_ir_sha256": "b" * 64,
                        "assignment_sha256": "c" * 64,
                        "routes_sha256": "d" * 64,
                        "original_paths": 2,
                        "original_path_ids_sha256": path_id_set_sha256(
                            source_ids
                        ),
                    },
                    "coverage": {"local_paths": 1},
                    "paths": [record],
                },
            )
            raw = root / "local.tsv"
            raw.write_text(
                "endpoint\tkind\tdelay_ns\tstart_pin\tend_pin\n"
                "local-a\tlocal\t3.25\ti0.Q[0]\ti1.D[0]\n",
                encoding="utf-8",
            )
            output = root / "local.json"
            report = import_vpr_local_path_timing(
                raw, identity_path, output
            )
            self.assertEqual(report["local_paths"], 1)
            self.assertEqual(report["maximum_delay_ns"], 3.25)
            database = read_json(output)
            self.assertEqual(
                validate_local_path_timing(database)["status"], "pass"
            )
            raw.write_text(
                "endpoint\tkind\tdelay_ns\tstart_pin\tend_pin\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                import_vpr_local_path_timing(raw, identity_path, output)

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
            missing_raw = root / "missing.tsv"
            missing_raw.write_text(
                "endpoint_hex\tkind\tdelay_ns\tstart_object_hex\t"
                "end_object_hex\n",
                encoding="utf-8",
            )
            partial = import_vivado_logic_segment_timing(
                missing_raw,
                identity_path,
                root / "partial.json",
                qualification="routed-board-integrated-endpoint-chain",
                allow_missing=True,
            )
            self.assertEqual(partial["segments"], 0)
            self.assertEqual(partial["missing_segments"], 1)
            partial_database = read_json(root / "partial.json")
            self.assertEqual(
                partial_database["unmeasured_segments"][0]["id"],
                segment["id"],
            )

    def test_vivado_import_preserves_cone_bound_measurement_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity_path = root / "identity.json"
            segment = {
                "id": "logic0",
                "kind": "launch",
                "system_path": "path0",
                "member_path": "member0",
                "cut_index": 0,
                "fpga": "fpga0",
                "replace_tx_endpoint": "tx0",
                "start_pin": "spurious_ff/Q",
                "end_pin": "tx_port[0]",
                "start_object_kind": "pin",
                "end_object_kind": "port",
                "cone_anchor_object_kind": "pin",
                "cone_anchor_pin": "cut_driver/O",
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
            fields = (
                segment["id"].encode().hex(),
                segment["kind"],
                "3.125",
                segment["start_pin"].encode().hex(),
                segment["end_pin"].encode().hex(),
                "cut-net-cone-upper-bound",
                "real_ff/C".encode().hex(),
                "pcs_fifo/D".encode().hex(),
            )
            raw = root / "timing.tsv"
            raw.write_text(
                "endpoint_hex\tkind\tdelay_ns\tstart_object_hex\t"
                "end_object_hex\tmeasurement\tactual_start_object_hex\t"
                "actual_end_object_hex\n"
                + "\t".join(fields)
                + "\n",
                encoding="utf-8",
            )
            output = root / "timing.json"
            report = import_vivado_logic_segment_timing(
                raw, identity_path, output
            )
            self.assertEqual(report["cone_bound_segments"], 1)
            database = read_json(output)
            self.assertEqual(
                database["segments"][0]["measurement"],
                "cut-net-cone-upper-bound",
            )
            self.assertEqual(
                database["segments"][0]["actual_start_object"],
                "real_ff/C",
            )


if __name__ == "__main__":
    unittest.main()
