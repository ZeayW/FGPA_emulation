import copy
import json
import shutil
import subprocess
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
from emuflow.timing_routing import (
    load_sta_paths,
    validate_timing_aware_system_routes,
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


def _link(
    link_id,
    left,
    right,
    lanes=1,
    direction="full_duplex",
    latency=1,
):
    return {
        "id": link_id,
        "endpoints": [left, right],
        "direction": direction,
        "mode": "abstract",
        "data_lanes_per_direction": lanes,
        "fabric_clock_mhz": 250.0,
        "latency_cycles": latency,
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
    def test_timing_aware_cpp_router_prioritizes_critical_clock_domain(
        self,
    ) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a C++17 compiler is required")
        platform = Platform.from_dict(
            _platform_value(
                "timing_diamond",
                ["a", "b", "c", "d"],
                [
                    _link("ab", "a", "b", latency=1),
                    _link("bd", "b", "d", latency=1),
                    _link("ac", "a", "c", latency=5),
                    _link("cd", "c", "d", latency=5),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [
                ("a_low_priority", "a", ["d"]),
                ("z_critical", "a", ["d"]),
            ],
        )
        timing_value = {
            "schema": "emuflow.sta-paths/v1",
            "design": "route_test",
            "paths": [
                {
                    "id": "critical_0",
                    "clock_domain": "fast_clk",
                    "clock_period_ns": 20.0,
                    "slack_ns": -1.0,
                    "fixed_delay_ns": 10.0,
                    "cut_nets": ["z_critical"],
                    "cut_signature": ["a->d"],
                },
                {
                    "id": "critical_duplicate",
                    "clock_domain": "fast_clk",
                    "clock_period_ns": 20.0,
                    "slack_ns": 0.0,
                    "fixed_delay_ns": 9.0,
                    "cut_nets": ["z_critical"],
                    "cut_signature": ["a->d"],
                },
                {
                    "id": "relaxed_0",
                    "clock_domain": "slow_clk",
                    "clock_period_ns": 100.0,
                    "slack_ns": 80.0,
                    "fixed_delay_ns": 0.0,
                    "cut_nets": ["a_low_priority"],
                    "cut_signature": ["slow:a->d"],
                },
            ],
        }
        constraints_value = {
            "schema": "emuflow.system-route-constraints/v1",
            "frame_slots": 1,
            "max_iterations": 12,
            "reroute_rounds": 4,
            "link_delay_ns": {
                "ab": 1.0,
                "bd": 1.0,
                "ac": 5.0,
                "cd": 5.0,
            },
        }
        baseline_constraints = normalize_route_constraints(
            constraints_value, platform
        )
        baseline = route_system(assignment, platform, baseline_constraints)
        baseline_by_net = {
            route["net"]: route for route in baseline["routes"]
        }
        self.assertEqual(
            baseline_by_net["z_critical"]["max_latency_cycles"], 10
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            executable = root / "emuflow_tlr_router"
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    str(ROOT / "src" / "native" / "tlr_router.cpp"),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            assignment_path = root / "assignment.json"
            platform_path = root / "platform.json"
            timing_path = root / "timing.json"
            constraints_path = root / "constraints.json"
            assignment_path.write_text(
                json.dumps(assignment), encoding="utf-8"
            )
            platform_path.write_text(
                json.dumps(platform.to_dict()), encoding="utf-8"
            )
            timing_path.write_text(
                json.dumps(timing_value), encoding="utf-8"
            )
            constraints_path.write_text(
                json.dumps(constraints_value), encoding="utf-8"
            )
            report = run_phase4(
                assignment_path=assignment_path,
                platform_path=platform_path,
                output_dir=root / "phase4",
                constraints_path=constraints_path,
                timing_paths_path=timing_path,
                router=str(executable),
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["validation"]["timing_paths_original"], 3)
            self.assertEqual(report["validation"]["timing_paths_compressed"], 2)
            routes = json.loads(
                (root / "phase4" / "routes.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                routes["provider"],
                "timing-aware-route-tdm-cooptimized-v1",
            )
            self.assertEqual(
                routes["joint_optimization"]["method"],
                "tdm-contention-aware-rip-up-reroute-v1",
            )
            route_by_net = {
                route["net"]: route for route in routes["routes"]
            }
            self.assertEqual(
                {
                    edge["link"]
                    for edge in route_by_net["z_critical"]["tree_edges"]
                },
                {"ab", "bd"},
            )
            self.assertEqual(
                route_by_net["z_critical"]["predicted_max_delay_ns"], 2.0
            )
            self.assertEqual(
                {
                    edge["link"]
                    for edge in route_by_net["a_low_priority"]["tree_edges"]
                },
                {"ac", "cd"},
            )
            normalized = load_sta_paths(
                timing_path,
                routes["demands"],
            )
            checked = validate_timing_aware_system_routes(
                assignment, platform, routes, normalized
            )
            self.assertEqual(checked["status"], "pass")
            normalized_reload = load_sta_paths(
                root / "phase4" / "timing_paths.normalized.json",
                routes["demands"],
            )
            self.assertEqual(normalized_reload, normalized)
            corrupted = copy.deepcopy(routes)
            corrupted["routes"][0]["predicted_max_delay_ns"] += 0.25
            with self.assertRaisesRegex(
                ValidationError, "independent edge-delay recomputation"
            ):
                validate_timing_aware_system_routes(
                    assignment, platform, corrupted, normalized
                )
            corrupted = copy.deepcopy(routes)
            corrupted["metrics"]["estimated_max_tdm_ratio"] += 1
            with self.assertRaisesRegex(
                ValidationError, "route/TDM proxy recomputation"
            ):
                validate_timing_aware_system_routes(
                    assignment, platform, corrupted, normalized
                )

            lock_platform = Platform.from_dict(
                _platform_value(
                    "direction_lock",
                    ["a", "b", "c"],
                    [
                        _link(
                            "ab",
                            "a",
                            "b",
                            lanes=4,
                            direction="half_duplex",
                        ),
                        _link("ac", "a", "c", lanes=4, latency=2),
                        _link("bc", "b", "c", lanes=4, latency=2),
                    ],
                )
            )
            lock_assignment = _assignment(
                lock_platform,
                [
                    ("forward_0", "a", ["b"]),
                    ("forward_1", "a", ["b"]),
                    ("reverse_0", "b", ["a"]),
                ],
            )
            lock_timing = {
                "schema": "emuflow.sta-paths/v1",
                "design": "route_test",
                "paths": [
                    {
                        "id": f"path_{net}",
                        "clock_domain": "clk",
                        "clock_period_ns": 50.0,
                        "slack_ns": 20.0,
                        "fixed_delay_ns": 0.0,
                        "cut_nets": [net],
                        "cut_signature": [signature],
                    }
                    for net, signature in (
                        ("forward_0", "a->b:0"),
                        ("forward_1", "a->b:1"),
                        ("reverse_0", "b->a"),
                    )
                ],
            }
            lock_assignment_path = root / "lock-assignment.json"
            lock_platform_path = root / "lock-platform.json"
            lock_timing_path = root / "lock-timing.json"
            lock_assignment_path.write_text(
                json.dumps(lock_assignment), encoding="utf-8"
            )
            lock_platform_path.write_text(
                json.dumps(lock_platform.to_dict()), encoding="utf-8"
            )
            lock_timing_path.write_text(
                json.dumps(lock_timing), encoding="utf-8"
            )
            lock_report = run_phase4(
                assignment_path=lock_assignment_path,
                platform_path=lock_platform_path,
                output_dir=root / "lock-phase4",
                frame_slots=1,
                provider="timing-aware-load-balanced-v1",
                timing_paths_path=lock_timing_path,
                router=str(executable),
            )
            self.assertEqual(lock_report["validation"]["direction_locks"], 1)
            lock_routes = json.loads(
                (root / "lock-phase4" / "routes.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                lock_routes["provider"], "timing-aware-load-balanced-v1"
            )
            self.assertEqual(lock_routes["constraints"]["lambda_tdm"], 0.0)
            self.assertNotIn("joint_optimization", lock_routes)
            self.assertEqual(
                lock_routes["direction_locks"][0]["from"], "a"
            )
            reverse_route = next(
                route
                for route in lock_routes["routes"]
                if route["net"] == "reverse_0"
            )
            self.assertNotIn(
                "ab", {edge["link"] for edge in reverse_route["tree_edges"]}
            )

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
            self.assertGreater(report["validation"]["demands"], 0)
            self.assertGreater(report["validation"]["routed_sinks"], 0)
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
