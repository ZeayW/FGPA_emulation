import copy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.mfspart_legalize import (
    _normalise_problem,
    legalize_mfspart_min_used,
    validate_mfspart_legalization,
)


ROOT = Path(__file__).resolve().parents[1]


class MFSPartLegalizerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a C++17 compiler is required")
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.temporary_directory.name) / "legalizer"
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-Werror",
                str(ROOT / "src/native/mfspart_legalizer.cpp"),
                "-o",
                str(cls.executable),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    @staticmethod
    def _problem(fixed=False):
        graph = {
            "nodes": [
                {"fixed_part": 0 if fixed else -1, "weights": [1]},
                {"fixed_part": 0 if fixed else -1, "weights": [1]},
                {"fixed_part": 0 if fixed else -1, "weights": [1]},
            ],
            "nets": [{"weight": 1.0, "source": 0, "sinks": [1, 2]}],
        }
        parts = ["F0", "F1"]
        capacities = {"F0": {"cells": 3}, "F1": {"cells": 3}}
        return graph, parts, capacities

    def test_native_fills_empty_part_and_oracle_replays_move(self) -> None:
        graph, parts, capacities = self._problem()
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = legalize_mfspart_min_used(
                graph,
                ["cells"],
                parts,
                capacities,
                [0, 0, 0],
                2,
                Path(temporary_directory),
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"], {"status": "pass", "moves": 1, "used_parts": 2})
        self.assertEqual(artifact["assignment"], [0, 1, 0])
        self.assertEqual(artifact["moves"][0]["pair_cut_delta"], 1.0)
        self.assertEqual(artifact["moves"][0]["connectivity_delta"], 1.0)

    def test_no_legal_move_is_reported(self) -> None:
        graph, parts, capacities = self._problem(fixed=True)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(EmuFlowError, "no legal move"):
                legalize_mfspart_min_used(
                    graph,
                    ["cells"],
                    parts,
                    capacities,
                    [0, 0, 0],
                    2,
                    Path(temporary_directory),
                    executable=str(self.executable),
                )

    def test_independent_oracle_rejects_corrupt_delta(self) -> None:
        graph, parts, capacities = self._problem()
        problem = _normalise_problem(
            graph, ["cells"], parts, capacities, [0, 0, 0], 2
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = legalize_mfspart_min_used(
                graph,
                ["cells"],
                parts,
                capacities,
                [0, 0, 0],
                2,
                Path(temporary_directory),
                executable=str(self.executable),
            )
        corrupt = copy.deepcopy(artifact)
        corrupt["moves"][0]["pair_cut_delta"] += 1.0
        with self.assertRaisesRegex(ValidationError, "delta mismatch"):
            validate_mfspart_legalization(corrupt, problem)


if __name__ == "__main__":
    unittest.main()
