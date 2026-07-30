import json
import stat
import tempfile
import unittest
from pathlib import Path

from emuflow.opensta import (
    DEFAULT_TIMING_MODEL,
    FPGA_TIMING_MODEL_SCHEMA,
    OPENSTA_PROVIDER,
    load_timing_model,
    parse_clock_definitions,
    render_opensta_liberty,
    run_opensta_path_database,
    validate_timing_model_coverage,
)
from emuflow.sta import validate_sta_path_database
from emuflow.verilog import mapped_verilog
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]


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
            )
            checked = validate_sta_path_database(output_path, ir_path)
            artifact = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(report["provider"], OPENSTA_PROVIDER)
        self.assertEqual(
            report["timing_model_qualification"],
            "analytical_uncharacterized",
        )
        self.assertEqual(report["paths"], 1)
        self.assertEqual(checked["status"], "pass")
        self.assertEqual(artifact["source"]["provider"], OPENSTA_PROVIDER)
        self.assertEqual(
            artifact["source"]["timing_model_qualification"],
            "analytical_uncharacterized",
        )


if __name__ == "__main__":
    unittest.main()
