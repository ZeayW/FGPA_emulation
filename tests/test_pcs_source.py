import hashlib
import unittest
from pathlib import Path

from emuflow.io import read_json


ROOT = Path(__file__).resolve().parents[1]
CORUNDUM = ROOT / "engines/corundum_eth"


class PcsSourceTest(unittest.TestCase):
    def test_imported_corundum_sources_match_pinned_manifest(self) -> None:
        manifest = read_json(CORUNDUM / "SOURCE_MANIFEST.json")
        self.assertEqual(
            manifest["revision"],
            "1ca0151b97af85aa5dd306d74b6bcec65904d2ce",
        )
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["modifications"], [])
        for relative, expected in manifest["files"].items():
            source = CORUNDUM / relative
            self.assertTrue(source.is_file(), relative)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                expected,
                relative,
            )

    def test_first_party_record_layer_has_explicit_cdc_and_dejitter(self) -> None:
        pcs = ROOT / "rtl/pcs"
        required = {
            "emuflow_xgmii_record_framer.sv",
            "emuflow_xgmii_record_deframer.sv",
            "emuflow_10g_pcs_record_link.sv",
            "emuflow_record_async_fifo.sv",
            "emuflow_10g_pcs_cdc_adapter.sv",
            "emuflow_record_dejitter_buffer.sv",
        }
        self.assertTrue(required.issubset({path.name for path in pcs.glob("*.sv")}))


if __name__ == "__main__":
    unittest.main()
