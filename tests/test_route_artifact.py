import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.packed_netlist import run_packed_netlist_import
from emuflow.route_artifact import validate_vpr_route_artifacts
from emuflow.route_artifact import _validate_sink_coverage
from tests.native_build import (
    vpr_packed_netlist_importer,
    vpr_route_checker,
)


ROOT = Path(__file__).resolve().parents[1]
PACKED = ROOT / "examples/physical/vpr_packed_fixture.net"
PLACEMENT = ROOT / "examples/physical/vpr_route_fixture.place"
ROUTE = ROOT / "examples/physical/vpr_route_fixture.route"
RR_GRAPH = ROOT / "examples/physical/vpr_rr_graph_fixture.xml"


class RouteArtifactTest(unittest.TestCase):
    def test_pin_level_sink_multiplicity_is_allowed(self) -> None:
        packed = {"sinks": ["clb[1]", "mult_36[2]"]}
        self.assertFalse(
            _validate_sink_coverage(
                "n0",
                packed,
                {"global": False, "local_only": False, "sinks": 3},
            )
        )
        with self.assertRaisesRegex(ValidationError, "sink coverage"):
            _validate_sink_coverage(
                "n0",
                packed,
                {"global": False, "local_only": False, "sinks": 1},
            )

    def test_global_endpoint_expansion_is_allowed(self) -> None:
        self.assertTrue(
            _validate_sink_coverage(
                "constant",
                {"sinks": ["clb[1]"]},
                {"global": True, "endpoints": 40},
            )
        )

    def _packed_contract(self, root: Path) -> Path:
        output = root / "packed.json"
        run_packed_netlist_import(
            PACKED,
            output,
            executable=str(vpr_packed_netlist_importer()),
        )
        return output

    def test_route_and_rr_graph_are_independently_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = validate_vpr_route_artifacts(
                ROUTE,
                RR_GRAPH,
                self._packed_contract(root),
                PLACEMENT,
                root / "route-check.json",
                executable=str(vpr_route_checker()),
            )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["packed_nets"], 2)
        self.assertEqual(report["routed_nets"], 2)
        self.assertEqual(report["route_nodes"], 10)
        self.assertEqual(report["route_edges"], 8)
        self.assertEqual(report["max_occupancy"], 1)
        self.assertEqual(
            report["checks"]["rr_edge_and_switch_legality"], "pass"
        )

    def test_illegal_rr_edge_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rr_graph = root / "broken.xml"
            rr_graph.write_text(
                RR_GRAPH.read_text(encoding="utf-8").replace(
                    'sink_node="3" src_node="2"',
                    'sink_node="9" src_node="2"',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValidationError, "edge/switch does not exist"
            ):
                validate_vpr_route_artifacts(
                    ROUTE,
                    rr_graph,
                    self._packed_contract(root),
                    PLACEMENT,
                    root / "route-check.json",
                    executable=str(vpr_route_checker()),
                )

    def test_route_is_bound_to_exact_placement_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            placement = root / "changed.place"
            placement.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(
                ValidationError, "placement ID does not match"
            ):
                validate_vpr_route_artifacts(
                    ROUTE,
                    RR_GRAPH,
                    self._packed_contract(root),
                    placement,
                    root / "route-check.json",
                    executable=str(vpr_route_checker()),
                )


if __name__ == "__main__":
    unittest.main()
