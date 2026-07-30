import tempfile
import unittest
from pathlib import Path

from emuflow.architecture import ArchitectureDB
from emuflow.io import read_json
from emuflow.packed_netlist import run_packed_netlist_import
from emuflow.packed_placement import (
    emit_vpr_place,
    export_packed_bookshelf,
)
from emuflow.vtr_architecture import run_vtr_architecture_import
from tests.native_build import (
    vpr_packed_netlist_importer,
    vtr_architecture_importer,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_XML = (
    ROOT / "examples/architecture/vtr_k6_heterogeneous_fixture.xml"
)
PACKED_FIXTURE = ROOT / "examples/physical/vpr_packed_fixture.net"


class PackedPlacementTest(unittest.TestCase):
    def _inputs(self, root: Path):
        architecture_path = root / "architecture.json"
        run_vtr_architecture_import(
            input_path=ARCHITECTURE_XML,
            architecture_output_path=architecture_path,
            timing_output_path=root / "timing.json",
            architecture_id="fixture",
            width=12,
            height=12,
            executable=str(vtr_architecture_importer()),
        )
        packed_path = root / "packed.json"
        run_packed_netlist_import(
            PACKED_FIXTURE,
            packed_path,
            executable=str(vpr_packed_netlist_importer()),
        )
        packed = read_json(packed_path)
        packed["source"]["architecture_id"] = (
            "SHA256:"
            + ArchitectureDB.load(architecture_path).value["source"]["sha256"]
        )
        from emuflow.io import write_json

        write_json(packed_path, packed)
        return packed_path, architecture_path

    def test_cluster_bookshelf_uses_exact_vtr_site_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packed_path, architecture_path = self._inputs(root)
            output = root / "bookshelf"
            manifest = export_packed_bookshelf(
                packed_path, architecture_path, output
            )
            sites = (output / "design.scl").read_text(encoding="utf-8")
            nodes = (output / "design.nodes").read_text(encoding="utf-8")
            config = read_json(output / "openparf.json")

        self.assertEqual(manifest["status"], "pass")
        self.assertEqual(manifest["clusters"], 3)
        self.assertEqual(manifest["fixed_multi_instance_types"], ["io"])
        self.assertIn("SITE io\n  io 8\nEND SITE", sites)
        self.assertIn("SITE clb\n  clb 1\nEND SITE", sites)
        self.assertIn("VPR_clb", nodes)
        self.assertEqual(config["resource_categories"]["clb"], "SSSIR")
        self.assertEqual(config["resource_categories"]["IO"], "SSMIR")
        self.assertEqual(config["generic_cluster_placement_flag"], 1)
        self.assertEqual(config["logic_area_type_names"], ["clb"])

    def test_legal_cluster_placement_emits_vpr_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packed_path, architecture_path = self._inputs(root)
            output = root / "bookshelf"
            export_packed_bookshelf(
                packed_path, architecture_path, output
            )
            name_map = read_json(output / "name_map.json")
            architecture = ArchitectureDB.load(architecture_path)
            sites = architecture.value["sites"]
            clb_site = next(site for site in sites if site["type"] == "clb")
            io_sites = [site for site in sites if site["type"] == "io"]
            safe = {
                entry["vpr"]: entry["openparf"]
                for entry in name_map["clusters"]
            }
            placement = root / "placed.pl"
            placement.write_text(
                "\n".join(
                    [
                        f"{safe['clb[0]']} {clb_site['x']} "
                        f"{clb_site['y']} 0",
                        f"{safe['io[1]']} {io_sites[0]['x']} "
                        f"{io_sites[0]['y']} 0 FIXED",
                        f"{safe['io[2]']} {io_sites[1]['x']} "
                        f"{io_sites[1]['y']} 0 FIXED",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            vpr_place = root / "fixture.place"
            report = emit_vpr_place(
                packed_path,
                architecture_path,
                output / "name_map.json",
                placement,
                vpr_place,
            )
            text = vpr_place.read_text(encoding="utf-8")

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["clusters"], 3)
        self.assertIn("Array size: 12 x 12 logic blocks", text)
        self.assertIn("\t#0", text)
        self.assertIn("\t#1", text)
        self.assertIn("\t#2", text)


if __name__ == "__main__":
    unittest.main()
