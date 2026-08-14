import json
import sys
import tempfile
import unittest
from pathlib import Path

from emuflow.canonical_experiment import (
    CANONICAL_EXPERIMENT_CONFIG_SCHEMA,
    compile_canonical_experiment_spec,
)
from emuflow.experiment_dag import validate_experiment_spec
from emuflow.errors import ValidationError
from emuflow.experiment_identity import build_implementation_closure
from emuflow.io import write_json


REPOSITORY = Path(__file__).resolve().parents[1]


class CanonicalExperimentTest(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        openparf_install = root / "openparf-install"
        (openparf_install / "openparf").mkdir(parents=True, exist_ok=True)
        (openparf_install / "openparf.py").write_text(
            "# fixture loader\n", encoding="utf-8"
        )
        (openparf_install / "openparf/__init__.py").write_text(
            "# fixture package\n", encoding="utf-8"
        )
        manifest = root / "openparf-manifest.json"
        write_json(
            manifest,
            build_implementation_closure(
                openparf_install, ["openparf.py", "openparf"]
            ),
        )
        tool_names = (
            "emuflow",
            "yosys",
            "opensta",
            "openroad",
            "hop_refiner",
            "router",
            "ratio_optimizer",
            "timing_dag_optimizer",
            "slot_optimizer",
            "pin_planner",
            "chimew_grouper",
            "chimew_refiner",
            "chimew_rudy",
            "chimew_assigner",
            "vpr",
            "architecture_importer",
            "packed_importer",
            "route_checker",
            "openparf_python",
        )
        rtl = root / "dla_like.medium.v"
        rtl.write_text("module DLA(input clk); endmodule\n", encoding="utf-8")
        platform = root / "boarddb.json"
        platform_value = json.loads(
            (REPOSITORY / "platforms/virtual/xcvu3p_2fpga_p2p.json").read_text()
        )
        platform_value["platform"]["name"] = "eda2023-case6-rtl"
        platform.write_text(json.dumps(platform_value), encoding="utf-8")
        route_constraints = root / "route_constraints.json"
        route_constraints.write_text(
            json.dumps(
                {
                    "schema": "emuflow.system-route-constraints/v1",
                    "frame_slots": 32,
                    "max_route_hops": 2,
                    "tdm_ratio_quantum": 4,
                    "tdm_min_ratio": 4,
                }
            ),
            encoding="utf-8",
        )
        from emuflow.contest_validation_matrix import load_contest_validation_matrix
        import hashlib

        _, contest_validation = load_contest_validation_matrix(
            REPOSITORY / "benchmarks/contest_validation_matrix.json"
        )
        boarddb_report = root / "boarddb_report.json"
        boarddb_report.write_text(
            json.dumps(
                {
                    "schema": "emuflow.public-contest-boarddb-report/v1",
                    "status": "pass",
                    "case_id": "eda2023.case6",
                    "suite": "eda2023",
                    "gate": "materialize-boarddb",
                    "matrix_sha256": contest_validation["matrix_sha256"],
                    "qualification": "academic-architecture-projection",
                    "projection": {},
                    "adapter": {},
                    "artifacts": [
                        {
                            "path": "boarddb.json",
                            "bytes": platform.stat().st_size,
                            "sha256": hashlib.sha256(platform.read_bytes()).hexdigest(),
                        },
                        {
                            "path": "route_constraints.json",
                            "bytes": route_constraints.stat().st_size,
                            "sha256": hashlib.sha256(
                                route_constraints.read_bytes()
                            ).hexdigest(),
                        },
                    ],
                    "phase3_status": "not-run",
                }
            ),
            encoding="utf-8",
        )
        value = {
            "schema": CANONICAL_EXPERIMENT_CONFIG_SCHEMA,
            "case_id": "koios-dla-medium-l5__eda2023-case6",
            "source_commit": "a" * 40,
            "rtl_source": str(rtl),
            "platform": str(platform),
            "boarddb_report": str(boarddb_report),
            "route_constraints": str(route_constraints),
            "timing_model": str(
                REPOSITORY / "resources/timing/ultrascaleplus-softlogic-v1.json"
            ),
            "architecture_timing_db": str(
                REPOSITORY
                / "resources/architectures/vtr/flagship-k6-n10-40nm.json"
            ),
            "physical_architecture": str(
                REPOSITORY / "examples/architecture/vtr_k6_heterogeneous_fixture.xml"
            ),
            "tools": {name: sys.executable for name in tool_names},
            "openparf_install": str(openparf_install),
            "openparf_manifest": str(manifest),
            "top": "DLA",
            "clocks": ["clk"],
            "clock_periods": {"clk": 10.0},
            "physical_workers": 8,
        }
        path = root / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_compiler_emits_fine_grained_shared_dag_and_nine_terminals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "spec.json"
            report = compile_canonical_experiment_spec(
                self._config(root), REPOSITORY, output
            )
            self.assertEqual(report["nodes"], 20)
            self.assertEqual(report["terminal_nodes"], 9)
            spec = validate_experiment_spec(json.loads(output.read_text()))
            nodes = {item["id"]: item for item in spec["nodes"]}
            self.assertEqual(
                [
                    item["id"]
                    for item in spec["nodes"][:7]
                ],
                [
                    "frontend",
                    "timing",
                    "partition",
                    "cut-timing",
                    "route",
                    "tdm",
                    "shared-phase1-5",
                ],
            )
            self.assertEqual(nodes["route"]["dependencies"], ["partition", "cut-timing"])
            self.assertEqual(nodes["tdm"]["dependencies"], ["route"])
            self.assertIn("--route-constraints", nodes["partition"]["command"])
            self.assertIn("--hop-refiner", nodes["partition"]["command"])
            self.assertIn("--constraints", nodes["route"]["command"])
            self.assertEqual(nodes["tdm"]["configuration"]["ratio_quantum"], 4)
            self.assertEqual(nodes["tdm"]["configuration"]["max_ratio"], 32)
            self.assertIn(
                "--pin-planner", nodes["phase6-placement-aware"]["command"]
            )
            for argument in (
                "--chimew-grouper",
                "--chimew-refiner",
                "--chimew-rudy",
                "--chimew-assigner",
            ):
                self.assertIn(argument, nodes["phase6-chimew"]["command"])
            terminals = [item for item in spec["nodes"] if item["stage"] == "phase7"]
            self.assertEqual(
                {(item["provider"], item["physical_seed"]) for item in terminals},
                {(provider, seed) for provider in ("baseline", "placement-aware", "chimew") for seed in (1, 2, 3)},
            )
            self.assertTrue(all(item["configuration"]["physical_workers"] == 8 for item in terminals))
            for terminal in terminals:
                roles = {
                    item["path"]: item["role"] for item in terminal["artifacts"]
                }
                self.assertEqual(
                    roles["physical/physical-summary.json"],
                    "evidence-critical",
                )
                self.assertEqual(
                    roles["physical/multi-fpga-physical-flow-report.json"],
                    "evidence-critical",
                )
                self.assertEqual(roles["physical"], "diagnostic")
            self.assertTrue(
                all(
                    item["validator"][-6:]
                    == [
                        "--seed",
                        str(item["physical_seed"]),
                        "--workers",
                        "8",
                        "--route-channel-width",
                        "300",
                    ]
                    for item in terminals
                )
            )

    def test_external_tool_bytes_are_part_of_execution_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = self._config(root)
            first = root / "first.json"
            compile_canonical_experiment_spec(config_path, REPOSITORY, first)
            config = json.loads(config_path.read_text())
            replacement = root / "replacement-tool"
            replacement.write_text("different tool bytes\n", encoding="utf-8")
            replacement.chmod(0o755)
            config["tools"]["router"] = str(replacement)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            second = root / "second.json"
            compile_canonical_experiment_spec(config_path, REPOSITORY, second)
            first_nodes = {item["id"]: item for item in json.loads(first.read_text())["nodes"]}
            second_nodes = {item["id"]: item for item in json.loads(second.read_text())["nodes"]}
            self.assertNotEqual(
                first_nodes["route"]["inputs"]["tool.router"],
                second_nodes["route"]["inputs"]["tool.router"],
            )
            self.assertEqual(
                first_nodes["frontend"]["inputs"], second_nodes["frontend"]["inputs"]
            )

    def test_matrix_and_boarddb_materialization_contract_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = self._config(root)
            config = json.loads(config_path.read_text())
            config["top"] = "renamed_DLA"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "top/clocks"):
                compile_canonical_experiment_spec(
                    config_path, REPOSITORY, root / "wrong-top.json"
                )

            config_path = self._config(root)
            config = json.loads(config_path.read_text())
            Path(config["platform"]).write_text(
                Path(config["platform"]).read_text() + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValidationError, "platform bytes"):
                compile_canonical_experiment_spec(
                    config_path, REPOSITORY, root / "wrong-platform.json"
                )

            config_path = self._config(root)
            config = json.loads(config_path.read_text())
            Path(config["route_constraints"]).write_text(
                Path(config["route_constraints"]).read_text() + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "route constraints bytes"):
                compile_canonical_experiment_spec(
                    config_path, REPOSITORY, root / "wrong-route-constraints.json"
                )


if __name__ == "__main__":
    unittest.main()
