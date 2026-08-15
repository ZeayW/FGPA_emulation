import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import ValidationError
from emuflow.cross_stage import (
    run_phase45_feedback_loop,
    validate_phase45_feedback_report,
)
from emuflow.phase4 import run_phase4
from emuflow.phase5 import run_phase5
from emuflow.platform import Platform
from emuflow.routing import demands_from_assignment, normalize_route_constraints
from emuflow.routing_candidates import exact_route_candidate_selection
from emuflow.tdm import (
    build_tdm_schedule,
    reconstruct_tdm_schedule_timing,
)
from emuflow.tdm_feedback import (
    build_tdm_feedback,
    validate_tdm_feedback,
)
from emuflow.tdm_oracle import exact_multi_round_slot_schedule
from emuflow.timing_routing import (
    GLOBAL_CANDIDATE_PROVIDER,
    STA_PATHS_SCHEMA,
    compress_sta_paths,
    normalize_sta_paths,
    route_system_native,
)
from tests.native_build import (
    tdm_ratio_optimizer,
    tdm_timing_dag_optimizer,
    tlr_router,
)
from tests.test_phase4 import _assignment, _link, _platform_value


class TdmFeedbackTest(unittest.TestCase):
    def _timing_fixture(self):
        platform = Platform.from_dict(
            _platform_value(
                "feedback_diamond",
                ["a", "b", "c", "d"],
                [
                    _link("ab", "a", "b", lanes=4),
                    _link("bd", "b", "d", lanes=4),
                    _link("ac", "a", "c", lanes=4),
                    _link("cd", "c", "d", lanes=4),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [("n0", "a", ["d"]), ("n1", "a", ["d"])],
        )
        timing_source = {
            "schema": STA_PATHS_SCHEMA,
            "design": "route_test",
            "paths": [
                {
                    "id": f"p{index}",
                    "clock_domain": "clk",
                    "clock_period_ns": 20.0,
                    "slack_ns": 10.0 + index,
                    "fixed_delay_ns": 1.0,
                    "cut_nets": [f"n{index}"],
                }
                for index in range(2)
            ],
        }
        timing = compress_sta_paths(
            normalize_sta_paths(
                timing_source,
                demands_from_assignment(assignment, platform),
            )
        )
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 16,
                "reroute_rounds": 0,
                "link_delay_ns": {
                    "ab": 1.0,
                    "bd": 1.0,
                    "ac": 1.1,
                    "cd": 1.1,
                },
            },
            platform,
        )
        return platform, assignment, timing_source, timing, constraints

    def test_feedback_reconstructs_domains_and_rejects_tampering(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "feedback_line",
                ["a", "b", "c"],
                [
                    _link("ab", "a", "b", lanes=1),
                    _link("bc", "b", "c", lanes=1),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [("n0", "a", ["c"]), ("n1", "a", ["c"])],
        )
        routes = route_system_native(
            assignment,
            platform,
            normalize_route_constraints(
                {
                    "schema": "emuflow.system-route-constraints/v1",
                    "frame_slots": 8,
                },
                platform,
            ),
            executable=str(tlr_router()),
        )
        schedule = build_tdm_schedule(routes, platform)
        feedback = build_tdm_feedback(routes, platform, schedule)
        checked = validate_tdm_feedback(
            routes, platform, schedule, feedback
        )
        self.assertEqual(checked["status"], "pass")
        self.assertEqual(checked["domains"], 4)
        self.assertEqual(checked["scheduled_bit_hops"], 4)
        self.assertEqual(checked["timing_paths"], 0)
        active = [
            domain
            for domain in feedback["domains"]
            if domain["scheduled_bit_hops"]
        ]
        self.assertEqual(len(active), 2)
        self.assertTrue(all(domain["routing_price"] > 0 for domain in active))

        tampered = copy.deepcopy(feedback)
        tampered["domains"][0]["routing_price"] += 1.0
        with self.assertRaisesRegex(ValidationError, "does not match"):
            validate_tdm_feedback(
                routes, platform, schedule, tampered
            )

    def test_timing_feedback_rejects_source_and_path_tampering(self) -> None:
        platform, assignment, _, timing, constraints = self._timing_fixture()
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            timing,
            executable=str(tlr_router()),
            provider=GLOBAL_CANDIDATE_PROVIDER,
        )
        schedule = build_tdm_schedule(routes, platform)
        feedback = build_tdm_feedback(routes, platform, schedule)
        self.assertEqual(len(feedback["paths"]), 2)

        changed_path = copy.deepcopy(feedback)
        changed_path["paths"][0]["transport_delay_ns"] += 0.25
        with self.assertRaisesRegex(ValidationError, "does not match"):
            validate_tdm_feedback(
                routes, platform, schedule, changed_path
            )
        changed_routes = copy.deepcopy(routes)
        changed_routes["metrics"]["total_link_bit_hops"] += 1
        with self.assertRaisesRegex(ValidationError, "does not match"):
            validate_tdm_feedback(
                changed_routes, platform, schedule, feedback
            )
        changed_schedule = copy.deepcopy(schedule)
        changed_schedule["metrics"]["completion_slot"] += 1
        with self.assertRaisesRegex(ValidationError, "does not match"):
            validate_tdm_feedback(
                routes, platform, changed_schedule, feedback
            )

    def test_feedback_domain_projection_is_indexed_by_schedule_entry(self) -> None:
        platform, assignment, _, timing, constraints = self._timing_fixture()
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            timing,
            executable=str(tlr_router()),
            provider=GLOBAL_CANDIDATE_PROVIDER,
        )
        schedule = build_tdm_schedule(routes, platform)
        feedback = build_tdm_feedback(routes, platform, schedule)
        domains = {
            entry["id"]: entry["capacity_key"]
            for entry in schedule["entries"]
        }
        scheduled_entries = {
            path["path"]: path["scheduled_entries"]
            for path in feedback["paths"]
        }
        for path in feedback["paths"]:
            self.assertEqual(
                path["capacity_domains"],
                sorted({domains[item] for item in scheduled_entries[path["path"]]}),
            )

    def test_phase4_requires_and_revalidates_feedback_sources(self) -> None:
        (
            platform,
            assignment,
            timing_source,
            timing,
            constraints,
        ) = self._timing_fixture()
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            timing,
            executable=str(tlr_router()),
            provider=GLOBAL_CANDIDATE_PROVIDER,
        )
        schedule = build_tdm_schedule(routes, platform)
        feedback = build_tdm_feedback(routes, platform, schedule)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                "assignment.json": assignment,
                "platform.json": platform.to_dict(),
                "timing.json": timing_source,
                "constraints.json": constraints,
                "prior-routes.json": routes,
                "prior-schedule.json": schedule,
                "feedback.json": feedback,
            }
            for name, value in values.items():
                (root / name).write_text(
                    json.dumps(value), encoding="utf-8"
                )
            with self.assertRaisesRegex(
                ValueError, "requires --tdm-feedback-routes"
            ):
                run_phase4(
                    root / "assignment.json",
                    root / "platform.json",
                    root / "missing-sources",
                    constraints_path=root / "constraints.json",
                    timing_paths_path=root / "timing.json",
                    provider=GLOBAL_CANDIDATE_PROVIDER,
                    router=str(tlr_router()),
                    tdm_feedback_path=root / "feedback.json",
                )
            report = run_phase4(
                root / "assignment.json",
                root / "platform.json",
                root / "phase4",
                constraints_path=root / "constraints.json",
                timing_paths_path=root / "timing.json",
                provider=GLOBAL_CANDIDATE_PROVIDER,
                router=str(tlr_router()),
                tdm_feedback_path=root / "feedback.json",
                tdm_feedback_routes_path=root / "prior-routes.json",
                tdm_feedback_schedule_path=root / "prior-schedule.json",
                tdm_feedback_weight=0.5,
            )
            self.assertEqual(
                report["tdm_feedback"]["validation"]["status"], "pass"
            )
            self.assertEqual(
                report["artifacts"]["tdm_feedback"],
                "tdm_feedback.normalized.json",
            )
            self.assertEqual(
                report["tdm_feedback"]["feedback_price_scale"], 0.5
            )
            routed = json.loads(
                (root / "phase4/routes.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                routed["joint_optimization"]["tdm_feedback"][
                    "feedback_price_scale"
                ],
                0.5,
            )
            with self.assertRaisesRegex(ValueError, "must be in"):
                run_phase4(
                    root / "assignment.json",
                    root / "platform.json",
                    root / "invalid-weight",
                    constraints_path=root / "constraints.json",
                    timing_paths_path=root / "timing.json",
                    provider=GLOBAL_CANDIDATE_PROVIDER,
                    router=str(tlr_router()),
                    tdm_feedback_path=root / "feedback.json",
                    tdm_feedback_routes_path=root / "prior-routes.json",
                    tdm_feedback_schedule_path=root / "prior-schedule.json",
                    tdm_feedback_weight=0.0,
                )
            broken = copy.deepcopy(feedback)
            broken["domains"][0]["routing_price"] += 1.0
            (root / "feedback.json").write_text(
                json.dumps(broken), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "does not match"):
                run_phase4(
                    root / "assignment.json",
                    root / "platform.json",
                    root / "tampered",
                    constraints_path=root / "constraints.json",
                    timing_paths_path=root / "timing.json",
                    provider=GLOBAL_CANDIDATE_PROVIDER,
                    router=str(tlr_router()),
                    tdm_feedback_path=root / "feedback.json",
                    tdm_feedback_routes_path=root / "prior-routes.json",
                    tdm_feedback_schedule_path=(
                        root / "prior-schedule.json"
                    ),
                )

    def test_concrete_domain_price_changes_candidate_generation(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "feedback_behavior",
                ["c", "d", "e", "f", "g"],
                [
                    _link("cd", "c", "d", lanes=1, latency=2),
                    _link("cf", "c", "f", lanes=2, latency=1),
                    _link("de", "d", "e", lanes=2, latency=1),
                    _link("dg", "d", "g", lanes=4, latency=1),
                    _link("ef", "e", "f", lanes=1, latency=2),
                    _link("fg", "f", "g", lanes=1, latency=1),
                ],
            )
        )
        assignment = _assignment(
            platform,
            [("to_e", "c", ["e"]), ("to_d", "c", ["d"])],
        )
        timing_source = {
            "schema": STA_PATHS_SCHEMA,
            "design": "route_test",
            "paths": [
                {
                    "id": f"p{index}",
                    "clock_domain": "clk",
                    "clock_period_ns": 80.0,
                    "slack_ns": 30.0 + index,
                    "fixed_delay_ns": 1.0,
                    "cut_nets": [net],
                }
                for index, net in enumerate(("to_e", "to_d"))
            ],
        }
        timing = compress_sta_paths(
            normalize_sta_paths(
                timing_source,
                demands_from_assignment(assignment, platform),
            )
        )
        constraints = normalize_route_constraints(
            {
                "schema": "emuflow.system-route-constraints/v1",
                "frame_slots": 64,
                "max_iterations": 12,
                "reroute_rounds": 2,
                "link_delay_ns": {
                    "cd": 2.0,
                    "cf": 1.0,
                    "de": 1.0,
                    "dg": 1.0,
                    "ef": 2.0,
                    "fg": 1.0,
                },
            },
            platform,
        )
        first = route_system_native(
            assignment,
            platform,
            constraints,
            timing,
            executable=str(tlr_router()),
            provider=GLOBAL_CANDIDATE_PROVIDER,
        )
        schedule = build_tdm_schedule(first, platform)
        feedback = build_tdm_feedback(first, platform, schedule)
        validate_tdm_feedback(first, platform, schedule, feedback)
        with tempfile.TemporaryDirectory() as temporary:
            pool_path = Path(temporary) / "feedback-pool.json"
            second = route_system_native(
                assignment,
                platform,
                constraints,
                timing,
                executable=str(tlr_router()),
                provider=GLOBAL_CANDIDATE_PROVIDER,
                candidate_pool_path=pool_path,
                tdm_feedback=feedback,
            )
            oracle = exact_route_candidate_selection(
                assignment,
                platform,
                json.loads(pool_path.read_text(encoding="utf-8")),
                timing,
            )
            self.assertIn(
                second["joint_optimization"]["candidate_generation"][
                    "master_selection"
                ],
                oracle["optimal_selections"],
            )
            self.assertTrue(second["metrics"]["master_exact"])
        route_by_net = {
            route["net"]: route for route in first["routes"]
        }
        feedback_route_by_net = {
            route["net"]: route for route in second["routes"]
        }
        self.assertEqual(
            [edge["link"] for edge in route_by_net["to_e"]["tree_edges"]],
            ["cd", "de"],
        )
        self.assertEqual(
            [
                edge["link"]
                for edge in feedback_route_by_net["to_e"]["tree_edges"]
            ],
            ["cf", "ef"],
        )
        prices = {
            domain["key"]: domain["routing_price"]
            for domain in feedback["domains"]
        }
        self.assertGreater(prices["cd:c->d"], prices["cf:c->f"])
        self.assertEqual(
            second["joint_optimization"]["tdm_feedback"][
                "source_schedule_sha256"
            ],
            feedback["source_schedule_sha256"],
        )
        database = {
            "schema": "emuflow.sta-path-database/v1",
            "design": assignment["design"],
            "source": {"provider": "fixture", "input": "fixture"},
            "normalization": timing["normalization"],
            "paths": [
                {
                    "id": path["id"],
                    "clock_domain": path["clock_domain"],
                    "clock_period_ns": path["clock_period_ns"],
                    "slack_ns": path["slack_ns"],
                    "fixed_delay_ns": path["fixed_delay_ns"],
                    "path_nets": list(path["cut_nets"]),
                    "normalized_slack": path["normalized_slack"],
                }
                for path in timing["paths"]
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, value in {
                "assignment.json": assignment,
                "platform.json": platform.to_dict(),
                "timing.json": timing_source,
                "database.json": database,
                "constraints.json": constraints,
            }.items():
                (root / name).write_text(
                    json.dumps(value), encoding="utf-8"
                )
            loop = run_phase45_feedback_loop(
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                output_dir=root / "loop",
                canonical_phase4_dir=root / "loop-routes",
                canonical_phase5_dir=root / "loop-tdm",
                max_iterations=3,
                route_constraints_path=root / "constraints.json",
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                simulation_frames=2,
                ratio_max_iterations=50,
                post_refinement_iterations=20,
            )
            self.assertEqual(loop["selected_candidate"], 1)
            self.assertEqual(loop["termination"], "route-cycle")
            self.assertEqual(
                loop["candidates"][1]["decision"]["reason"],
                "phase5-objective-improved",
            )
            self.assertTrue(loop["candidates"][1]["decision"]["accepted"])
            trust = run_phase45_feedback_loop(
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                output_dir=root / "trust-loop",
                canonical_phase4_dir=root / "trust-routes",
                canonical_phase5_dir=root / "trust-tdm",
                max_iterations=1,
                max_route_change_fraction=0.5,
                route_constraints_path=root / "constraints.json",
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                simulation_frames=2,
                ratio_max_iterations=50,
                post_refinement_iterations=20,
            )
            self.assertEqual(trust["selected_candidate"], 0)
            self.assertEqual(trust["termination"], "line-search-rejected")
            self.assertTrue(
                all(
                    candidate["decision"]["reason"]
                    == "route-trust-region-exceeded"
                    for candidate in trust["candidates"][1:]
                )
            )

    def test_phase5_writes_checked_feedback_artifact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        platform_path = root / "platforms/virtual/xcvu3p_2fpga_p2p.json"
        platform = Platform.load(platform_path)
        assignment = _assignment(platform, [("n0", "fpga0", ["fpga1"])])
        routes = route_system_native(
            assignment,
            platform,
            normalize_route_constraints(None, platform, frame_slots=8),
            executable=str(tlr_router()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            routes_path = temporary_root / "routes.json"
            routes_path.write_text(json.dumps(routes), encoding="utf-8")
            report = run_phase5(
                routes_path,
                platform_path,
                temporary_root / "phase5",
                simulation_frames=2,
            )
            feedback_path = temporary_root / "phase5/tdm_feedback.json"
            self.assertTrue(feedback_path.is_file())
            self.assertEqual(
                report["artifacts"]["tdm_feedback"], "tdm_feedback.json"
            )
            self.assertEqual(
                report["tdm_feedback_validation"]["status"], "pass"
            )

    def test_academic_feedback_consumer_rebuilds_canonical_ratio_model(
        self,
    ) -> None:
        (
            platform,
            assignment,
            _,
            timing,
            constraints,
        ) = self._timing_fixture()
        routes = route_system_native(
            assignment,
            platform,
            constraints,
            timing,
            executable=str(tlr_router()),
            provider=GLOBAL_CANDIDATE_PROVIDER,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            routes_path = root / "routes.json"
            platform_path = root / "platform.json"
            routes_path.write_text(json.dumps(routes), encoding="utf-8")
            platform_path.write_text(
                json.dumps(platform.to_dict()), encoding="utf-8"
            )
            run_phase5(
                routes_path,
                platform_path,
                root / "phase5",
                simulation_frames=2,
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                ratio_max_iterations=20,
                post_refinement_iterations=10,
            )
            schedule = json.loads(
                (root / "phase5/schedule.json").read_text(encoding="utf-8")
            )
            ratio_plan = json.loads(
                (root / "phase5/ratio_plan.json").read_text(
                    encoding="utf-8"
                )
            )
            feedback = json.loads(
                (root / "phase5/tdm_feedback.json").read_text(
                    encoding="utf-8"
                )
            )
            # A downstream Phase 4 process has only the sealed artifacts,
            # not Phase 5's in-memory dense model.  Academic feedback must
            # rebuild that canonical model rather than silently switching to
            # the sparse ratio-free timing reconstruction.
            with patch(
                "emuflow.tdm_feedback."
                "reconstruct_tdm_schedule_timing_paths_from_routes",
                side_effect=AssertionError("sparse path must not be used"),
            ):
                checked = validate_tdm_feedback(
                    routes,
                    platform,
                    schedule,
                    feedback,
                    ratio_plan,
                )
            self.assertEqual(checked["status"], "pass")

    def test_checked_phase45_loop_seals_and_revalidates_trials(self) -> None:
        (
            platform,
            assignment,
            timing_source,
            timing,
            constraints,
        ) = self._timing_fixture()
        database = {
            "schema": "emuflow.sta-path-database/v1",
            "design": assignment["design"],
            "source": {"provider": "fixture", "input": "fixture"},
            "normalization": timing["normalization"],
            "paths": [
                {
                    "id": path["id"],
                    "clock_domain": path["clock_domain"],
                    "clock_period_ns": path["clock_period_ns"],
                    "slack_ns": path["slack_ns"],
                    "fixed_delay_ns": path["fixed_delay_ns"],
                    "path_nets": list(path["cut_nets"]),
                    "normalized_slack": path["normalized_slack"],
                }
                for path in timing["paths"]
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                "assignment.json": assignment,
                "platform.json": platform.to_dict(),
                "timing.json": timing_source,
                "database.json": database,
                "constraints.json": constraints,
            }
            for name, value in values.items():
                (root / name).write_text(
                    json.dumps(value), encoding="utf-8"
                )
            report = run_phase45_feedback_loop(
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                output_dir=root / "phase45",
                canonical_phase4_dir=root / "system-route",
                canonical_phase5_dir=root / "tdm",
                max_iterations=2,
                route_constraints_path=root / "constraints.json",
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                simulation_frames=2,
                ratio_max_iterations=20,
                post_refinement_iterations=10,
            )
            self.assertEqual(report["status"], "pass")
            self.assertGreaterEqual(len(report["candidates"]), 2)
            validation = validate_phase45_feedback_report(
                root / "phase45/phase45_feedback_report.json",
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                route_constraints_path=root / "constraints.json",
                canonical_phase4_dir=root / "system-route",
                canonical_phase5_dir=root / "tdm",
            )
            self.assertEqual(validation["status"], "pass")
            for candidate in report["candidates"]:
                if candidate["status"] != "pass":
                    continue
                phase4_root = root / "phase45" / candidate["phase4_dir"]
                phase5_root = root / "phase45" / candidate["phase5_dir"]
                candidate_routes = json.loads(
                    (phase4_root / "routes.json").read_text(encoding="utf-8")
                )
                route_oracle = exact_route_candidate_selection(
                    assignment,
                    platform,
                    json.loads(
                        (phase4_root / "route_candidate_pool.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                    timing,
                )
                self.assertIn(
                    candidate_routes["joint_optimization"][
                        "candidate_generation"
                    ]["master_selection"],
                    route_oracle["optimal_selections"],
                )
                candidate_schedule = json.loads(
                    (phase5_root / "schedule.json").read_text(
                        encoding="utf-8"
                    )
                )
                candidate_ratio = json.loads(
                    (phase5_root / "ratio_plan.json").read_text(
                        encoding="utf-8"
                    )
                )
                slot_oracle = exact_multi_round_slot_schedule(
                    candidate_routes,
                    platform,
                    candidate_ratio,
                    max_hops=8,
                )
                timing_result = reconstruct_tdm_schedule_timing(
                    candidate_routes, platform, candidate_schedule
                )
                self.assertAlmostEqual(
                    timing_result["worst_normalized_slack"],
                    slot_oracle["worst_normalized_slack"],
                )
                self.assertEqual(
                    candidate_schedule["metrics"]["completion_slot"],
                    slot_oracle["completion_slot"],
                )
            parallel = run_phase45_feedback_loop(
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                output_dir=root / "phase45-parallel",
                canonical_phase4_dir=root / "system-route-parallel",
                canonical_phase5_dir=root / "tdm-parallel",
                max_iterations=2,
                route_constraints_path=root / "constraints.json",
                router=str(tlr_router()),
                route_candidate_workers=2,
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                simulation_frames=2,
                ratio_max_iterations=20,
                post_refinement_iterations=10,
            )
            self.assertEqual(
                (root / "system-route/routes.json").read_bytes(),
                (root / "system-route-parallel/routes.json").read_bytes(),
            )
            self.assertEqual(
                (root / "tdm/schedule.json").read_bytes(),
                (root / "tdm-parallel/schedule.json").read_bytes(),
            )
            self.assertEqual(report["termination"], parallel["termination"])
            self.assertEqual(
                report["candidates"][report["selected_candidate"]][
                    "objective_key"
                ],
                parallel["candidates"][parallel["selected_candidate"]][
                    "objective_key"
                ],
            )
            tampered = copy.deepcopy(report)
            tampered["selected_candidate"] = len(report["candidates"]) - 1
            (root / "phase45/tampered.json").write_text(
                json.dumps(tampered), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValidationError, "selected candidate mismatch"
            ):
                validate_phase45_feedback_report(
                    root / "phase45/tampered.json",
                    database_path=root / "database.json",
                    assignment_path=root / "assignment.json",
                    platform_path=root / "platform.json",
                    timing_paths_path=root / "timing.json",
                    route_constraints_path=root / "constraints.json",
                    canonical_phase4_dir=root / "system-route",
                    canonical_phase5_dir=root / "tdm",
                )
            resealed = copy.deepcopy(report)
            normalized_record = resealed["candidates"][1]["artifacts"][
                "normalized_feedback"
            ]
            normalized_path = root / "phase45" / normalized_record["path"]
            normalized = json.loads(
                normalized_path.read_text(encoding="utf-8")
            )
            normalized["domains"][0]["routing_price"] += 0.125
            normalized_path.write_text(
                json.dumps(normalized), encoding="utf-8"
            )
            normalized_record["sha256"] = hashlib.sha256(
                normalized_path.read_bytes()
            ).hexdigest()
            (root / "phase45/resealed.json").write_text(
                json.dumps(resealed), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValidationError, "preserve the independently rebuilt"
            ):
                validate_phase45_feedback_report(
                    root / "phase45/resealed.json",
                    database_path=root / "database.json",
                    assignment_path=root / "assignment.json",
                    platform_path=root / "platform.json",
                    timing_paths_path=root / "timing.json",
                    route_constraints_path=root / "constraints.json",
                    canonical_phase4_dir=root / "system-route",
                    canonical_phase5_dir=root / "tdm",
                )

    def test_phase45_loop_inherits_frozen_ratio_quantum(self) -> None:
        (
            platform,
            assignment,
            timing_source,
            timing,
            constraints,
        ) = self._timing_fixture()
        constraints["tdm_min_ratio"] = 4
        constraints["tdm_ratio_quantum"] = 4
        database = {
            "schema": "emuflow.sta-path-database/v1",
            "design": assignment["design"],
            "source": {"provider": "fixture", "input": "fixture"},
            "normalization": timing["normalization"],
            "paths": [
                {
                    "id": path["id"],
                    "clock_domain": path["clock_domain"],
                    "clock_period_ns": path["clock_period_ns"],
                    "slack_ns": path["slack_ns"],
                    "fixed_delay_ns": path["fixed_delay_ns"],
                    "path_nets": list(path["cut_nets"]),
                    "normalized_slack": path["normalized_slack"],
                }
                for path in timing["paths"]
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, value in {
                "assignment.json": assignment,
                "platform.json": platform.to_dict(),
                "timing.json": timing_source,
                "database.json": database,
                "constraints.json": constraints,
            }.items():
                (root / name).write_text(
                    json.dumps(value), encoding="utf-8"
                )
            report = run_phase45_feedback_loop(
                database_path=root / "database.json",
                assignment_path=root / "assignment.json",
                platform_path=root / "platform.json",
                timing_paths_path=root / "timing.json",
                output_dir=root / "phase45",
                canonical_phase4_dir=root / "system-route",
                canonical_phase5_dir=root / "tdm",
                max_iterations=0,
                route_constraints_path=root / "constraints.json",
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                simulation_frames=2,
                ratio_max_iterations=20,
                post_refinement_iterations=10,
            )
            ratio_plan = json.loads(
                (root / "tdm/ratio_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ratio_plan["configuration"]["min_ratio"], 4)
            self.assertEqual(
                ratio_plan["configuration"]["ratio_quantum"], 4
            )
            self.assertEqual(report["selected_candidate"], 0)


if __name__ == "__main__":
    unittest.main()
