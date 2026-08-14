import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.canonical_qor import (
    parse_canonical_qor_arms,
    run_canonical_qor_comparison,
    validate_canonical_qor_comparison,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json
from emuflow.runtime import QOR_REPORT_SCHEMA


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CanonicalQorTest(unittest.TestCase):
    def _fixture(self, root: Path):
        shared = root / "shared"
        for relative in (
            "frontend/phase1/design.emuir.json",
            "partition/assignment.json",
            "system-route/routes.json",
            "tdm/schedule.json",
        ):
            path = shared / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, {"path": relative})
        frozen = {
            "emuir_sha256": _sha256(
                shared / "frontend/phase1/design.emuir.json"
            ),
            "assignment_sha256": _sha256(
                shared / "partition/assignment.json"
            ),
            "routes_sha256": _sha256(shared / "system-route/routes.json"),
            "schedule_sha256": _sha256(shared / "tdm/schedule.json"),
        }
        roots = {}
        records = []
        offsets = {"baseline": 0.0, "placement-aware": 0.1, "chimew": 0.2}
        for provider in ("baseline", "placement-aware", "chimew"):
            for seed in (1, 2, 3):
                arm = root / f"{provider}-{seed}"
                (arm / "physical").mkdir(parents=True)
                (arm / "runtime").mkdir()
                summary = arm / "physical/physical-summary.json"
                physical_report = (
                    arm / "physical/multi-fpga-physical-flow-report.json"
                )
                write_json(summary, {"provider": provider, "seed": seed})
                write_json(physical_report, {"status": "pass"})
                offset = offsets[provider]
                qor = {
                    "schema": QOR_REPORT_SCHEMA,
                    "status": "pass",
                    "design": "DLA",
                    "platform": "eda2023-case6-rtl",
                    "timing": {
                        "status": "pass",
                        "qualification": "test-whole-design-bound",
                        "path_exactness": {"scheduled_link_tdm": True},
                        "target_clock": {
                            "worst_slack_bound_ns": -1.0 + offset,
                            "total_negative_slack_bound_ns": -3.0 + offset,
                            "negative_slack_paths": 3,
                        },
                        "runtime_clock": {
                            "worst_slack_bound_ns": 2.0 + offset,
                            "total_negative_slack_bound_ns": 0.0,
                            "negative_slack_paths": 0,
                        },
                    },
                    "physical": {
                        "status": "pass",
                        "unrouted_nets": 0,
                        "drc_violations": 0,
                        "worst_wns_ns": -0.5 + offset,
                        "total_tns_ns": -1.5 + offset,
                    },
                }
                qor_path = arm / "runtime/qor_report.json"
                write_json(qor_path, qor)
                write_json(
                    arm / "experiment-phase7-report.json",
                    {
                        "schema": "emuflow.experiment-phase7-checkpoint/v1",
                        "status": "pass",
                        "provider": provider,
                        "physical_seed": seed,
                        "frozen_upstream": frozen,
                        "physical_summary_sha256": _sha256(summary),
                        "qor_sha256": _sha256(qor_path),
                        "qor": qor,
                    },
                )
                roots[(provider, seed)] = arm
                records.append((provider, str(seed), str(arm)))
        return shared, roots, records

    @patch(
        "emuflow.canonical_qor.validate_multi_fpga_physical_report",
        return_value={"status": "pass"},
    )
    def test_nine_arm_comparison_is_paired_and_replayable(self, _validate):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared, arms, records = self._fixture(root)
            self.assertEqual(
                parse_canonical_qor_arms(records),
                {key: value.resolve() for key, value in arms.items()},
            )
            output = root / "comparison"
            report = run_canonical_qor_comparison(shared, arms, output)
            self.assertEqual(len(report["arms"]), 9)
            self.assertEqual(
                report["comparisons"]["chimew"]["target_clock_result"],
                "improved",
            )
            self.assertAlmostEqual(
                report["comparisons"]["chimew"]["mean_deltas"][
                    "global_target_clock_wns_ns"
                ],
                0.2,
            )
            self.assertEqual(
                validate_canonical_qor_comparison(output, shared, arms)["arms"],
                9,
            )

            tampered = read_json(
                arms[("chimew", 3)] / "experiment-phase7-report.json"
            )
            tampered["frozen_upstream"]["routes_sha256"] = "0" * 64
            write_json(
                arms[("chimew", 3)] / "experiment-phase7-report.json",
                tampered,
            )
            with self.assertRaisesRegex(ValidationError, "arm seal"):
                validate_canonical_qor_comparison(output, shared, arms)

    def test_arm_parser_rejects_incomplete_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            with self.assertRaisesRegex(ValidationError, "exactly nine"):
                parse_canonical_qor_arms([["baseline", "1", str(root)]])


if __name__ == "__main__":
    unittest.main()
