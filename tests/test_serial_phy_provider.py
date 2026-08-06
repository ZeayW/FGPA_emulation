import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from emuflow.board_arm_mps4 import materialize_arm_mps4_boarddb
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.platform import Platform
from emuflow.serial_phy_provider import (
    SERIAL_PHY_PROVIDER_SCHEMA,
    validate_serial_phy_provider,
    validate_serial_phy_provider_file,
)


PROVIDER_SOURCE = """module emuflow_external_serial_clock_reset #(
  parameter integer BOARD_RESET_ACTIVE_LOW = 1
) (input wire refclk_p, input wire refclk_n, input wire board_reset,
   output wire phy_refclk, output wire phy_reset, output wire ready);
  assign phy_refclk = refclk_p;
  assign phy_reset = BOARD_RESET_ACTIVE_LOW ? ~board_reset : board_reset;
  assign ready = 1'b1;
endmodule

module emuflow_external_serial_phy_lane #(
  parameter integer PAYLOAD_WIDTH = 64
) (input wire user_clk, input wire reset, input wire phy_refclk,
   input wire phy_reset, input wire [PAYLOAD_WIDTH-1:0] tx_data,
   output wire [PAYLOAD_WIDTH-1:0] rx_data, output wire txp,
   output wire txn, input wire rxp, input wire rxn, output wire ready);
  assign rx_data = {PAYLOAD_WIDTH{1'b0}};
  assign txp = 1'b0;
  assign txn = 1'b1;
  assign ready = 1'b1;
endmodule
"""


class SerialPhyProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.platform_path = root / "platform.json"
        materialize_arm_mps4_boarddb(
            self.platform_path,
            name="provider_fixture",
            fabric_clock_mhz=50.0,
            payload_bits_per_lane_per_cycle=64,
            latency_cycles=4,
        )
        self.platform = Platform.load(self.platform_path)
        self.source_path = root / "provider.sv"
        self.source_path.write_text(PROVIDER_SOURCE, encoding="utf-8")
        digest = hashlib.sha256(PROVIDER_SOURCE.encode("utf-8")).hexdigest()
        self.manifest_path = root / "provider.json"
        self.manifest = {
            "schema": SERIAL_PHY_PROVIDER_SCHEMA,
            "id": "structural_provider_fixture",
            "qualification": "simulation_only",
            "supported_parts": ["xcvu13p-fhga2104-1-e"],
            "modules": {
                "clock_reset": "emuflow_external_serial_clock_reset",
                "lane": "emuflow_external_serial_phy_lane",
            },
            "source_root": ".",
            "sources": [
                {
                    "path": "provider.sv",
                    "language": "systemverilog",
                    "role": "structural_test_fixture",
                    "sha256": digest,
                }
            ],
            "protocol": {
                "payload_bits_per_lane_per_cycle": 64,
                "user_clock_mhz": 50.0,
                "line_rate_gbps_per_lane": 10.0,
                "encoding": "test_only_no_line_encoding",
                "link_training": "test_only_always_ready",
                "reset_sequence": "test_only_combinational_reset",
            },
            "provenance": {
                "license": "Apache-2.0-test-fixture",
                "upstream": "repository unit test",
            },
        }
        write_json(self.manifest_path, self.manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_validates_editable_source_and_platform_compatibility(self) -> None:
        result = validate_serial_phy_provider(
            self.manifest, self.manifest_path, self.platform
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["editable_sources"], 1)
        self.assertEqual(result["compatibility"]["serial_links"], 3)
        self.assertEqual(result["compatibility"]["status"], "compatible")
        normalized = self.root / "normalized.json"
        report = validate_serial_phy_provider_file(
            self.manifest_path, self.platform_path, normalized
        )
        self.assertEqual(report["provider"], "structural_provider_fixture")
        self.assertEqual(read_json(normalized)["sources"][0]["bytes"], len(
            PROVIDER_SOURCE.encode("utf-8")
        ))

    def test_rejects_tampered_or_opaque_source(self) -> None:
        tampered = copy.deepcopy(self.manifest)
        tampered["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "SHA-256 mismatch"):
            validate_serial_phy_provider(tampered, self.manifest_path)
        opaque_path = self.root / "provider.dcp"
        opaque_path.write_bytes(b"opaque checkpoint")
        opaque = copy.deepcopy(self.manifest)
        opaque["sources"][0] = {
            "path": "provider.dcp",
            "language": "systemverilog",
            "role": "forbidden_binary",
            "sha256": hashlib.sha256(b"opaque checkpoint").hexdigest(),
        }
        with self.assertRaisesRegex(ValidationError, "opaque"):
            validate_serial_phy_provider(opaque, self.manifest_path)

    def test_rejects_missing_module_or_unsupported_part(self) -> None:
        missing = copy.deepcopy(self.manifest)
        self.source_path.write_text("module unrelated; endmodule\n", encoding="utf-8")
        missing["sources"][0]["sha256"] = hashlib.sha256(
            self.source_path.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(ValidationError, "does not define"):
            validate_serial_phy_provider(missing, self.manifest_path)
        self.source_path.write_text(PROVIDER_SOURCE, encoding="utf-8")
        unsupported = copy.deepcopy(self.manifest)
        unsupported["supported_parts"] = ["some-other-part"]
        with self.assertRaisesRegex(ValidationError, "does not support"):
            validate_serial_phy_provider(
                unsupported, self.manifest_path, self.platform
            )

    def test_rejects_protocol_outside_board_contract(self) -> None:
        incompatible = copy.deepcopy(self.manifest)
        incompatible["protocol"]["user_clock_mhz"] = 62.5
        with self.assertRaisesRegex(ValidationError, "user_clock"):
            validate_serial_phy_provider(
                incompatible, self.manifest_path, self.platform
            )
        incompatible = copy.deepcopy(self.manifest)
        incompatible["protocol"]["line_rate_gbps_per_lane"] = 25.1
        with self.assertRaisesRegex(ValidationError, "board_ceiling"):
            validate_serial_phy_provider(
                incompatible, self.manifest_path, self.platform
            )


if __name__ == "__main__":
    unittest.main()
