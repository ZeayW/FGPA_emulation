import copy
import tempfile
import unittest
from pathlib import Path

from emuflow.board_arm_mps4 import materialize_arm_mps4_boarddb
from emuflow.board_support import (
    BOARD_SUPPORT_OVERLAY_SCHEMA,
    validate_board_support_overlay,
    validate_board_support_overlay_file,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.platform import Platform


class BoardSupportOverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.platform_path = root / "platform.json"
        materialize_arm_mps4_boarddb(
            self.platform_path,
            name="mps4_overlay_fixture",
            fabric_clock_mhz=50.0,
            payload_bits_per_lane_per_cycle=64,
            latency_cycles=4,
        )
        self.platform = Platform.load(self.platform_path)
        self.overlay = {
            "schema": BOARD_SUPPORT_OVERLAY_SCHEMA,
            "platform": self.platform.name,
            "qualification": "user_supplied_unverified",
            "provenance": {
                "sources": [
                    {
                        "title": "Private board schematic fixture",
                        "uri": "user://board-schematic",
                        "locator": "sheet GT clocks and reset",
                    }
                ]
            },
            "reference_clocks": [
                {
                    "id": "mps4_1_refclk0",
                    "fpga": "mps4_1",
                    "board_service": "b2b_mgt_refclk_pool",
                    "selected_signal": "B2B_CLK[0]",
                    "package_pins": {"p": "REFP0", "n": "REFN0"},
                    "frequency_mhz": 156.25,
                    "frequency_basis": "documented",
                }
            ],
            "resets": [
                {
                    "id": "mps4_1_cold_reset",
                    "fpga": "mps4_1",
                    "board_service": "cb_npor",
                    "package_pin": "RST0",
                    "iostandard": "LVCMOS18",
                }
            ],
            "transceiver_sites": [
                {
                    "fpga": "mps4_1",
                    "link": "mps4_b2b_1",
                    "connector": "J49",
                    "mgt_group": "MGT0",
                    "physical_lane": 0,
                    "site": "GTYE4_CHANNEL_X0Y0",
                    "reference_clock_binding": "mps4_1_refclk0",
                    "reset_binding": "mps4_1_cold_reset",
                }
            ],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_validates_and_normalizes_explicit_overlay(self) -> None:
        result = validate_board_support_overlay(self.overlay, self.platform)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["hardware_qualification"], "unverified")
        self.assertEqual(result["reference_clock_bindings"], 1)
        self.assertEqual(result["reset_bindings"], 1)
        self.assertEqual(result["transceiver_site_bindings"], 1)
        site = result["normalized"]["transceiver_sites"][0]
        self.assertEqual(site["connector"], "J49")
        self.assertEqual(site["reference_clock_binding"], "mps4_1_refclk0")

    def test_file_api_writes_deterministic_normalized_overlay(self) -> None:
        root = Path(self.temporary.name)
        source = root / "overlay.json"
        normalized = root / "normalized.json"
        write_json(source, self.overlay)
        report = validate_board_support_overlay_file(
            self.platform_path, source, normalized
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(
            read_json(normalized),
            validate_board_support_overlay(self.overlay, self.platform)[
                "normalized"
            ],
        )

    def test_rejects_clock_outside_documented_pool(self) -> None:
        corrupted = copy.deepcopy(self.overlay)
        corrupted["reference_clocks"][0]["selected_signal"] = "B2B_CLK[10]"
        with self.assertRaisesRegex(ValidationError, "out of range"):
            validate_board_support_overlay(corrupted, self.platform)

    def test_rejects_connector_or_reference_clock_mismatch(self) -> None:
        corrupted = copy.deepcopy(self.overlay)
        corrupted["transceiver_sites"][0]["connector"] = "J48"
        with self.assertRaisesRegex(ValidationError, "transceiver-site"):
            validate_board_support_overlay(corrupted, self.platform)
        corrupted = copy.deepcopy(self.overlay)
        corrupted["reference_clocks"][0]["fpga"] = "mps4_2"
        with self.assertRaisesRegex(ValidationError, "transceiver-site"):
            validate_board_support_overlay(corrupted, self.platform)

    def test_rejects_service_pin_collision(self) -> None:
        corrupted = copy.deepcopy(self.overlay)
        corrupted["resets"][0]["package_pin"] = "REFP0"
        with self.assertRaisesRegex(ValidationError, "reset binding"):
            validate_board_support_overlay(corrupted, self.platform)
        corrupted = copy.deepcopy(self.overlay)
        corrupted["resets"][0]["package_pin"] = "BD42"
        with self.assertRaisesRegex(ValidationError, "reset binding"):
            validate_board_support_overlay(corrupted, self.platform)


if __name__ == "__main__":
    unittest.main()
