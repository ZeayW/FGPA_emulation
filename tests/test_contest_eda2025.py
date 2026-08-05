import json
import tempfile
import unittest
from pathlib import Path

from emuflow.contest_eda2025 import (
    evaluate_eda2025_routes,
    import_eda2025_instance,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json
from emuflow.phase4 import run_phase4
from tests.native_build import tlr_router


INFO = """\
F1 3
F2 3
F3 3
F4 3
"""

NETS = """\
g1 1 g2 g3
g4 1 g7
g5 1 g6
"""

TOPOLOGY = """\
F1: 0,1,0,1
F2: 1,0,1,0
F3: 0,1,0,1
F4: 1,0,1,0
"""

ASSIGNMENT = """\
F1: g2 g4
F2: g7
F3: g1 g6
F4: g3 g5
"""


class Eda2025ContestAdapterTest(unittest.TestCase):
    def _import(self, root: Path):
        source = root / "source"
        source.mkdir()
        files = {
            "info": ("design.info", INFO),
            "net": ("design.net", NETS),
            "topology": ("design.topo", TOPOLOGY),
            "assignment": ("design.fpga.out", ASSIGNMENT),
        }
        paths = {}
        for key, (name, content) in files.items():
            paths[key] = source / name
            paths[key].write_text(content, encoding="utf-8")
        output = root / "normalized"
        report = import_eda2025_instance(
            info_path=paths["info"],
            net_path=paths["net"],
            topology_path=paths["topology"],
            assignment_path=paths["assignment"],
            output_dir=output,
            name="eda2025_sample",
        )
        return report, output

    def test_published_sample_runs_through_native_cpp_router_and_checker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            imported, normalized = self._import(root)
            self.assertEqual(imported["fpgas"], 4)
            self.assertEqual(imported["nets"], 3)
            self.assertEqual(imported["cut_nets"], 3)

            routed = root / "routed"
            phase4 = run_phase4(
                assignment_path=normalized / "partition_assignment.json",
                platform_path=normalized / "boarddb.json",
                output_dir=routed,
                constraints_path=normalized / "route_constraints.json",
                timing_paths_path=normalized / "contest_timing_paths.json",
                router=str(tlr_router()),
            )
            self.assertEqual(phase4["status"], "pass")
            self.assertEqual(
                phase4["provider"],
                "timing-aware-route-tdm-cooptimized-v1",
            )
            routes_artifact = read_json(routed / "routes.json")
            opposing_pair = next(
                record
                for record in routes_artifact["link_utilization"]
                if record["link"] == "contest_link_002_003"
            )
            self.assertEqual(
                opposing_pair["key"], "contest_link_002_003:shared"
            )
            self.assertEqual(opposing_pair["used_bits"], 2)

            evaluation = evaluate_eda2025_routes(
                normalized / "contest_instance.json",
                routed / "routes.json",
                runtime_seconds=18.0,
                official_output_dir=root / "official",
            )
            self.assertEqual(evaluation["status"], "pass")
            self.assertEqual(evaluation["metrics"]["routed_cut_nets"], 3)
            self.assertEqual(evaluation["metrics"]["max_tdm_ratio"], 8)
            self.assertAlmostEqual(
                evaluation["metrics"]["worst_path_delay_ns"], 71.2
            )
            self.assertAlmostEqual(
                evaluation["metrics"]["contest_score"],
                71.2 * (1.0 + 0.2 * 18.0 / 3600.0),
            )
            official_routes = (root / "official" / "design.route.out").read_text()
            self.assertEqual(official_routes.count("[net "), 3)
            self.assertIn("[net 1]", official_routes)
            self.assertEqual(
                (root / "official" / "design.newtopo").read_text(),
                TOPOLOGY,
            )

    def test_checker_rejects_route_on_disconnected_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, normalized = self._import(root)
            routes = {
                "schema": "emuflow.system-routes/v1",
                "routes": [
                    {
                        "id": "d000000",
                        "net": "net_000001",
                        "source": "F3",
                        "sinks": ["F1", "F4"],
                        "tree_edges": [
                            {"link": "invented", "from": "F3", "to": "F1"},
                            {"link": "x", "from": "F3", "to": "F4"},
                        ],
                    },
                    {
                        "id": "d000001",
                        "net": "net_000002",
                        "source": "F1",
                        "sinks": ["F2"],
                        "tree_edges": [
                            {"link": "x", "from": "F1", "to": "F2"}
                        ],
                    },
                    {
                        "id": "d000002",
                        "net": "net_000003",
                        "source": "F4",
                        "sinks": ["F3"],
                        "tree_edges": [
                            {"link": "x", "from": "F4", "to": "F3"}
                        ],
                    },
                ],
            }
            routes_path = root / "routes.json"
            routes_path.write_text(json.dumps(routes), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "disconnected FPGA pair"):
                evaluate_eda2025_routes(
                    normalized / "contest_instance.json", routes_path
                )

    def test_import_rejects_non_unit_net_weight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, normalized = self._import(root)
            del normalized
            bad_net = root / "source" / "design.net"
            bad_net.write_text("g1 2 g2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "unit net weights"):
                import_eda2025_instance(
                    info_path=root / "source" / "design.info",
                    net_path=bad_net,
                    topology_path=root / "source" / "design.topo",
                    assignment_path=root / "source" / "design.fpga.out",
                    output_dir=root / "bad",
                    name="bad",
                )

    def test_checker_enforces_published_topology_change_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, normalized = self._import(root)
            routed = root / "routed"
            run_phase4(
                assignment_path=normalized / "partition_assignment.json",
                platform_path=normalized / "boarddb.json",
                output_dir=routed,
                constraints_path=normalized / "route_constraints.json",
                timing_paths_path=normalized / "contest_timing_paths.json",
                router=str(tlr_router()),
            )
            changed = root / "changed.topo"
            changed.write_text(
                "F1: 0,0,1,1\n"
                "F2: 0,0,1,0\n"
                "F3: 1,1,0,1\n"
                "F4: 1,0,1,0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "topology changes"):
                evaluate_eda2025_routes(
                    normalized / "contest_instance.json",
                    routed / "routes.json",
                    new_topology_path=changed,
                )

    def test_normalized_artifacts_are_self_describing(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, normalized = self._import(Path(temporary))
            instance = read_json(normalized / "contest_instance.json")
            boarddb = read_json(normalized / "boarddb.json")
            constraints = read_json(normalized / "route_constraints.json")
            timing_paths = read_json(
                normalized / "contest_timing_paths.json"
            )
            self.assertEqual(
                instance["source"]["contest"], "2025 EDA Elite Challenge"
            )
            self.assertEqual(boarddb["platform"]["kind"], "virtual")
            self.assertEqual(constraints["tdm_ratio_quantum"], 8)
            self.assertEqual(constraints["frame_slots"], 512)
            self.assertEqual(
                len(constraints["shared_capacity_links"]), 4
            )
            self.assertEqual(
                set(constraints["link_delay_ns"].values()), {30.7}
            )
            self.assertEqual(len(timing_paths["paths"]), 4)


if __name__ == "__main__":
    unittest.main()
