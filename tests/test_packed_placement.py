import tempfile
import unittest
from pathlib import Path

from emuflow.architecture import ArchitectureDB
from emuflow.io import read_json
from emuflow.packed_netlist import run_packed_netlist_import
from emuflow.packed_placement import (
    _fixed_multi_instance_placements,
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
    def test_saturated_single_site_resource_is_fixed(self) -> None:
        initial, fixed_types = _fixed_multi_instance_placements(
            {"clusters": [{"id": "memory[0]", "block_type": "memory"}]},
            {"memory[0]": "VPR_memory_0"},
            {"memory": [(3, 4, 0)]},
            {"memory": 1},
        )
        self.assertEqual(fixed_types, ["memory"])
        self.assertEqual(initial, "VPR_memory_0 3 4 0 FIXED\n")

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

    def test_vpr_placement_seeds_movable_openparf_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packed_path, architecture_path = self._inputs(root)
            packed = read_json(packed_path)
            architecture = ArchitectureDB.load(architecture_path)
            clb_site = next(
                site for site in architecture.value["sites"]
                if site["type"] == "clb"
            )
            io_site = next(
                site for site in architecture.value["sites"]
                if site["type"] == "io"
            )
            by_instance = {
                cluster["instance"]: cluster
                for cluster in packed["clusters"]
            }
            seed = root / "seed.place"
            seed.write_text(
                "\n".join(
                    [
                        "Netlist_File: fixture.net Netlist_ID: fixture",
                        "Array size: 12 x 12 logic blocks",
                        "",
                        f"{by_instance['clb[0]']['name']} "
                        f"{clb_site['x']} {clb_site['y']} 0 0 #0",
                        f"{by_instance['io[1]']['name']} "
                        f"{io_site['x']} {io_site['y']} 0 0 #1",
                        f"{by_instance['io[2]']['name']} "
                        f"{io_site['x']} {io_site['y']} 1 0 #2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "bookshelf"
            manifest = export_packed_bookshelf(
                packed_path,
                architecture_path,
                output,
                seed_placement_path=seed,
            )
            initial = (output / "design.pl").read_text(encoding="utf-8")
            config = read_json(output / "openparf.json")

        self.assertEqual(config["random_center_init_flag"], 0)
        self.assertEqual(manifest["seed_placement"], str(seed.resolve()))
        self.assertIn(
            f"{clb_site['x']} {clb_site['y']} 0\n", initial
        )
        self.assertEqual(initial.count(" FIXED\n"), 2)

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
