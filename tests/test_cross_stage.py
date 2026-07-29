import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.cross_stage import (
    build_cross_stage_candidate,
    compare_candidate_objectives,
    run_cross_stage_optimization,
    validate_cross_stage_report,
)
from emuflow.errors import ValidationError
from emuflow.io import write_json
from emuflow.partition import PARTITION_ASSIGNMENT_SCHEMA
from emuflow.phase3 import run_phase3
from emuflow.platform import Platform
from emuflow.tdm import build_tdm_schedule
from emuflow.tdm_ratio import build_tdm_ratio_plan
from emuflow.yosys import import_yosys_json
from tests.test_phase5 import _link, _platform_value, _routes


ROOT = Path(__file__).resolve().parents[1]


class CrossStageCandidateTest(unittest.TestCase):
    def test_all_path_objective_keeps_non_crossing_paths(self) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a C++17 compiler is required")
        platform = Platform.from_dict(
            _platform_value(
                "cross_stage",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=2, latency=1)],
            )
        )
        cuts = [("n0", "a", ["b"]), ("n1", "a", ["b"])]
        assignment = {
            "schema": PARTITION_ASSIGNMENT_SCHEMA,
            "design": "tdm_test",
            "platform": platform.name,
            "cut_nets": [
                {
                    "net": net,
                    "cut_class": "register_output",
                    "source_fpgas": [source],
                    "sink_fpgas": sinks,
                    "sink_endpoints": len(sinks),
                }
                for net, source, sinks in cuts
            ],
        }
        routes = _routes(platform, cuts, frame_slots=16)
        routes["timing"] = {
            "schema": "emuflow.sta-paths/v1",
            "normalization": {
                "positive_slack_scale_ns": 10.0,
                "negative_slack_scale_ns": 10.0,
                "max_clock_period_ns": 20.0,
            },
            "compression": {
                "original_paths": 2,
                "compressed_paths": 2,
            },
            "paths": [
                {
                    "path": "p0",
                    "clock_domain": "clk",
                    "clock_period_ns": 20.0,
                    "fixed_delay_ns": 8.0,
                    "cut_nets": ["n0"],
                },
                {
                    "path": "p1",
                    "clock_domain": "clk",
                    "clock_period_ns": 20.0,
                    "fixed_delay_ns": 4.0,
                    "cut_nets": ["n1"],
                },
            ],
        }
        database = {
            "schema": "emuflow.sta-path-database/v1",
            "design": "tdm_test",
            "source": {"provider": "fixture", "input": "fixture"},
            "normalization": {
                "positive_slack_scale_ns": 10.0,
                "negative_slack_scale_ns": 10.0,
                "max_clock_period_ns": 20.0,
            },
            "paths": [
                {
                    "id": "p0",
                    "clock_domain": "clk",
                    "clock_period_ns": 20.0,
                    "slack_ns": 10.0,
                    "fixed_delay_ns": 8.0,
                    "path_nets": ["n0"],
                    "normalized_slack": 1.0,
                },
                {
                    "id": "p1",
                    "clock_domain": "clk",
                    "clock_period_ns": 20.0,
                    "slack_ns": 10.0,
                    "fixed_delay_ns": 4.0,
                    "path_nets": ["n1"],
                    "normalized_slack": 1.0,
                },
                {
                    "id": "local-critical",
                    "clock_domain": "clk",
                    "clock_period_ns": 5.0,
                    "slack_ns": -5.0,
                    "fixed_delay_ns": 10.0,
                    "path_nets": ["local"],
                    "normalized_slack": -0.1,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            executable = (
                Path(temporary) / "emuflow_tdm_ratio_optimizer"
            )
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    str(
                        ROOT
                        / "src"
                        / "native"
                        / "tdm_ratio_optimizer.cpp"
                    ),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            plan = build_tdm_ratio_plan(
                routes,
                platform,
                executable=str(executable),
                max_ratio=8,
                post_refinement_iterations=10,
            )
            schedule = build_tdm_schedule(routes, platform, plan)
            candidate = build_cross_stage_candidate(
                database,
                assignment,
                routes,
                schedule,
                plan,
                platform,
            )
            repeated = build_cross_stage_candidate(
                database,
                assignment,
                routes,
                schedule,
                plan,
                platform,
            )
        self.assertEqual(candidate, repeated)
        self.assertEqual(candidate["path_metrics"]["all_paths"], 3)
        self.assertEqual(candidate["path_metrics"]["crossing_paths"], 2)
        self.assertEqual(candidate["path_metrics"]["no_cut_paths"], 1)
        self.assertEqual(
            candidate["path_metrics"]["worst_path"], "local-critical"
        )

    def test_lexicographic_acceptance_and_rollback(self) -> None:
        incumbent = {
            "objective_metrics": {
                "worst_normalized_slack": -2.0,
                "total_negative_normalized_slack": -8.0,
                "negative_slack_paths": 4,
                "max_tdm_ratio": 8,
                "completion_slot": 20,
                "total_link_bit_hops": 30,
                "cut_bits": 20,
                "replica_luts": 0,
            }
        }
        improved = {
            "objective_metrics": {
                **incumbent["objective_metrics"],
                "worst_normalized_slack": -1.0,
            }
        }
        regressed = {
            "objective_metrics": {
                **incumbent["objective_metrics"],
                "worst_normalized_slack": -3.0,
            }
        }
        tied = {"objective_metrics": dict(incumbent["objective_metrics"])}
        self.assertTrue(
            compare_candidate_objectives(improved, incumbent)["accepted"]
        )
        self.assertFalse(
            compare_candidate_objectives(regressed, incumbent)["accepted"]
        )
        self.assertFalse(
            compare_candidate_objectives(tied, incumbent)["accepted"]
        )

    def test_connected_rtl_baseline_transaction_is_reproducible(self) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a C++17 compiler is required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = import_yosys_json(
                ROOT / "examples" / "yosys" / "counter.json",
                top="counter",
                clocks=["clk"],
            )
            ir_path = root / "ir.json"
            platform_path = root / "platform.json"
            initial_root = root / "initial"
            database_path = root / "database.json"
            write_json(ir_path, ir.value)
            write_json(
                platform_path,
                _platform_value(
                    "connected_cross_stage",
                    ["a", "b"],
                    [_link("ab", "a", "b", lanes=8, latency=1)],
                ),
            )
            run_phase3(
                ir_path,
                platform_path,
                initial_root,
                provider="greedy",
                min_used_fpgas=2,
                balance_tolerance=1.0,
            )
            assignment = json.loads(
                (initial_root / "assignment.json").read_text()
            )
            self.assertTrue(assignment["cut_nets"])
            path_nets = [net["id"] for net in ir.value["nets"]]
            write_json(
                database_path,
                {
                    "schema": "emuflow.sta-path-database/v1",
                    "design": "counter",
                    "source": {
                        "provider": "connected-rtl-fixture",
                        "input": "counter",
                    },
                    "normalization": {
                        "positive_slack_scale_ns": 10.0,
                        "negative_slack_scale_ns": 1.0,
                        "max_clock_period_ns": 20.0,
                    },
                    "paths": [
                        {
                            "id": "counter-critical",
                            "clock_domain": "clk",
                            "clock_period_ns": 20.0,
                            "slack_ns": 10.0,
                            "fixed_delay_ns": 10.0,
                            "path_nets": path_nets,
                            "normalized_slack": 1.0,
                        }
                    ],
                },
            )
            router = root / "emuflow_tlr_router"
            ratio_optimizer = root / "emuflow_tdm_ratio_optimizer"
            feedback_optimizer = (
                root / "emuflow_tdm_partition_feedback"
            )
            for source, output in (
                ("tlr_router.cpp", router),
                ("tdm_ratio_optimizer.cpp", ratio_optimizer),
                ("tdm_partition_feedback.cpp", feedback_optimizer),
            ):
                subprocess.run(
                    [
                        compiler,
                        "-std=c++17",
                        "-O2",
                        str(ROOT / "src" / "native" / source),
                        "-o",
                        str(output),
                    ],
                    check=True,
                )
            reports = []
            for run in range(2):
                output = root / f"run_{run}"
                report = run_cross_stage_optimization(
                    ir_path=ir_path,
                    platform_path=platform_path,
                    database_path=database_path,
                    initial_assignment_path=(
                        initial_root / "assignment.json"
                    ),
                    output_dir=output,
                    phase3_provider="greedy",
                    max_outer_iterations=1,
                    min_used_fpgas=2,
                    balance_tolerance=1.0,
                    router=str(router),
                    ratio_optimizer=str(ratio_optimizer),
                    feedback_optimizer=str(feedback_optimizer),
                    simulation_frames=2,
                    max_ratio=8,
                    post_refinement_iterations=10,
                )
                checked = validate_cross_stage_report(
                    output / "cross_stage_report.json",
                    ir_path,
                    database_path,
                    platform_path,
                )
                self.assertEqual(checked["status"], "pass")
                self.assertEqual(report["selected_iteration"], 0)
                self.assertEqual(
                    report["termination"], "line-search-rejected"
                )
                self.assertEqual(
                    report["configuration"][
                        "partition_timeout_seconds"
                    ],
                    3600,
                )
                self.assertEqual(len(report["candidates"]), 5)
                self.assertEqual(
                    [
                        candidate["feedback_step"]
                        for candidate in report["candidates"][1:]
                    ],
                    [1.0, 0.5, 0.25, 0.125],
                )
                self.assertTrue(
                    all(
                        not candidate["decision"]["accepted"]
                        for candidate in report["candidates"][1:]
                    )
                )
                self.assertTrue(
                    all(
                        candidate["partition_migration"][
                            "moved_clusters"
                        ]
                        == 0
                        for candidate in report["candidates"][1:]
                    )
                )
                reports.append(report)
            self.assertEqual(
                reports[0]["selected_candidate_id"],
                reports[1]["selected_candidate_id"],
            )
            self.assertEqual(
                reports[0]["candidates"][0]["objective_key"],
                reports[1]["candidates"][0]["objective_key"],
            )
            corrupted = copy.deepcopy(reports[0])
            corrupted["candidates"][1]["feedback_validation"][
                "maximum_feedback_weight"
            ] += 0.1
            corrupted_path = root / "run_0" / "corrupted_report.json"
            write_json(corrupted_path, corrupted)
            with self.assertRaisesRegex(
                ValidationError, "damped feedback validation mismatch"
            ):
                validate_cross_stage_report(
                    corrupted_path,
                    ir_path,
                    database_path,
                    platform_path,
                )
            corrupted = copy.deepcopy(reports[0])
            corrupted["configuration"][
                "partition_timeout_seconds"
            ] = 0
            write_json(corrupted_path, corrupted)
            with self.assertRaisesRegex(
                ValidationError, "partition timeout is invalid"
            ):
                validate_cross_stage_report(
                    corrupted_path,
                    ir_path,
                    database_path,
                    platform_path,
                )


if __name__ == "__main__":
    unittest.main()
