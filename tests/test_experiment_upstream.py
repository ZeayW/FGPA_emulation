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
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExperimentUpstreamTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
