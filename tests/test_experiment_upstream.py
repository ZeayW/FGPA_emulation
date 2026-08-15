import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from emuflow.errors import ValidationError
from emuflow.experiment_partition import validate_partition_checkpoint
from emuflow.experiment_upstream import (
    EXPERIMENT_PARTITION_SCHEMA,
    EXPERIMENT_TDM_SCHEMA,
    validate_tdm_checkpoint,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExperimentUpstreamTest(unittest.TestCase):
    def _tdm_fixture(
        self,
        root: Path,
        *,
        provider: str,
        optimization_provider: str | None = None,
    ) -> tuple[Path, Path, Path]:
        route = root / "route"
        tdm = root / "tdm"
        platform = root / "platform.json"
        route.mkdir()
        tdm.mkdir()
        (route / "routes.json").write_text("{}", encoding="utf-8")
        platform.write_text("{}", encoding="utf-8")
        (tdm / "schedule.json").write_text("{}", encoding="utf-8")
        phase5 = {"provider": provider}
        if optimization_provider is not None:
            phase5["optimization_provider"] = optimization_provider
        (tdm / "phase5_report.json").write_text(
            json.dumps(phase5), encoding="utf-8"
        )
        report = {
            "schema": EXPERIMENT_TDM_SCHEMA,
            "status": "pass",
            "routes_sha256": _sha256(route / "routes.json"),
            "platform_sha256": _sha256(platform),
            "schedule_sha256": _sha256(tdm / "schedule.json"),
            "phase5_report_sha256": _sha256(tdm / "phase5_report.json"),
            "phase5": phase5,
        }
        (tdm / "experiment-tdm-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return route, platform, tdm

    def _partition_fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        frontend = root / "frontend"
        timing = root / "timing"
        partition = root / "partition"
        platform = root / "platform.json"
        (frontend / "phase1").mkdir(parents=True)
        timing.mkdir()
        partition.mkdir()
        (frontend / "phase1/design.emuir.json").write_text(
            "{}", encoding="utf-8"
        )
        (timing / "partition-net-weights.json").write_text(
            "{}", encoding="utf-8"
        )
        platform.write_text("{}", encoding="utf-8")
        (partition / "clusters.json").write_text("{}", encoding="utf-8")
        assignment = {
            "provider_metadata": {
                "seed_attempts": [
                    {"mode": "timing_weighted", "seed": 0},
                    {"mode": "timing_weighted", "seed": 1},
                    {"mode": "unweighted_baseline", "seed": 0},
                ],
                "balance_repair": {"enabled": True, "summary": {}},
            }
        }
        (partition / "assignment.json").write_text(
            json.dumps(assignment), encoding="utf-8"
        )
        (partition / "phase3_report.json").write_text("{}", encoding="utf-8")
        report = {
            "schema": EXPERIMENT_PARTITION_SCHEMA,
            "status": "pass",
            "provider": "tritonpart",
            "seed": 0,
            "seed_attempts": 2,
            "repair_balance": True,
            "emuir_sha256": _sha256(
                frontend / "phase1/design.emuir.json"
            ),
            "platform_sha256": _sha256(platform),
            "weights_sha256": _sha256(
                timing / "partition-net-weights.json"
            ),
            "assignment_sha256": _sha256(partition / "assignment.json"),
            "clusters_sha256": _sha256(partition / "clusters.json"),
            "phase3_report_sha256": _sha256(
                partition / "phase3_report.json"
            ),
            "route_constraints_sha256": None,
        }
        (partition / "experiment-partition-report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return frontend, timing, platform, partition

    @mock.patch("emuflow.experiment_partition.validate_phase3")
    def test_partition_validator_binds_seed_sweep_and_repair(
        self, validate_phase3
    ) -> None:
        validate_phase3.return_value = {"status": "pass"}
        with tempfile.TemporaryDirectory() as temporary:
            frontend, timing, platform, partition = self._partition_fixture(
                Path(temporary)
            )
            checked = validate_partition_checkpoint(
                frontend,
                timing,
                platform,
                partition,
                expected_provider="tritonpart",
                expected_seed=0,
                expected_seed_attempts=2,
                expected_repair_balance=True,
            )
            self.assertEqual(checked["status"], "pass")

    @mock.patch("emuflow.experiment_partition.validate_phase3")
    def test_partition_validator_rejects_resealed_policy_mismatch(
        self, validate_phase3
    ) -> None:
        validate_phase3.return_value = {"status": "pass"}
        with tempfile.TemporaryDirectory() as temporary:
            frontend, timing, platform, partition = self._partition_fixture(
                Path(temporary)
            )
            report_path = partition / "experiment-partition-report.json"
            report = json.loads(report_path.read_text())
            report["seed_attempts"] = 3
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                ValidationError, "seed-attempt report disagrees"
            ):
                validate_partition_checkpoint(
                    frontend, timing, platform, partition
                )

            report["seed_attempts"] = 2
            report["repair_balance"] = False
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                ValidationError, "balance-repair report disagrees"
            ):
                validate_partition_checkpoint(
                    frontend, timing, platform, partition
                )

    @mock.patch("emuflow.experiment_upstream.validate_phase5")
    def test_tdm_validator_binds_academic_optimization_provider(
        self, validate_phase5
    ) -> None:
        validate_phase5.return_value = {"status": "pass"}
        with tempfile.TemporaryDirectory() as temporary:
            route, platform, tdm = self._tdm_fixture(
                Path(temporary),
                provider="lagrangian-kkt-ratio-aware-list-schedule-v1",
                optimization_provider="aspdac26-timing-dag-lagrangian-v1",
            )
            checked = validate_tdm_checkpoint(
                route,
                platform,
                tdm,
                expected_provider="aspdac26-timing-dag-lagrangian-v1",
            )
            self.assertEqual(checked["status"], "pass")
            with self.assertRaisesRegex(
                ValidationError, "provider contract disagrees"
            ):
                validate_tdm_checkpoint(
                    route,
                    platform,
                    tdm,
                    expected_provider=(
                        "lagrangian-kkt-ratio-aware-list-schedule-v1"
                    ),
                )

    @mock.patch("emuflow.experiment_upstream.validate_phase5")
    def test_tdm_validator_preserves_baseline_provider_contract(
        self, validate_phase5
    ) -> None:
        validate_phase5.return_value = {"status": "pass"}
        with tempfile.TemporaryDirectory() as temporary:
            route, platform, tdm = self._tdm_fixture(
                Path(temporary), provider="static-tdm-v2"
            )
            checked = validate_tdm_checkpoint(
                route,
                platform,
                tdm,
                expected_provider="static-tdm-v2",
            )
            self.assertEqual(checked["status"], "pass")


if __name__ == "__main__":
    unittest.main()
