import tempfile
import unittest
from pathlib import Path

from emuflow.contest_eda2024 import evaluate_eda2024_solution
from emuflow.errors import ValidationError


INFO = """\
F1 10 8 8 0 0 0 0 0 0
F2 10 8 8 0 0 0 0 0 0
F3 10 8 8 0 0 0 0 0 0
"""

AREA = """\
a 1 1 0 0 0 0 0 0
b 1 1 0 0 0 0 0 0
c 1 1 0 0 0 0 0 0
d 1 1 0 0 0 0 0 0
"""

NETS = """\
a 3 b c
d 2 a
"""

TOPOLOGY = """\
2
F1 F2
F2 F3
"""

PRIMARY_SOLUTION = """\
F1: a d
F2:
F3: b c
"""

REPLICATED_SOLUTION = """\
F1: a d
F2:
F3: b c a*
"""


class Eda2024ContestCheckerTest(unittest.TestCase):
    def _write_case(self, root: Path, solution: str = PRIMARY_SOLUTION):
        values = {
            "design.info": INFO,
            "design.are": AREA,
            "design.net": NETS,
            "design.topo": TOPOLOGY,
            "design.fpga.out": solution,
        }
        for name, value in values.items():
            (root / name).write_text(value, encoding="utf-8")
        return {
            "info_path": root / "design.info",
            "area_path": root / "design.are",
            "net_path": root / "design.net",
            "topology_path": root / "design.topo",
            "solution_path": root / "design.fpga.out",
        }

    def test_checker_recomputes_weighted_hop_and_communication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(root)
            report = evaluate_eda2024_solution(
                **paths, runtime_seconds=18.0, output_path=root / "report.json"
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["metrics"]["total_hop_distance"], 6)
            self.assertEqual(report["metrics"]["cut_hyperedges"], 1)
            self.assertEqual(report["metrics"]["remote_fpga_sinks"], 1)
            self.assertEqual(report["metrics"]["replica_copies"], 0)
            self.assertAlmostEqual(
                report["metrics"]["contest_score"],
                6 * (1.0 + 0.2 * 18.0 / 3600.0),
            )
            usage = {
                record["fpga"]: record["used"]
                for record in report["communication"]
            }
            self.assertEqual(usage, {"F1": 3, "F2": 0, "F3": 3})
            self.assertTrue((root / "report.json").is_file())

    def test_source_replica_removes_outputs_but_receives_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(root, REPLICATED_SOLUTION)
            report = evaluate_eda2024_solution(**paths)
            # a* makes a->(b,c) local on F3, while d->a must now also feed F3.
            self.assertEqual(report["metrics"]["total_hop_distance"], 4)
            self.assertEqual(report["metrics"]["replicated_modules"], 1)
            self.assertEqual(report["metrics"]["replica_copies"], 1)
            resources = {
                record["fpga"]: record["used"] for record in report["resources"]
            }
            self.assertEqual(resources["F3"]["FF"], 3)
            usage = {
                record["fpga"]: record["used"]
                for record in report["communication"]
            }
            self.assertEqual(usage, {"F1": 2, "F2": 0, "F3": 2})

    def test_checker_rejects_maximum_hop_violation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(root)
            (root / "design.topo").write_text(
                "1\nF1 F2\nF2 F3\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "above maximum"):
                evaluate_eda2024_solution(**paths)

    def test_checker_rejects_replica_resource_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(root, REPLICATED_SOLUTION)
            (root / "design.info").write_text(
                INFO.replace("F3 10 8 8", "F3 10 2 8"), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "resource overflow"):
                evaluate_eda2024_solution(**paths)

    def test_checker_rejects_external_communication_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(root)
            (root / "design.info").write_text(
                INFO.replace("F1 10", "F1 2"), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValidationError, "external communication overflow"
            ):
                evaluate_eda2024_solution(**paths)

    def test_checker_rejects_solution_without_exact_area_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_case(
                root,
                "F1: a d ghost\nF2:\nF3: b c\n",
            )
            with self.assertRaisesRegex(ValidationError, "absent from design.are"):
                evaluate_eda2024_solution(**paths)


if __name__ == "__main__":
    unittest.main()
