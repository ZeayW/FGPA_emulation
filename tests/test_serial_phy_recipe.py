import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from emuflow.board_arm_mps4 import materialize_arm_mps4_boarddb
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.platform import Platform
from emuflow.serial_phy_recipe import (
    SERIAL_PHY_RECIPE_SCHEMA,
    build_vivado_recipe_tcl,
    validate_serial_phy_recipe,
)


RECIPE = """# editable test recipe
create_ip -name gtwizard_ultrascale -vendor xilinx.com -library ip -module_name test_gty
"""


class SerialPhyRecipeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.recipe_path = self.root / "generate.tcl"
        self.recipe_path.write_text(RECIPE, encoding="utf-8")
        self.manifest_path = self.root / "recipe.json"
        self.manifest = {
            "schema": SERIAL_PHY_RECIPE_SCHEMA,
            "id": "test-vivado-gty-recipe",
            "qualification": "vendor_generated_hardware",
            "generator": "vivado_gtwizard_ultrascale",
            "supported_parts": ["xcvu13p-fhga2104-1-e"],
            "recipe": {
                "path": "generate.tcl",
                "sha256": hashlib.sha256(RECIPE.encode("utf-8")).hexdigest(),
            },
            "expected_ips": ["test_gty"],
            "expected_primitives": {
                "channel": "GTYE4_CHANNEL",
                "common": "GTYE4_COMMON",
            },
            "protocol": {
                "line_rate_gbps_per_lane": 10.3125,
                "reference_clock_mhz": 156.25,
                "encoding": "10GBASE-R_64B66B_ASYNC",
            },
            "provenance": {
                "license": "CERN-OHL-S-2.0",
                "upstream": "https://example.invalid/upstream",
                "revision": "fixture-revision",
            },
        }
        write_json(self.manifest_path, self.manifest)
        self.platform_path = self.root / "platform.json"
        materialize_arm_mps4_boarddb(
            self.platform_path,
            name="recipe_fixture",
            fabric_clock_mhz=50.0,
            payload_bits_per_lane_per_cycle=64,
            latency_cycles=4,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_validates_recipe_and_marks_it_non_open(self) -> None:
        result = validate_serial_phy_recipe(
            self.manifest,
            self.manifest_path,
            Platform.load(self.platform_path),
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["compatibility"]["status"], "compatible")
        normalized = result["normalized"]
        self.assertFalse(
            normalized["open_flow_qualification"][
                "counts_as_open_flow_implementation"
            ]
        )
        self.assertEqual(normalized["recipe"]["bytes"], len(RECIPE.encode()))

    def test_rejects_tampered_or_non_gty_recipe(self) -> None:
        tampered = copy.deepcopy(self.manifest)
        tampered["recipe"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "SHA-256 mismatch"):
            validate_serial_phy_recipe(tampered, self.manifest_path)
        non_gty_text = "puts {not a generator}\n"
        self.recipe_path.write_text(non_gty_text, encoding="utf-8")
        non_gty = copy.deepcopy(self.manifest)
        non_gty["recipe"]["sha256"] = hashlib.sha256(
            non_gty_text.encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ValidationError, "does not create"):
            validate_serial_phy_recipe(non_gty, self.manifest_path)

    def test_rejects_unqualified_or_unsupported_platform(self) -> None:
        wrong_qualification = copy.deepcopy(self.manifest)
        wrong_qualification["qualification"] = "editable_source_hardware"
        with self.assertRaisesRegex(ValidationError, "qualification"):
            validate_serial_phy_recipe(wrong_qualification, self.manifest_path)
        wrong_part = copy.deepcopy(self.manifest)
        wrong_part["supported_parts"] = ["xcvu9p-flga2104-2-e"]
        with self.assertRaisesRegex(ValidationError, "does not support"):
            validate_serial_phy_recipe(
                wrong_part,
                self.manifest_path,
                Platform.load(self.platform_path),
            )

    def test_builds_checked_vivado_driver(self) -> None:
        script = build_vivado_recipe_tcl(
            recipe_path=Path("recipe source.tcl"),
            output_dir=Path("output dir"),
            part="xcvu13p-fhga2104-1-e",
            expected_ips=["z_core", "a_core"],
        )
        self.assertIn("create_project -force", script)
        self.assertIn("source {recipe source.tcl}", script)
        self.assertIn("set expected_ips [lsort [list {a_core} {z_core}]]", script)
        self.assertIn("generate_target all $actual_ips", script)
        self.assertIn("status=pass part=xcvu13p-fhga2104-1-e", script)

    def test_repository_recipe_has_a_hash_bound_v3_adapter(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "providers"
            / "vivado_gty_10g"
            / "recipe.json"
        )
        result = validate_serial_phy_recipe(read_json(path), path)
        normalized = result["normalized"]
        self.assertEqual(
            normalized["provider"]["schema"],
            "emuflow.serial-phy-provider/v3",
        )
        self.assertEqual(normalized["protocol"]["pcs_data_width"], 64)
        self.assertEqual(normalized["protocol"]["pcs_header_width"], 2)
        self.assertEqual(
            normalized["provenance"]["revision"],
            "77320a9471d19c7dd383914bc049e02d9f4f1ffb",
        )


if __name__ == "__main__":
    unittest.main()
