import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.platform import Platform


ROOT = Path(__file__).resolve().parents[1]


class PlatformTest(unittest.TestCase):
    def test_reference_platform(self) -> None:
        platform = Platform.load(
            ROOT / "platforms/virtual/xcvu3p_2fpga_p2p.json"
        )
        self.assertEqual(platform.name, "virtual_xcvu3p_2fpga_p2p")
        self.assertEqual(len(platform.fpgas), 2)
        self.assertEqual(len(platform.links), 1)
        self.assertEqual(platform.fpgas[0].effective_capacity["lut"], 295560)
        self.assertAlmostEqual(
            platform.links[0].raw_bits_per_second_per_direction,
            8_000_000_000.0,
        )

    def test_eight_fpga_scale_platform(self) -> None:
        platform = Platform.load(
            ROOT / "platforms/virtual/xcvu3p_8fpga_mesh.json"
        )
        self.assertEqual(platform.name, "virtual_xcvu3p_8fpga_mesh")
        self.assertEqual(len(platform.fpgas), 8)
        self.assertEqual(len(platform.links), 10)
        self.assertEqual(
            sum(fpga.effective_capacity["lut"] for fpga in platform.fpgas),
            2_364_480,
        )

    def test_unknown_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown FPGA IDs"):
            Platform.from_dict(
                {
                    "schema": "emuflow.boarddb/v1",
                    "platform": {"name": "bad", "kind": "virtual"},
                    "fpgas": [
                        {
                            "id": "fpga0",
                            "part": "xcvu3p",
                            "utilization_limit": 0.75,
                            "capacity": {"lut": 10},
                        }
                    ],
                    "links": [
                        {
                            "id": "bad_link",
                            "endpoints": ["fpga0", "fpga1"],
                            "direction": "full_duplex",
                            "mode": "abstract",
                            "data_lanes_per_direction": 1,
                            "fabric_clock_mhz": 1,
                            "latency_cycles": 0,
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
