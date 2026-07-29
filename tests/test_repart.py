import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.partition import (
    assign_clusters,
    build_clusters,
    normalize_partition_constraints,
)
from emuflow.phase3 import run_phase3
from emuflow.platform import Platform
from emuflow.repart import (
    REPART_FIXED_SEED,
    REPART_INPUT_SCHEMA,
    export_repart_inputs,
    parse_repart_solution,
)
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


class RePartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        self.platform = Platform.load(PLATFORM_PATH)
        self.constraints = normalize_partition_constraints(
            {
                "schema": "emuflow.partition-constraints/v1",
                "min_used_fpgas": 2,
                "balance_tolerance": 1.0,
            },
            self.ir,
            self.platform,
        )
        self.clusters = build_clusters(self.ir, self.constraints)

    def _write_solution(
        self,
        path: Path,
        assignment,
        replica=None,
    ) -> None:
        by_fpga = {fpga.id: [] for fpga in self.platform.fpgas}
        for cluster_id, fpga_id in sorted(assignment.items()):
            by_fpga[fpga_id].append(cluster_id)
        if replica is not None:
            cluster_id, fpga_id = replica
            by_fpga[fpga_id].append(f"{cluster_id}*")
        path.write_text(
            "".join(
                f"{fpga_id}: {' '.join(by_fpga[fpga_id])}\n"
                for fpga_id in by_fpga
            ),
            encoding="utf-8",
        )

    def test_export_writes_repart_native_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            artifact = export_repart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                output,
            )
            self.assertEqual(artifact["schema"], REPART_INPUT_SCHEMA)
            self.assertFalse(artifact["replication_enabled"])
            self.assertEqual(len(artifact["resource_dimensions"]), 8)
            self.assertGreater(len(artifact["hyperedges"]), 0)
            for filename in artifact["files"].values():
                if filename == "design.fpga.out":
                    continue
                self.assertTrue((output / filename).is_file())
            for line in (output / "design.are").read_text().splitlines():
                self.assertEqual(len(line.split()), 9)
            for line in (output / "design.info").read_text().splitlines():
                self.assertEqual(len(line.split()), 10)
            topology = (output / "design.topo").read_text().splitlines()
            self.assertEqual(topology[0], "1")
            self.assertEqual(topology[1], "fpga0 fpga1")

    def test_parser_separates_primary_and_replica_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            artifact = export_repart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                output,
            )
            assignment = assign_clusters(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                seed=7,
            )["cluster_assignment"]
            cluster_id = next(iter(sorted(assignment)))
            replica_fpga = (
                "fpga1" if assignment[cluster_id] == "fpga0" else "fpga0"
            )
            solution = output / "solution.out"
            self._write_solution(
                solution,
                assignment,
                replica=(cluster_id, replica_fpga),
            )
            parsed, replicas = parse_repart_solution(solution, artifact)
            self.assertEqual(parsed, assignment)
            self.assertEqual(replicas, {cluster_id: [replica_fpga]})

    def test_parser_rejects_missing_primary_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            artifact = export_repart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                output,
            )
            solution = output / "solution.out"
            solution.write_text("fpga0:\nfpga1:\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "exact coverage"):
                parse_repart_solution(solution, artifact)

    def test_phase3_imports_unique_owner_repart_solution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            ir_path = output / "design.emuir.json"
            ir_path.write_text(
                json.dumps(self.ir.to_dict()), encoding="utf-8"
            )
            baseline = assign_clusters(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                seed=9,
            )
            solution = output / "repart.out"
            self._write_solution(
                solution,
                baseline["cluster_assignment"],
            )
            report = run_phase3(
                ir_path=ir_path,
                platform_path=PLATFORM_PATH,
                output_dir=output / "phase3",
                balance_tolerance=1.0,
                provider="repart",
                repart_solution=solution,
            )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                report["provider"], "repart-fpga-aware-multilevel-v1"
            )
            self.assertEqual(report["seed"], REPART_FIXED_SEED)
            self.assertEqual(report["validation"]["used_fpgas"], 2)
            self.assertTrue(
                (output / "phase3" / "repart" / "repart_input.json").is_file()
            )

    def test_phase3_rejects_replication_in_partition_only_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            ir_path = output / "design.emuir.json"
            ir_path.write_text(
                json.dumps(self.ir.to_dict()), encoding="utf-8"
            )
            baseline = assign_clusters(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                seed=9,
            )
            assignment = baseline["cluster_assignment"]
            cluster_id = next(iter(sorted(assignment)))
            replica_fpga = (
                "fpga1" if assignment[cluster_id] == "fpga0" else "fpga0"
            )
            solution = output / "repart.out"
            self._write_solution(
                solution,
                assignment,
                replica=(cluster_id, replica_fpga),
            )
            with self.assertRaisesRegex(
                ValidationError, "replication-disabled"
            ):
                run_phase3(
                    ir_path=ir_path,
                    platform_path=PLATFORM_PATH,
                    output_dir=output / "phase3",
                    balance_tolerance=1.0,
                    provider="repart",
                    repart_solution=solution,
                )


if __name__ == "__main__":
    unittest.main()
