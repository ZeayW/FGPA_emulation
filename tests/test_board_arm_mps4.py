import tempfile
import unittest
from pathlib import Path

from emuflow.board_arm_mps4 import (
    ARM_MPS4_TRM_URL,
    materialize_arm_mps4_boarddb,
)
from emuflow.bsp import _link_channels, _physical_data_lanes, _validate_anchors
from emuflow.errors import ValidationError
from emuflow.io import read_json
from emuflow.netlist import _build_virtual_anchors
from emuflow.platform import Platform


class ArmMps4BoardTest(unittest.TestCase):
    def test_materializes_documented_three_board_serial_ring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "mps4.json"
            report = materialize_arm_mps4_boarddb(
                output,
                name="mps4_test",
                fabric_clock_mhz=390.625,
                payload_bits_per_lane_per_cycle=64,
                latency_cycles=4,
            )
            document = read_json(output)
            platform = Platform.load(output)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(platform.kind, "hardware")
        self.assertEqual(len(platform.fpgas), 3)
        self.assertEqual(len(platform.links), 3)
        self.assertTrue(
            all(fpga.part == "xcvu13p-fhga2104-1-e" for fpga in platform.fpgas)
        )
        self.assertEqual(platform.fpgas[0].capacity["lut"], 1_728_000)
        self.assertEqual(platform.fpgas[0].capacity["dsp48"], 12_288)
        self.assertTrue(
            all(link.data_lanes_per_direction == 12 for link in platform.links)
        )
        self.assertTrue(
            all(
                link.transport_bits_per_cycle_per_direction == 768
                for link in platform.links
            )
        )
        self.assertTrue(
            all(
                link.raw_bits_per_second_per_direction == 300e9
                for link in platform.links
            )
        )
        bindings = [
            binding
            for link in document["links"]
            for binding in link["endpoint_bindings"]
        ]
        self.assertEqual(
            {(item["fpga"], item["connector"]) for item in bindings},
            {
                (fpga, connector)
                for fpga in ("mps4_1", "mps4_2", "mps4_3")
                for connector in ("J48", "J49")
            },
        )
        self.assertTrue(all(len(item["lanes"]) == 12 for item in bindings))
        first = document["links"][0]["endpoint_bindings"][0]["lanes"][0]
        self.assertEqual(first["tx_package_pins"], {"p": "BD42", "n": "BD43"})
        self.assertEqual(first["rx_package_pins"], {"p": "BC45", "n": "BC46"})
        self.assertEqual(
            document["provenance"]["board_manual"]["url"], ARM_MPS4_TRM_URL
        )
        self.assertEqual(
            document["provenance"]["transport_profile"]["qualification"],
            "configured_model_not_hardware_measured",
        )
        physical_lanes = _physical_data_lanes(platform)
        self.assertEqual(len(physical_lanes), 3 * 12 * 4)
        self.assertTrue(
            all(item["lane_kind"] == "serial_transceiver" for item in physical_lanes)
        )
        self.assertTrue(
            all(
                set(item["required_binding_fields"])
                == {
                    "package_pin_p",
                    "package_pin_n",
                    "connector",
                    "transceiver_site",
                }
                for item in physical_lanes
            )
        )
        channels = _link_channels(platform)
        self.assertEqual(len(channels), 6)
        self.assertTrue(
            all("transceiver_profile" in item["required_binding_fields"] for item in channels)
        )
        anchors = _build_virtual_anchors(
            "mps4_1",
            platform,
            [
                {
                    "id": "endpoint-0",
                    "link": "mps4_b2b_1",
                    "peer": "mps4_2",
                    "kind": "tx",
                    "lane": 767,
                    "slot": 3,
                }
            ],
        )
        anchor = anchors["anchors"][0]
        self.assertEqual(anchor["logical_lane"], 767)
        self.assertEqual(anchor["physical_lane"], 11)
        self.assertEqual(anchor["bit_within_physical_lane"], 63)
        self.assertEqual(
            anchor["required_hardware_binding_fields"],
            [
                "package_pin_p",
                "package_pin_n",
                "connector",
                "transceiver_site",
            ],
        )
        anchor_documents = {
            fpga.id: _build_virtual_anchors(
                fpga.id,
                platform,
                (
                    [
                        {
                            "id": "endpoint-0",
                            "link": "mps4_b2b_1",
                            "peer": "mps4_2",
                            "kind": "tx",
                            "lane": 767,
                            "slot": 3,
                        }
                    ]
                    if fpga.id == "mps4_1"
                    else []
                ),
            )
            for fpga in platform.fpgas
        }
        validated_anchors = _validate_anchors(
            anchor_documents,
            {
                "validation": {
                    "virtual_anchors": 1,
                    "unbound_package_pins": 1,
                }
            },
            platform,
            physical_lanes,
        )
        self.assertEqual(validated_anchors[0]["physical_lane"], 11)
        self.assertEqual(validated_anchors[0]["bit_within_physical_lane"], 63)

    def test_rejects_profile_above_documented_line_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValidationError, "exceeds"):
                materialize_arm_mps4_boarddb(
                    Path(temporary) / "bad.json",
                    name="bad",
                    fabric_clock_mhz=500.0,
                    payload_bits_per_lane_per_cycle=64,
                    latency_cycles=1,
                )


if __name__ == "__main__":
    unittest.main()
