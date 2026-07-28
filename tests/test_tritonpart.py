import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from emuflow.errors import ValidationError
from emuflow.ir import EmuIR
from emuflow.partition import (
    build_clusters,
    normalize_partition_constraints,
)
from emuflow.phase3 import run_phase3
from emuflow.platform import Platform
from emuflow.tritonpart import (
    TRITONPART_INPUT_SCHEMA,
    _repair_min_used_fpgas,
    export_tritonpart_inputs,
    parse_tritonpart_solution,
)
from emuflow.yosys import import_yosys_json


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


class TritonPartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = import_yosys_json(
            ROOT / "examples" / "yosys" / "counter.json",
            top="counter",
            clocks=["clk"],
        )
        self.platform = Platform.load(PLATFORM_PATH)
        self.constraints = normalize_partition_constraints(
            None, self.ir, self.platform
        )
        self.clusters = build_clusters(self.ir, self.constraints)

    def test_export_is_weighted_multiresource_hypergraph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            artifact = export_tritonpart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                output,
            )
            self.assertEqual(artifact["schema"], TRITONPART_INPUT_SCHEMA)
            self.assertEqual(artifact["fpga_order"], ["fpga0", "fpga1"])
            self.assertEqual(artifact["vertex_dimensions"][0], "cells")
            self.assertIn("lut", artifact["vertex_dimensions"])
            self.assertIn("ff", artifact["vertex_dimensions"])
            self.assertGreater(len(artifact["hyperedges"]), 0)

            lines = (output / "partition.hgr").read_text().splitlines()
            edge_count, vertex_count, weight_flag = map(
                int, lines[0].split()
            )
            self.assertEqual(edge_count, len(artifact["hyperedges"]))
            self.assertEqual(vertex_count, len(artifact["cluster_order"]))
            self.assertEqual(weight_flag, 11)
            self.assertEqual(
                len((output / "partition.fix").read_text().splitlines()),
                vertex_count,
            )

    def test_solution_parser_rejects_invalid_part(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            artifact = export_tritonpart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                output,
            )
            solution = output / "bad.part"
            solution.write_text(
                "\n".join(["2"] * len(artifact["cluster_order"])) + "\n"
            )
            with self.assertRaisesRegex(ValidationError, "invalid part"):
                parse_tritonpart_solution(solution, artifact)

    def test_min_used_repair_moves_one_small_atomic_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = export_tritonpart_inputs(
                self.ir,
                self.platform,
                self.clusters,
                self.constraints,
                Path(temporary_directory),
            )
            raw = {
                cluster["id"]: "fpga0"
                for cluster in self.clusters["clusters"]
            }
            repaired, moves = _repair_min_used_fpgas(
                raw,
                self.clusters,
                self.platform,
                self.constraints,
                artifact["hyperedges"],
            )
            self.assertEqual(set(repaired.values()), {"fpga0", "fpga1"})
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0]["source"], "fpga0")
            self.assertEqual(moves[0]["target"], "fpga1")
            self.assertEqual(moves[0]["instances"], 2)

    def test_phase3_executes_provider_and_independently_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            ir_path = output / "design.emuir.json"
            ir_path.write_text(
                json.dumps(self.ir.to_dict()), encoding="utf-8"
            )
            attempted_seeds = []

            def fake_openroad(command, **kwargs):
                self.assertTrue(Path(command[-1]).is_absolute())
                run_directory = Path(kwargs["cwd"])
                tcl = Path(command[-1]).read_text(encoding="utf-8")
                seed_line = next(
                    line for line in tcl.splitlines() if "-seed" in line
                )
                attempted_seeds.append(int(seed_line.split()[1]))
                tritonpart_input = json.loads(
                    (run_directory / "tritonpart_input.json").read_text()
                )
                solution = run_directory / tritonpart_input["files"]["solution"]
                solution.write_text(
                    "\n".join(
                        (
                            "0"
                            if len(attempted_seeds) == 1
                            else str(index % 2)
                        )
                        for index in range(
                            len(tritonpart_input["cluster_order"])
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout="TritonPart test provider\n"
                )

            with mock.patch(
                "emuflow.tritonpart.subprocess.run",
                side_effect=fake_openroad,
            ):
                report = run_phase3(
                    ir_path=ir_path,
                    platform_path=PLATFORM_PATH,
                    output_dir=output / "phase3",
                    seed=19,
                    provider="tritonpart",
                    openroad="/fake/openroad",
                    tritonpart_seed_attempts=2,
                )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["seed"], 20)
            self.assertEqual(attempted_seeds, [19, 20])
            self.assertEqual(
                report["provider"],
                "tritonpart-openroad-hypergraph-v1",
            )
            self.assertEqual(report["validation"]["used_fpgas"], 2)
            self.assertEqual(report["validation"]["illegal_cuts"], 0)
            assignment = json.loads(
                (output / "phase3" / "assignment.json").read_text()
            )
            self.assertEqual(
                assignment["provider_metadata"]["mode"], "execute"
            )
            self.assertEqual(
                assignment["provider_metadata"]["seed_attempts"],
                [
                    {
                        "seed": 19,
                        "raw_used_fpgas": 1,
                        "used_fpgas": 1,
                        "repair_moves": [],
                        "log": "openroad-tritonpart.seed-19.log",
                    },
                    {
                        "seed": 20,
                        "raw_used_fpgas": 2,
                        "used_fpgas": 2,
                        "repair_moves": [],
                        "log": "openroad-tritonpart.seed-20.log",
                    },
                ],
            )
            self.assertEqual(
                assignment["provider_metadata"]["min_used_fpgas_repair"],
                {"enabled": False, "moves": []},
            )
            self.assertTrue(
                (
                    output
                    / "phase3"
                    / "tritonpart"
                    / "openroad-tritonpart.seed-20.log"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
