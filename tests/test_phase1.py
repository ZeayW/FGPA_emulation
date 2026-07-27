import json
import tempfile
import unittest
from pathlib import Path

from emuflow.phase1 import run_phase1


ROOT = Path(__file__).resolve().parents[1]


class Phase1Test(unittest.TestCase):
    def test_pipeline_writes_valid_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            report = run_phase1(
                yosys_json=ROOT / "examples/yosys/counter.json",
                platform_path=(
                    ROOT / "platforms/virtual/xcvu3p_2fpga_p2p.json"
                ),
                output_dir=output,
                top="counter",
                clocks=["clk"],
            )
            self.assertEqual(report["status"], "pass")
            self.assertTrue(all(report["fits_on_fpga"].values()))
            self.assertTrue(report["fits_on_platform"])
            self.assertEqual(report["fit_scope"], "single_fpga")
            for filename in (
                "design.emuir.json",
                "platform.normalized.json",
                "phase1_report.json",
            ):
                self.assertTrue((output / filename).is_file())
                with (output / filename).open("r", encoding="utf-8") as stream:
                    self.assertIsInstance(json.load(stream), dict)

    def test_aggregate_multi_fpga_capacity_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            platform = {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "two_tiny_fpgas",
                    "kind": "virtual",
                },
                "fpgas": [
                    {
                        "id": fpga_id,
                        "part": "test",
                        "utilization_limit": 1.0,
                        "capacity": {"lut": 3, "ff": 3},
                    }
                    for fpga_id in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "abstract",
                        "data_lanes_per_direction": 1,
                        "fabric_clock_mhz": 1,
                        "latency_cycles": 0,
                    }
                ],
            }
            platform_path = root / "platform.json"
            platform_path.write_text(
                json.dumps(platform),
                encoding="utf-8",
            )
            report = run_phase1(
                yosys_json=ROOT / "examples/yosys/counter.json",
                platform_path=platform_path,
                output_dir=root / "phase1",
                top="counter",
                clocks=["clk"],
            )
            self.assertEqual(report["status"], "pass")
            self.assertFalse(any(report["fits_on_fpga"].values()))
            self.assertTrue(report["fits_on_platform"])
            self.assertEqual(report["fit_scope"], "aggregate_platform")


if __name__ == "__main__":
    unittest.main()
