import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from emuflow.cli import _build_parser
from emuflow.errors import EmuFlowError
from emuflow.experiment_stages import (
    _placement_aware_positions,
    _prepare_empty_output,
    _projected_timing_paths,
    _timing_paths,
    _validate_managed_phase6_checkpoint,
)
from emuflow.experiment_upstream import (
    run_frontend_checkpoint,
    validate_frontend_checkpoint,
)
from emuflow.io import read_json, write_json
from emuflow.pin_planning import SIGNAL_POSITION_HINTS_SCHEMA


class ExperimentStagesTest(unittest.TestCase):
    def test_shared_checkpoint_uses_projected_paths_for_phase4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            timing = root / "timing"
            timing.mkdir()
            cut_database = timing / "cut-path-database.json"
            projected = timing / "cut-timing-paths.json"
            write_json(cut_database, {"schema": "emuflow.sta-path-database/v1"})
            write_json(projected, {"schema": "emuflow.sta-paths/v1"})
            self.assertEqual(_timing_paths(root), cut_database)
            self.assertEqual(_projected_timing_paths(root), projected)

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
                "--reuse-validated-phase6-equivalence",
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
        self.assertTrue(args.reuse_validated_phase6_equivalence)
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
                "--reuse-validated-phase6-equivalence",
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
        self.assertTrue(validated.reuse_validated_phase6_equivalence)

    def test_phase6_equivalence_reuse_requires_managed_validation_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            object_root = Path(temporary) / ("a" * 64)
            output = object_root / "output"
            output.mkdir(parents=True)
            marker = output / "marker.json"
            marker.write_text("{}\n", encoding="utf-8")
            digest = hashlib.sha256(marker.read_bytes()).hexdigest()
            checkpoint = {
                "schema": "emuflow.experiment-checkpoint/v2",
                "status": "pass",
                "execution_key": "a" * 64,
                "node_id": "phase6-baseline",
                "stage": "phase6",
                "provider": "baseline",
                "physical_seed": None,
                "dependency_keys": {},
                "storage": "managed",
                "output_immutable": True,
                "output_dir": str(output.resolve()),
                "expected_artifacts": [
                    {
                        "path": "marker.json",
                        "role": "evidence-critical",
                        "retention": "required",
                    }
                ],
                "artifacts": {
                    "marker.json": {
                        "kind": "file",
                        "sha256": digest,
                        "bytes": marker.stat().st_size,
                    }
                },
            }
            write_json(object_root / "checkpoint.json", checkpoint)
            validation_key = "b" * 64
            write_json(
                object_root / "validations" / f"{validation_key}.json",
                {
                    "schema": "emuflow.experiment-validation/v1",
                    "execution_key": "a" * 64,
                    "validation_key": validation_key,
                    "status": "pass",
                },
            )
            os.chmod(marker, 0o444)
            os.chmod(output, 0o555)
            self.assertEqual(
                _validate_managed_phase6_checkpoint(output)["stage"], "phase6"
            )
            os.chmod(output, 0o755)
            with self.assertRaisesRegex(Exception, "writable|immutable"):
                _validate_managed_phase6_checkpoint(output)

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


if __name__ == "__main__":
    unittest.main()
