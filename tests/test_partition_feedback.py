import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.partition_feedback import (
    build_partition_feedback,
    validate_partition_feedback,
)
from emuflow.platform import Platform
from emuflow.tdm_ratio import build_tdm_ratio_plan
from tests.test_phase5 import _link, _platform_value, _routes


ROOT = Path(__file__).resolve().parents[1]


class PartitionFeedbackTest(unittest.TestCase):
    def test_channel_usage_feedback_is_reproducible_and_checked(self) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a C++17 compiler is required")
        platform = Platform.from_dict(
            _platform_value(
                "feedback",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=2, latency=1)],
            )
        )
        routes = _routes(
            platform,
            [(f"n{index:02d}", "a", ["b"]) for index in range(17)],
            frame_slots=32,
        )
        routes["timing"] = {
            "schema": "emuflow.sta-paths/v1",
            "normalization": {
                "positive_slack_scale_ns": 20.0,
                "negative_slack_scale_ns": 20.0,
                "max_clock_period_ns": 100.0,
            },
            "compression": {
                "original_paths": 2,
                "compressed_paths": 2,
            },
            "paths": [
                {
                    "path": "critical",
                    "clock_domain": "fast",
                    "clock_period_ns": 20.0,
                    "fixed_delay_ns": 12.0,
                    "cut_nets": ["n00"],
                },
                {
                    "path": "relaxed",
                    "clock_domain": "slow",
                    "clock_period_ns": 100.0,
                    "fixed_delay_ns": 0.0,
                    "cut_nets": ["n01"],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ratio_optimizer = root / "emuflow_tdm_ratio_optimizer"
            feedback_optimizer = root / "emuflow_tdm_partition_feedback"
            for source, output in (
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
            plan = build_tdm_ratio_plan(
                routes,
                platform,
                executable=str(ratio_optimizer),
                max_ratio=16,
                post_refinement_iterations=20,
            )
            artifact = build_partition_feedback(
                routes,
                plan,
                platform,
                executable=str(feedback_optimizer),
            )
            repeated = build_partition_feedback(
                routes,
                plan,
                platform,
                executable=str(feedback_optimizer),
            )
            self.assertEqual(artifact, repeated)
            checked = validate_partition_feedback(
                routes, plan, platform, artifact
            )
            self.assertEqual(checked["status"], "pass")
            self.assertGreater(
                artifact["weights"]["n00"], artifact["weights"]["n01"]
            )
            by_net = {record["net"]: record for record in artifact["records"]}
            self.assertEqual(by_net["n00"]["group_size"], 1)
            self.assertEqual(by_net["n01"]["group_size"], 16)

            corrupted = copy.deepcopy(artifact)
            corrupted["records"][0]["channel_usage"] += 0.1
            with self.assertRaisesRegex(
                ValidationError, "independent reconstruction"
            ):
                validate_partition_feedback(
                    routes, plan, platform, corrupted
                )

            corrupted = copy.deepcopy(artifact)
            corrupted["configuration"]["pair_pressure_weight"] = float("nan")
            with self.assertRaisesRegex(
                ValidationError, "pair pressure weight"
            ):
                validate_partition_feedback(
                    routes, plan, platform, corrupted
                )

            corrupted = copy.deepcopy(artifact)
            corrupted["slack_range"]["minimum"] += 0.1
            with self.assertRaisesRegex(ValidationError, "slack range"):
                validate_partition_feedback(
                    routes, plan, platform, corrupted
                )


if __name__ == "__main__":
    unittest.main()
