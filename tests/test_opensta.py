import json
import stat
import tempfile
import unittest
from pathlib import Path

from emuflow.opensta import (
    DEFAULT_TIMING_MODEL,
    FPGA_TIMING_MODEL_SCHEMA,
    FPGA_TIMING_MODEL_SCHEMA_V2,
    OPENSTA_PROVIDER,
    build_vtr_opensta_timing_model,
    load_timing_model,
    parse_clock_definitions,
    render_opensta_liberty,
    run_opensta_path_database,
    validate_timing_model_coverage,
)
from emuflow.sta import validate_sta_path_database
from emuflow.verilog import mapped_verilog
from emuflow.vtr_architecture import run_vtr_architecture_import
from emuflow.yosys import import_yosys_json
from tests.native_build import vtr_architecture_importer


ROOT = Path(__file__).resolve().parents[1]
VTR_FIXTURE = (
    ROOT / "examples" / "architecture" / "vtr_k6_heterogeneous_fixture.xml"
)


class OpenStaProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )

    def test_open_model_is_explicitly_uncharacterized(self) -> None:
        model = load_timing_model(DEFAULT_TIMING_MODEL)
        self.assertEqual(model["schema"], FPGA_TIMING_MODEL_SCHEMA)
        self.assertEqual(
            model["source"]["qualification"],
            "analytical_uncharacterized",
        )
        coverage = validate_timing_model_coverage(self.ir, model)
        self.assertEqual(coverage["status"], "pass")
        liberty = render_opensta_liberty(model)
        self.assertIn("cell (LUT6)", liberty)
        self.assertIn("cell (FDRE)", liberty)
        self.assertIn("timing_type : setup_rising;", liberty)
        self.assertIn("timing_type : rising_edge;", liberty)
        timing_netlist = mapped_verilog(self.ir, timing_only=True)
        self.assertNotIn("input wire", timing_netlist)
        self.assertNotIn("KEEP =", timing_netlist)
        self.assertNotIn("#(", timing_netlist)

    def test_clock_definitions_are_strict(self) -> None:
        self.assertEqual(
            parse_clock_definitions(["clk=10", "aux=4.5"]),
            {"clk": 10.0, "aux": 4.5},
        )
        with self.assertRaisesRegex(Exception, "duplicate clock"):
            parse_clock_definitions(["clk=10", "clk=5"])
        with self.assertRaisesRegex(Exception, "expected CLOCK"):
            parse_clock_definitions(["clk"])

    def test_path_export_supports_directed_cut_net_queries(self) -> None:
        script = (
            ROOT / "scripts/opensta/export_timing_path_database.tcl"
        ).read_text(encoding="utf-8")
        self.assertIn("-group_count $max_paths", script)
        self.assertIn("EMUFLOW_STA_THROUGH_NETS", script)
        self.assertIn("get_pins -quiet -of_objects $through_net", script)
        self.assertIn("foreach through_pin $through_pins", script)
        self.assertIn("-from [list $through_pin]", script)
        self.assertIn("-to [list $through_pin]", script)
        self.assertIn("-endpoint_count 1", script)

    def test_vtr_timing_db_builds_scalarized_opensta_model(self) -> None:
        source = {
            "creator": "OpenSTA VTR timing test",
            "modules": {
                "top": {
                    "attributes": {"top": "1"},
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "a": {"direction": "input", "bits": [3, 4]},
                        "q": {"direction": "output", "bits": [7]},
                    },
                    "cells": {
                        "lut": {
                            "type": "$lut",
                            "parameters": {"WIDTH": "10", "LUT": "0110"},
                            "port_directions": {
                                "A": "input",
                                "Y": "output",
                            },
                            "connections": {"A": [3, 4], "Y": [5]},
                        },
                        "ff": {
                            "type": "$_DFF_P_",
                            "parameters": {},
                            "port_directions": {
                                "C": "input",
                                "D": "input",
                                "Q": "output",
                            },
                            "connections": {"C": [2], "D": [5], "Q": [7]},
                        },
                    },
                    "netnames": {
                        "clk": {"bits": [2]},
                        "a": {"bits": [3, 4]},
                        "n": {"bits": [5]},
                        "q": {"bits": [7]},
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            yosys_path = root / "mapped.json"
            architecture_path = root / "architecture.json"
            timing_path = root / "timing.json"
            model_path = root / "model.json"
            yosys_path.write_text(json.dumps(source), encoding="utf-8")
            ir = import_yosys_json(yosys_path, top="top", clocks=["clk"])
            run_vtr_architecture_import(
                input_path=VTR_FIXTURE,
                architecture_output_path=architecture_path,
                timing_output_path=timing_path,
                architecture_id="fixture-k6",
                width=24,
                height=24,
                executable=str(vtr_architecture_importer()),
            )
            model, cell_types = build_vtr_opensta_timing_model(
                ir, timing_path, model_path
            )
            liberty = render_opensta_liberty(model)
            verilog = mapped_verilog(
                ir,
                timing_only=True,
                timing_cell_types=cell_types,
            )
        self.assertEqual(model["schema"], FPGA_TIMING_MODEL_SCHEMA_V2)
        self.assertEqual(
            model["source"]["qualification"], "academic_open_model"
        )
        self.assertGreater(
            model["source"]["sink_interconnect_delay_ns"], 0.0
        )
        self.assertIn("cell (EMUFLOW_VTR_LUT2)", liberty)
        self.assertIn("cell (EMUFLOW_VTR_DFF)", liberty)
        self.assertIn("pin (A__0)", liberty)
        self.assertIn(".\\A__1 ", verilog)

    def test_vtr_timing_db_normalizes_xilinx_lut_and_ff_names(self) -> None:
        source = {
            "creator": "OpenSTA Xilinx primitive normalization test",
            "modules": {
                "top": {
                    "attributes": {"top": "1"},
                    "ports": {
                        "clk": {"direction": "input", "bits": [2]},
                        "a": {"direction": "input", "bits": [3, 4, 5]},
                        "q": {"direction": "output", "bits": [9, 10]},
                    },
                    "cells": {
                        "lut": {
                            "type": "LUT3",
                            "parameters": {},
                            "port_directions": {
                                "I0": "input",
                                "I1": "input",
                                "I2": "input",
                                "O": "output",
                            },
                            "connections": {
                                "I0": [3],
                                "I1": [4],
                                "I2": [5],
                                "O": [6],
                            },
                        },
                        "ff_clear": {
                            "type": "FDCE",
                            "parameters": {},
                            "port_directions": {
                                "C": "input",
                                "CE": "input",
                                "CLR": "input",
                                "D": "input",
                                "Q": "output",
                            },
                            "connections": {
                                "C": [2],
                                "CE": ["1"],
                                "CLR": ["0"],
                                "D": [6],
                                "Q": [9],
                            },
                        },
                        "ff_reset": {
                            "type": "FDRE",
                            "parameters": {},
                            "port_directions": {
                                "C": "input",
                                "CE": "input",
                                "D": "input",
                                "Q": "output",
                                "R": "input",
                            },
                            "connections": {
                                "C": [2],
                                "CE": ["1"],
                                "D": [6],
                                "Q": [10],
                                "R": ["0"],
                            },
                        },
                    },
                    "netnames": {
                        "clk": {"bits": [2]},
                        "a": {"bits": [3, 4, 5]},
                        "n": {"bits": [6]},
                        "q": {"bits": [9, 10]},
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            yosys_path = root / "mapped.json"
            architecture_path = root / "architecture.json"
            timing_path = root / "timing.json"
            yosys_path.write_text(json.dumps(source), encoding="utf-8")
            ir = import_yosys_json(yosys_path, top="top", clocks=["clk"])
            run_vtr_architecture_import(
                input_path=VTR_FIXTURE,
                architecture_output_path=architecture_path,
                timing_output_path=timing_path,
                architecture_id="fixture-k6",
                width=24,
                height=24,
                executable=str(vtr_architecture_importer()),
            )
            model, cell_types = build_vtr_opensta_timing_model(ir, timing_path)
        self.assertEqual(cell_types["lut"], "EMUFLOW_VTR_LUT3")
        self.assertEqual(cell_types["ff_clear"], "EMUFLOW_VTR_FDCE")
        self.assertEqual(cell_types["ff_reset"], "EMUFLOW_VTR_FDRE")
        self.assertIn("EMUFLOW_VTR_LUT3", model["cells"])
        self.assertIn("EMUFLOW_VTR_FDCE", model["cells"])
        self.assertIn("EMUFLOW_VTR_FDRE", model["cells"])
        self.assertEqual(model["cells"]["EMUFLOW_VTR_FDCE"]["clock"], "C")

    def test_runner_imports_and_independently_checks_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir_path = root / "ir.json"
            output_path = root / "database.json"
            log_path = root / "opensta.log"
            executable = root / "fake-openroad"
            ir_path.write_text(
                json.dumps(self.ir.value), encoding="utf-8"
            )
            executable.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path

net_map = Path(os.environ["EMUFLOW_STA_NET_MAP"]).read_text().splitlines()
mapped_hex, emuir_hex = net_map[1].split("\\t")
path_id = "fake opensta path".encode().hex()
clock = "clk".encode().hex()
header = (
    "path_id_hex\\tclock_domain_hex\\tclock_period_ns\\t"
    "slack_ns\\tfixed_delay_ns\\tpath_nets_hex"
)
Path(os.environ["EMUFLOW_STA_OUTPUT"]).write_text(
    header + "\\n"
    + f"{path_id}\\t{clock}\\t10\\t9.5\\t0.5\\t{emuir_hex}\\n"
)
print("fake OpenSTA pass")
""",
                encoding="utf-8",
            )
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            report = run_opensta_path_database(
                ir_path=ir_path,
                output_path=output_path,
                clocks={"clk": 10.0},
                executable=str(executable),
                max_paths=8,
                log_path=log_path,
                through_nets=[self.ir.value["nets"][0]["id"]],
            )
            checked = validate_sta_path_database(output_path, ir_path)
            artifact = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["provider"], OPENSTA_PROVIDER)
        self.assertEqual(
            report["timing_model_qualification"],
            "analytical_uncharacterized",
        )
        self.assertEqual(report["paths"], 1)
        self.assertEqual(report["max_paths"], 8)
        self.assertEqual(
            report["through_nets"], [self.ir.value["nets"][0]["id"]]
        )
        self.assertFalse(report["path_limit_reached"])
        self.assertEqual(checked["status"], "pass")
        self.assertEqual(artifact["source"]["provider"], OPENSTA_PROVIDER)
        self.assertEqual(
            artifact["source"]["timing_model_qualification"],
            "analytical_uncharacterized",
        )


if __name__ == "__main__":
    unittest.main()
