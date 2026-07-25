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
            for filename in (
                "design.emuir.json",
                "platform.normalized.json",
                "phase1_report.json",
            ):
                self.assertTrue((output / filename).is_file())
                with (output / filename).open("r", encoding="utf-8") as stream:
                    self.assertIsInstance(json.load(stream), dict)


if __name__ == "__main__":
    unittest.main()
