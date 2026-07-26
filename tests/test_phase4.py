import copy
import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.partition import (
    PARTITION_ASSIGNMENT_SCHEMA,
    assign_clusters,
    build_clusters,
    normalize_partition_constraints,
)
from emuflow.phase4 import run_phase4
from emuflow.platform import Platform
from emuflow.routing import (
    normalize_route_constraints,
    route_system,
    validate_system_routes,
)
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


def _platform_value(name, fpga_ids, links):
    return {
        "schema": "emuflow.boarddb/v1",
        "platform": {
            "name": name,
            "kind": "virtual",
            "description": "Phase 4 test topology",
        },
        "fpgas": [
            {
                "id": fpga_id,
                "part": "xcvu3p-ffvc1517-2-e",
                "utilization_limit": 1.0,
                "capacity": {"lut": 100, "ff": 100},
            }
            for fpga_id in fpga_ids
        ],
        "links": links,
    }


def _link(link_id, left, right, lanes=1, direction="full_duplex"):
    return {
        "id": link_id,
        "endpoints": [left, right],
        "direction": direction,
        "mode": "abstract",
        "data_lanes_per_direction": lanes,
        "fabric_clock_mhz": 250.0,
        "latency_cycles": 1,
    }


def _assignment(platform, cuts):
    return {
        "schema": PARTITION_ASSIGNMENT_SCHEMA,
        "design": "route_test",
        "platform": platform.name,
        "cut_nets": [
            {
                "net": net,
                "cut_class": "register_output",
                "source_fpgas": [source],
                "sink_fpgas": list(sinks),
                "sink_endpoints": len(sinks),
            }
            for net, source, sinks in cuts
        ],
    }


class Phase4Test(unittest.TestCase):
    def test_pipeline_routes_real_counter_partition(self) -> None:
        ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        platform = Platform.load(PLATFORM_PATH)
        constraints = normalize_partition_constraints(None, ir, platform)
        clusters = build_clusters(ir, constraints)
        assignment = assign_clusters(
            ir,
            platform,
            clusters,
            constraints,
            seed=4,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assignment_path = root / "assignment.json"
            assignment_path.write_text(
                json.dumps(assignment), encoding="utf-8"
            )
            report = run_phase4(
                assignment_path=assignment_path,
                platform_path=PLATFORM_PATH,
                output_dir=root / "phase4",
                frame_slots=1,
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["validation"]["demands"], 1)
            self.assertEqual(report["validation"]["routed_sinks"], 1)
            self.assertEqual(report["validation"]["overloaded_links"], 0)
            for filename in (
                "route_constraints.normalized.json",
                "routes.json",
                "phase4_report.json",
            ):
                self.assertTrue((root / "phase4" / filename).is_file())

    def test_negotiated_router_uses_both_diamond_paths(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "diamond",
                ["a", "b", "c", "d"],
                [
                    _link("ab", "a", "b"),
                    _link("bd", "b", "d"),
                    _link("ac", "a", "c"),
                    _link("cd", "c", "d"),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [("n0", "a", ["d"]), ("n1", "a", ["d"])],
        )
        constraints = normalize_route_constraints(
            {"schema": "emuflow.system-route-constraints/v1", "frame_slots": 1},
            platform,
        )
        routes = route_system(assignment, platform, constraints)
        validation = validate_system_routes(assignment, platform, routes)
        self.assertEqual(validation["status"], "pass")
        used_links = {
            edge["link"]
            for route in routes["routes"]
            for edge in route["tree_edges"]
        }
        self.assertEqual(used_links, {"ab", "bd", "ac", "cd"})
        self.assertEqual(validation["max_link_utilization"], 1.0)

    def test_multicast_is_a_reachable_acyclic_tree(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "multicast",
                ["a", "b", "c", "d"],
                [
                    _link("ab", "a", "b", lanes=4),
                    _link("ac", "a", "c", lanes=4),
                    _link("bd", "b", "d", lanes=4),
                    _link("cd", "c", "d", lanes=4),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [("multicast_net", "a", ["b", "c", "d"])],
        )
        constraints = normalize_route_constraints(None, platform, frame_slots=1)
        routes = route_system(assignment, platform, constraints)
        validation = validate_system_routes(assignment, platform, routes)
        self.assertEqual(validation["routed_sinks"], 3)
        self.assertEqual(validation["demands"], 1)
        self.assertLessEqual(validation["tree_edges"], 3)

    def test_unavailable_links_report_unreachable_sink(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "line",
                ["a", "b", "c"],
                [_link("ab", "a", "b"), _link("bc", "b", "c")],
            )
        )
        assignment = _assignment(platform, [("n0", "a", ["c"])])
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "unavailable_links": ["bc"],
            },
            platform,
        )
        with self.assertRaisesRegex(ValidationError, "cannot reach"):
            route_system(assignment, platform, constraints)

    def test_infeasible_link_capacity_is_rejected(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "single",
                ["a", "b"],
                [_link("ab", "a", "b")],
            )
        )
        assignment = _assignment(
            platform,
            [("n0", "a", ["b"]), ("n1", "a", ["b"])],
        )
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 1,
                "max_iterations": 2,
            },
            platform,
        )
        with self.assertRaisesRegex(ValidationError, "infeasible"):
            route_system(assignment, platform, constraints)

    def test_half_duplex_capacity_is_shared(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "half",
                ["a", "b"],
                [_link("ab", "a", "b", direction="half_duplex")],
            )
        )
        assignment = _assignment(
            platform,
            [("n0", "a", ["b"]), ("n1", "b", ["a"])],
        )
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 1,
                "max_iterations": 2,
            },
            platform,
        )
        with self.assertRaisesRegex(ValidationError, "infeasible"):
            route_system(assignment, platform, constraints)

    def test_cycle_in_route_artifact_is_rejected(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "cycle",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=4)],
            )
        )
        assignment = _assignment(platform, [("n0", "a", ["b"])])
        constraints = normalize_route_constraints(None, platform, frame_slots=1)
        routes = route_system(assignment, platform, constraints)
        broken = copy.deepcopy(routes)
        broken["routes"][0]["tree_edges"].append(
            {"link": "ab", "from": "b", "to": "a"}
        )
        with self.assertRaisesRegex(ValidationError, "cycle"):
            validate_system_routes(assignment, platform, broken)


if __name__ == "__main__":
    unittest.main()
