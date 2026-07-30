from hashlib import sha256
import tempfile
import unittest
from pathlib import Path

from emuflow.architecture import ArchitectureDB
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.vtr_architecture import (
    _tile_templates,
    fetch_pinned_vtr_architecture,
    read_vpr_placement_dimensions,
    run_vtr_architecture_import,
    validate_vtr_architecture_db,
    validate_vtr_timing_db,
    validate_vtr_timing_db_file,
)
from tests.native_build import vtr_architecture_importer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT / "examples/architecture/vtr_k6_heterogeneous_fixture.xml"
)


class VtrArchitectureTest(unittest.TestCase):
    def test_reads_exact_vpr_auto_layout_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            placement = Path(temporary) / "design.place"
            placement.write_text(
                "Netlist_File: design.net\n"
                "Array size: 8 x 11 logic blocks\n\n"
                "#block name\tx\ty\tsubblk\tblock number\n",
                encoding="utf-8",
            )
            dimensions = read_vpr_placement_dimensions(placement)
        self.assertEqual(dimensions, (8, 11))

    def test_rejects_placement_without_array_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            placement = Path(temporary) / "design.place"
            placement.write_text("invalid\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "Array size"):
                read_vpr_placement_dimensions(placement)

    def test_equivalent_sites_are_alternatives_not_additive(self) -> None:
        templates = _tile_templates(
            {
                "tiles": {
                    "logic": {
                        "width": 1,
                        "height": 1,
                        "sub_tiles": [
                            {
                                "name": "logic",
                                "capacity": 2,
                                "pb_type": "wide",
                            },
                            {
                                "name": "logic",
                                "capacity": 2,
                                "pb_type": "narrow",
                            },
                        ],
                    }
                },
                "resources": {
                    "wide": {"LUT6": 1, "DFF": 2},
                    "narrow": {"LUT4": 2, "DFF": 2},
                },
            }
        )
        compatible = [
            bel["compatible_cells"]
            for bel in templates["logic"]["bels"]
        ]
        self.assertEqual(
            sum("DFF" in cells for cells in compatible),
            4,
        )
        self.assertEqual(
            sum("LUT6" in cells for cells in compatible),
            2,
        )

    def test_fetcher_verifies_pinned_open_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "downloaded.xml"
            manifest = root / "manifest.json"

            write_json(
                manifest,
                {
                    "schema": "emuflow.pinned-architecture-source/v1",
                    "name": "fixture",
                    "raw_url": FIXTURE.resolve().as_uri(),
                    "sha256": sha256(FIXTURE.read_bytes()).hexdigest(),
                    "qualification": "academic_open_model",
                },
            )
            report = fetch_pinned_vtr_architecture(output, manifest)
            downloaded = output.read_bytes()

        self.assertEqual(report["status"], "pass")
        self.assertEqual(downloaded, FIXTURE.read_bytes())

    def test_open_xml_imports_architecture_and_timing_databases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            architecture_path = root / "architecture.json"
            timing_path = root / "timing.json"
            report = run_vtr_architecture_import(
                input_path=FIXTURE,
                architecture_output_path=architecture_path,
                timing_output_path=timing_path,
                architecture_id="fixture-k6",
                width=24,
                height=24,
                executable=str(vtr_architecture_importer()),
            )
            architecture = ArchitectureDB.load(architecture_path)
            timing = read_json(timing_path)
            architecture_checked = validate_vtr_architecture_db(architecture)
            timing_checked = validate_vtr_timing_db_file(timing_path)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(architecture_checked["status"], "pass")
        self.assertEqual(architecture_checked["cell_slots"]["LUT6"], 3520)
        self.assertEqual(architecture_checked["cell_slots"]["DFF"], 7040)
        self.assertEqual(
            architecture_checked["cell_slots"]["MULT_9X9"], 60
        )
        self.assertEqual(
            architecture_checked["cell_slots"]["MEM_1024X32_SP"], 9
        )
        self.assertEqual(timing_checked["status"], "pass")
        self.assertEqual(timing_checked["switches"], 2)
        self.assertEqual(timing_checked["segments"], 1)
        self.assertIn("DFF", timing_checked["primitive_cells"])
        self.assertEqual(
            architecture.value["source"]["sha256"],
            timing["source"]["sha256"],
        )

    def test_dimensions_change_capacity_but_not_source_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summaries = []
            timing_hashes = []
            for size in (16, 32):
                architecture_path = root / f"architecture-{size}.json"
                timing_path = root / f"timing-{size}.json"
                run_vtr_architecture_import(
                    input_path=FIXTURE,
                    architecture_output_path=architecture_path,
                    timing_output_path=timing_path,
                    architecture_id="fixture-k6",
                    width=size,
                    height=size,
                    executable=str(vtr_architecture_importer()),
                )
                summaries.append(
                    ArchitectureDB.load(architecture_path).summary()
                )
                timing_hashes.append(read_json(timing_path)["source"]["sha256"])
        self.assertGreater(
            summaries[1]["cell_slots"]["LUT6"],
            summaries[0]["cell_slots"]["LUT6"],
        )
        self.assertEqual(timing_hashes[0], timing_hashes[1])

    def test_tampered_timing_arc_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            architecture_path = root / "architecture.json"
            timing_path = root / "timing.json"
            run_vtr_architecture_import(
                input_path=FIXTURE,
                architecture_output_path=architecture_path,
                timing_output_path=timing_path,
                architecture_id="fixture-k6",
                width=16,
                height=16,
                executable=str(vtr_architecture_importer()),
            )
            timing = read_json(timing_path)
        timing["primitive_arcs"][0]["max_seconds"] = -1.0
        with self.assertRaisesRegex(Exception, "max_seconds"):
            validate_vtr_timing_db(timing)


if __name__ == "__main__":
    unittest.main()
