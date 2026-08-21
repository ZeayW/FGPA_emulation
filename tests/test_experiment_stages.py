import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from emuflow.cli import _build_parser
from emuflow.errors import EmuFlowError
from emuflow.experiment_stages import (
    _ValidationSession,
    _phase7_qor_projection,
    _physical_timing_databases,
    _placement_aware_positions,
    _prepare_empty_output,
    _sta_path_database,
    _timing_paths,
    resume_physical_lookahead,
)
from emuflow.experiment_upstream import (
    run_frontend_checkpoint,
    validate_frontend_checkpoint,
)
from emuflow.io import read_json, write_json
from emuflow.pin_planning import SIGNAL_POSITION_HINTS_SCHEMA
from emuflow.runtime import QOR_REPORT_SCHEMA


class ExperimentStagesTest(unittest.TestCase):
    def test_phase7_qor_projection_is_compact_and_rejects_nonfinite(self) -> None:
        qor = {
            "schema": QOR_REPORT_SCHEMA,
            "status": "pass",
            "design": "design",
            "platform": "platform",
            "timing": {
                "status": "pass",
                "qualification": "whole-design",
                "path_exactness": {"scheduled_link_tdm": True},
                "target_clock": {
                    "worst_slack_bound_ns": -2.0,
                    "total_negative_slack_bound_ns": -4.0,
                    "negative_slack_paths": 2,
                    "large_path_payload": [0] * 100,
                },
                "runtime_clock": {
                    "worst_slack_bound_ns": 1.0,
                    "total_negative_slack_bound_ns": 0.0,
                    "negative_slack_paths": 0,
                },
            },
            "physical": {
                "status": "pass",
                "worst_wns_ns": -0.5,
                "total_tns_ns": -1.5,
                "unrouted_nets": 0,
                "drc_violations": 0,
                "large_route_payload": [0] * 100,
            },
        }
        projection = _phase7_qor_projection(qor)
        self.assertNotIn(
            "large_path_payload", projection["timing"]["target_clock"]
        )
        self.assertNotIn("large_route_payload", projection["physical"])
        qor["timing"]["target_clock"]["worst_slack_bound_ns"] = float("nan")
        with self.assertRaisesRegex(Exception, "must be finite"):
            _phase7_qor_projection(qor)

    def test_validation_session_deduplicates_one_physical_report(self) -> None:
        report = {"schema": "fixture"}
        session = _ValidationSession()
        with mock.patch(
            "emuflow.experiment_stages.validate_multi_fpga_physical_report",
            return_value={"status": "pass"},
        ) as validate:
            first = session.validate_physical(report)
            first["status"] = "mutated-by-caller"
            second = session.validate_physical(report)
        validate.assert_called_once_with(report)
        self.assertEqual(second, {"status": "pass"})

    def test_shared_timing_uses_partition_projected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timing = root / "timing"
            timing.mkdir()
            self.assertIsNone(_sta_path_database(root))
            (timing / "path-database.json").write_text(
                "{}", encoding="utf-8"
            )
            self.assertEqual(
                _sta_path_database(root), timing / "path-database.json"
            )
            (timing / "cut-path-database.json").write_text(
                "{}", encoding="utf-8"
            )
            write_json(
                timing / "cut-timing-paths.json",
                {"source": {"input": "cut-path-database.json"}},
            )
            self.assertEqual(
                _physical_timing_databases(root),
                (
                    timing / "path-database.json",
                    timing / "cut-path-database.json",
                ),
            )
            projected = timing / "cut-timing-paths.json"
            self.assertEqual(_timing_paths(root), projected)
            write_json(
                projected,
                {"source": {"input": "path-database.json"}},
            )
            self.assertEqual(
                _physical_timing_databases(root),
                (
                    timing / "path-database.json",
                    timing / "path-database.json",
                ),
            )

    def test_physical_timing_requires_both_sta_database_namespaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timing = root / "timing"
            timing.mkdir()
            self.assertEqual(_physical_timing_databases(root), (None, None))
            (timing / "path-database.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "through-cut STA"):
                _physical_timing_databases(root)
            (timing / "path-database.json").unlink()
            (timing / "cut-path-database.json").write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaisesRegex(Exception, "complete original STA"):
                _physical_timing_databases(root)

    def test_physical_timing_rejects_unknown_projection_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timing = root / "timing"
            timing.mkdir()
            for name in ("path-database.json", "cut-path-database.json"):
                (timing / name).write_text("{}", encoding="utf-8")
            write_json(
                timing / "cut-timing-paths.json",
                {"source": {"input": "unsealed.json"}},
            )
            with self.assertRaisesRegex(Exception, "unknown STA database"):
                _physical_timing_databases(root)

    def test_frontend_checkpoint_is_reusable_and_tamper_evident(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        platform = repository / "platforms/virtual/xcvu3p_2fpga_p2p.json"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "frontend"
            report = run_frontend_checkpoint(
                platform,
                output,
                yosys_json=repository / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(report["source_artifacts"]), 1)
            self.assertTrue(
                (output / report["source_artifacts"][0]["artifact"]).is_file()
            )
            self.assertEqual(
                validate_frontend_checkpoint(output, platform)["status"], "pass"
            )
            (output / "phase1/design.emuir.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(Exception, "EmuIR seal"):
                validate_frontend_checkpoint(output, platform)

    def test_checkpoint_runner_accepts_precreated_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "staging"
            output.mkdir()
            self.assertEqual(
                _prepare_empty_output(output, "checkpoint"), output.resolve()
            )
            (output / "artifact").write_text("present", encoding="utf-8")
            with self.assertRaisesRegex(EmuFlowError, "must be an empty"):
                _prepare_empty_output(output, "checkpoint")

    def test_direct_stage_output_obeys_validation_server_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"EMUFLOW_REQUIRE_RESEARCH_STORAGE": "1"},
        ), mock.patch(
            "emuflow.experiment_storage.VALIDATION_STORAGE_ROOT",
            Path(temporary) / "allowed",
        ):
            with self.assertRaisesRegex(Exception, "restricted"):
                _prepare_empty_output(Path(temporary) / "outside", "checkpoint")

    def test_frontend_source_artifact_cannot_escape_checkpoint(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        platform = repository / "platforms/virtual/xcvu3p_2fpga_p2p.json"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "frontend"
            run_frontend_checkpoint(
                platform,
                output,
                yosys_json=repository / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
            )
            report_path = output / "experiment-frontend-report.json"
            report = read_json(report_path)
            report["source_artifacts"][0]["artifact"] = "sources/../../outside"
            write_json(report_path, report)
            with self.assertRaisesRegex(Exception, "path is invalid"):
                validate_frontend_checkpoint(output, platform)

    def test_placement_aware_positions_reuse_frozen_open_placement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ir = root / "ir.json"
            schedule = root / "schedule.json"
            placement = root / "placement.json"
            write_json(
                ir,
                {
                    "nets": [
                        {
                            "id": "n0",
                            "drivers": [{"instance": "a"}],
                            "sinks": [{"instance": "b"}],
                        }
                    ]
                },
            )
            write_json(
                schedule,
                {
                    "design": "d",
                    "platform": "p",
                    "entries": [
                        {"id": "e0", "net": "n0", "from": "f0", "to": "f1"}
                    ],
                },
            )
            write_json(
                placement,
                {
                    "fpgas": [
                        {
                            "fpga": "f0",
                            "instances": [{"id": "a", "normalised_y": 0.2}],
                        },
                        {
                            "fpga": "f1",
                            "instances": [{"id": "b", "normalised_y": 0.9}],
                        },
                    ]
                },
            )
            positions = _placement_aware_positions(
                ir, schedule, placement, region_count=4
            )
            self.assertEqual(positions["schema"], SIGNAL_POSITION_HINTS_SCHEMA)
            self.assertEqual(
                positions["entries"],
                [
                    {
                        "schedule_entry": "e0",
                        "source_y": 0.2,
                        "sink_y": 0.9,
                        "source_region": 0,
                        "sink_region": 3,
                        "source_fallback": False,
                        "sink_fallback": False,
                    }
                ],
            )

    def test_cli_exposes_provider_seed_checkpoint_commands(self) -> None:
        args = _build_parser().parse_args(
            [
                "experiment-stage",
                "phase7-run",
                "--shared",
                "shared",
                "--lookahead",
                "lookahead",
                "--phase6",
                "phase6",
                "--platform",
                "boarddb.json",
                "--seed",
                "3",
                "--workers",
                "8",
                "--out",
                "out",
            ]
        )
        self.assertEqual(args.seed, 3)
        self.assertEqual(args.workers, 8)
        validated = _build_parser().parse_args(
            [
                "experiment-stage",
                "phase7-validate",
                "result",
                "--shared",
                "shared",
                "--lookahead",
                "lookahead",
                "--phase6",
                "phase6",
                "--platform",
                "boarddb.json",
                "--seed",
                "3",
                "--workers",
                "8",
                "--route-channel-width",
                "300",
            ]
        )
        self.assertEqual(
            (validated.seed, validated.workers, validated.route_channel_width),
            (3, 8, 300),
        )

    def test_cli_exposes_fine_grained_phase1_5_commands(self) -> None:
        parser = _build_parser()
        timing = parser.parse_args(
            [
                "experiment-stage",
                "timing-run",
                "--frontend",
                "frontend",
                "--clock-period",
                "clk=10",
                "--out",
                "timing",
            ]
        )
        self.assertEqual(timing.clock_period, ["clk=10"])
        shared = parser.parse_args(
            [
                "experiment-stage",
                "shared-materialize",
                "--frontend",
                "f",
                "--timing",
                "t",
                "--partition",
                "p",
                "--cut-timing",
                "c",
                "--route",
                "r",
                "--tdm",
                "d",
                "--platform",
                "board.json",
                "--out",
                "shared",
            ]
        )
        self.assertEqual(shared.experiment_stage_command, "shared-materialize")

    def test_baseline_phase6_does_not_require_lookahead(self) -> None:
        args = _build_parser().parse_args(
            [
                "experiment-stage",
                "phase6-run",
                "--shared",
                "shared",
                "--platform",
                "boarddb.json",
                "--provider",
                "baseline",
                "--out",
                "out",
            ]
        )
        self.assertIsNone(args.lookahead)
        self.assertEqual(args.provider, "baseline")

    def test_lookahead_can_bind_a_baseline_phase6_checkpoint(self) -> None:
        args = _build_parser().parse_args(
            [
                "experiment-stage",
                "lookahead-run",
                "--shared",
                "shared",
                "--baseline-phase6",
                "baseline-phase6",
                "--platform",
                "boarddb.json",
                "--out",
                "out",
            ]
        )
        self.assertEqual(args.baseline_phase6, Path("baseline-phase6"))

    def test_cli_exposes_resumed_physical_lookahead(self) -> None:
        args = _build_parser().parse_args(
            [
                "experiment-stage",
                "lookahead-resume",
                "--shared",
                "shared",
                "--baseline-phase6",
                "baseline",
                "--platform",
                "boarddb.json",
                "--seed",
                "2",
                "--workers",
                "6",
                "--out",
                "recovered",
            ]
        )
        self.assertEqual(args.experiment_stage_command, "lookahead-resume")
        self.assertEqual((args.seed, args.workers), (2, 6))

    def test_cli_exposes_distinct_physical_timing_databases(self) -> None:
        args = _build_parser().parse_args(
            [
                "multi-fpga",
                "physical",
                "--split",
                "split",
                "--platform",
                "boarddb.json",
                "--schedule",
                "schedule.json",
                "--path-database",
                "full.json",
                "--logic-path-database",
                "through-cut.json",
                "--out",
                "physical",
            ]
        )
        self.assertEqual(args.path_database, Path("full.json"))
        self.assertEqual(args.logic_path_database, Path("through-cut.json"))

    def test_resumed_lookahead_requires_only_a_physical_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "recovered"
            physical = root / "physical"
            physical.mkdir(parents=True)
            write_json(
                physical / "multi-fpga-physical-flow-report.json",
                {"schema": "placeholder"},
            )
            with mock.patch(
                "emuflow.experiment_stages._finish_physical_lookahead",
                return_value={"status": "pass"},
            ) as finish:
                report = resume_physical_lookahead(
                    Path("shared"),
                    Path("baseline"),
                    Path("platform"),
                    root,
                    seed=1,
                    workers=8,
                    region_count=4,
                )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(finish.call_args.args[4], {"schema": "placeholder"})

            (root / "unrelated").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "contain only physical"):
                resume_physical_lookahead(
                    Path("shared"),
                    Path("baseline"),
                    Path("platform"),
                    root,
                    seed=1,
                    workers=8,
                    region_count=4,
                )

    def test_resumed_lookahead_rebases_sealed_attempt_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "recovered"
            physical = root / "physical"
            physical.mkdir(parents=True)
            old_root = Path(temporary) / "attempt/output/physical"
            report = {
                "schema": "fixture",
                "fpgas": [
                    {
                        "fpga": "FPGA0",
                        "stages": {
                            "placement_ir": {
                                "output": str(old_root / "FPGA0/placement-ir.json")
                            },
                            "openparf_placement": {
                                "artifacts": {
                                    "vpr_placement": str(
                                        old_root / "FPGA0/openparf/design.place"
                                    )
                                }
                            },
                        },
                    }
                ],
                "external_source": "/research/example/input.json",
            }
            write_json(
                physical / "multi-fpga-physical-flow-report.json", report
            )
            with mock.patch(
                "emuflow.experiment_stages._finish_physical_lookahead",
                return_value={"status": "pass"},
            ) as finish:
                resume_physical_lookahead(
                    Path("shared"),
                    Path("baseline"),
                    Path("platform"),
                    root,
                    seed=1,
                    workers=8,
                    region_count=4,
                )
            rebased = finish.call_args.args[4]
            self.assertEqual(
                rebased["fpgas"][0]["stages"]["placement_ir"]["output"],
                str(physical.resolve() / "FPGA0/placement-ir.json"),
            )
            self.assertEqual(
                rebased["fpgas"][0]["stages"]["openparf_placement"]
                ["artifacts"]["vpr_placement"],
                str(physical.resolve() / "FPGA0/openparf/design.place"),
            )
            self.assertEqual(
                rebased["external_source"], "/research/example/input.json"
            )
            self.assertEqual(
                read_json(
                    physical / "multi-fpga-physical-flow-report.json"
                ),
                rebased,
            )


if __name__ == "__main__":
    unittest.main()
