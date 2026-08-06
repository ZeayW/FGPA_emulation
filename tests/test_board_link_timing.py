import copy
import unittest

from emuflow.board_link_timing import (
    build_board_link_timing_model,
    validate_board_link_timing,
)
from emuflow.errors import ValidationError
from emuflow.platform import Platform


class BoardLinkTimingTest(unittest.TestCase):
    def setUp(self):
        self.platform = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "two-fpga",
                    "kind": "virtual",
                    "description": "test platform",
                },
                "fpgas": [
                    {
                        "id": "fpga0",
                        "part": "virtual-a",
                        "utilization_limit": 0.8,
                        "capacity": {
                            "lut": 100,
                            "ff": 100,
                            "bram": 1,
                            "dsp": 1,
                            "io": 64,
                            "other": 10,
                        },
                    },
                    {
                        "id": "fpga1",
                        "part": "virtual-b",
                        "utilization_limit": 0.8,
                        "capacity": {
                            "lut": 100,
                            "ff": 100,
                            "bram": 1,
                            "dsp": 1,
                            "io": 64,
                            "other": 10,
                        },
                    },
                ],
                "links": [
                    {
                        "id": "link0",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "serial",
                        "data_lanes_per_direction": 1,
                        "payload_bits_per_lane_per_cycle": 64,
                        "fabric_clock_mhz": 250.0,
                        "latency_cycles": 4,
                    }
                ],
            }
        )

    def test_boarddb_model_has_exact_directed_coverage(self):
        database = build_board_link_timing_model(self.platform)
        result = validate_board_link_timing(database, self.platform)
        self.assertEqual(result["directed_links"], 2)
        self.assertEqual(result["maximum_delay_bound_ns"], 16.0)
        self.assertEqual(result["model_only_links"], 2)
        self.assertFalse(result["final_link_timing_signoff"])

    def test_measured_signoff_requires_evidence_on_every_direction(self):
        database = build_board_link_timing_model(self.platform)
        for record in database["links"]:
            record["qualification"] = "measured-upper-bound"
            record["source"] = {
                "kind": "hardware-measurement",
                "reference": "lab-capture-sha256:0123456789abcdef",
                "observations": 1000,
            }
        database["final_link_timing_signoff"] = True
        result = validate_board_link_timing(database, self.platform)
        self.assertTrue(result["final_link_timing_signoff"])

        broken = copy.deepcopy(database)
        broken["links"][0]["latency_cycles"] = 5
        with self.assertRaisesRegex(ValidationError, "contract"):
            validate_board_link_timing(broken, self.platform)


if __name__ == "__main__":
    unittest.main()
