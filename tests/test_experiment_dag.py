import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.experiment_dag import (
    EXPERIMENT_PLAN_SCHEMA,
    EXPERIMENT_SPEC_SCHEMA,
    build_experiment_farm_spec,
    import_experiment_checkpoint,
    plan_experiment,
    run_experiment_node,
    validate_experiment_checkpoint,
    validate_experiment_spec,
)
from emuflow.io import read_json, write_json


COMMIT = "1" * 40


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _writer(payload: str, artifact: str, *dependencies: str) -> list[str]:
    script = (
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "p.mkdir(parents=True,exist_ok=True); "
        f"p.joinpath('{artifact}').write_text('{payload}')"
    )
    return [sys.executable, "-c", script, "{output_dir}", *dependencies]


def _validator(artifact: str, *dependencies: str) -> list[str]:
    script = (
        "import pathlib,sys; "
        f"raise SystemExit(0 if pathlib.Path(sys.argv[1]).joinpath('{artifact}').is_file() else 3)"
    )
    return [sys.executable, "-c", script, "{artifact_root}", *dependencies]


class ExperimentDagTest(unittest.TestCase):
    def _spec(self) -> dict:
        return {
            "schema": EXPERIMENT_SPEC_SCHEMA,
            "experiment_id": "koios-case6-phase6-ab",
            "source_commit": COMMIT,
            "nodes": [
                {
                    "id": "shared-phase1-5",
                    "stage": "shared-phase1-5",
                    "dependencies": [],
                    "inputs": {
                        "rtl": _digest("rtl"),
                        "boarddb": _digest("boarddb"),
                    },
                    "configuration": {"partition_seed": 0},
                    "command": _writer("shared", "phase5.json"),
                    "validator": _validator("phase5.json"),
                    "artifacts": ["phase5.json"],
                },
                {
                    "id": "phase6-baseline",
                    "stage": "phase6",
                    "provider": "baseline",
                    "dependencies": ["shared-phase1-5"],
                    "inputs": {},
                    "configuration": {"equivalence_seed": 7},
                    "command": _writer(
                        "baseline", "phase6.json", "{dependency:shared-phase1-5}"
                    ),
                    "validator": _validator(
                        "phase6.json", "{dependency:shared-phase1-5}"
                    ),
                    "artifacts": ["phase6.json"],
                },
                {
                    "id": "phase6-chimew",
                    "stage": "phase6",
                    "provider": "chimew",
                    "dependencies": ["shared-phase1-5"],
                    "inputs": {},
                    "configuration": {"regions": 4},
                    "command": _writer(
                        "chimew", "phase6.json", "{dependency:shared-phase1-5}"
                    ),
                    "validator": _validator(
                        "phase6.json", "{dependency:shared-phase1-5}"
                    ),
                    "artifacts": ["phase6.json"],
                },
                {
                    "id": "shared-lookahead",
                    "stage": "physical-lookahead",
                    "dependencies": ["shared-phase1-5", "phase6-baseline"],
                    "inputs": {},
                    "configuration": {"lookahead_seed": 11},
                    "command": _writer(
                        "lookahead",
                        "lookahead.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-baseline}",
                    ),
                    "validator": _validator(
                        "lookahead.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-baseline}",
                    ),
                    "artifacts": ["lookahead.json"],
                },
                {
                    "id": "phase7-baseline-seed1",
                    "stage": "phase7",
                    "provider": "baseline",
                    "physical_seed": 1,
                    "dependencies": ["shared-phase1-5", "phase6-baseline"],
                    "inputs": {},
                    "configuration": {
                        "physical_backend": "open",
                        "physical_workers": 8,
                    },
                    "command": _writer(
                        "baseline-seed1",
                        "physical-summary.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-baseline}",
                    ),
                    "validator": _validator(
                        "physical-summary.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-baseline}",
                    ),
                    "artifacts": ["physical-summary.json"],
                },
                {
                    "id": "phase7-chimew-seed1",
                    "stage": "phase7",
                    "provider": "chimew",
                    "physical_seed": 1,
                    "dependencies": ["shared-phase1-5", "phase6-chimew"],
                    "inputs": {},
                    "configuration": {
                        "physical_backend": "open",
                        "physical_workers": 8,
                    },
                    "command": _writer(
                        "chimew-seed1",
                        "physical-summary.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-chimew}",
                    ),
                    "validator": _validator(
                        "physical-summary.json",
                        "{dependency:shared-phase1-5}",
                        "{dependency:phase6-chimew}",
                    ),
                    "artifacts": ["physical-summary.json"],
                },
            ],
        }

    def _write_spec(self, root: Path, value: Optional[dict] = None) -> Path:
        path = root / "spec.json"
        write_json(path, value or self._spec())
        return path

    def test_frontiers_reuse_shared_and_provider_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            spec = self._write_spec(root)
            plan1_path = root / "plan1.json"
            plan1 = plan_experiment(spec, cache, plan1_path)
            self.assertEqual(
                plan1["counts"], {"reuse": 0, "ready": 1, "waiting": 5}
            )
            self.assertEqual(plan1["nodes"][0]["state"], "ready")

            shared_run = root / "run-shared"
            shared = run_experiment_node(
                plan1_path, "shared-phase1-5", shared_run
            )
            self.assertEqual(shared["status"], "pass")
            plan2_path = root / "plan2.json"
            plan2 = plan_experiment(spec, cache, plan2_path)
            self.assertEqual(
                plan2["counts"], {"reuse": 1, "ready": 2, "waiting": 3}
            )

            baseline6 = run_experiment_node(
                plan2_path, "phase6-baseline", root / "run-baseline6"
            )
            self.assertEqual(baseline6["status"], "pass")
            plan3_path = root / "plan3.json"
            plan3 = plan_experiment(spec, cache, plan3_path)
            by_id = {item["id"]: item["state"] for item in plan3["nodes"]}
            self.assertEqual(by_id["shared-phase1-5"], "reuse")
            self.assertEqual(by_id["phase6-baseline"], "reuse")
            self.assertEqual(by_id["phase6-chimew"], "ready")
            self.assertEqual(by_id["shared-lookahead"], "ready")
            self.assertEqual(by_id["phase7-baseline-seed1"], "ready")
            self.assertEqual(by_id["phase7-chimew-seed1"], "waiting")

            baseline7 = run_experiment_node(
                plan3_path,
                "phase7-baseline-seed1",
                root / "run-baseline7",
            )
            self.assertEqual(baseline7["status"], "pass")
            repeated = run_experiment_node(
                plan3_path,
                "phase7-baseline-seed1",
                root / "run-baseline7-repeat",
            )
            self.assertEqual(repeated["status"], "reused")

    def test_changes_invalidate_only_the_affected_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            spec = self._write_spec(root)
            plan1_path = root / "plan1.json"
            plan_experiment(spec, cache, plan1_path)
            run_experiment_node(plan1_path, "shared-phase1-5", root / "shared")
            plan2_path = root / "plan2.json"
            plan_experiment(spec, cache, plan2_path)
            run_experiment_node(plan2_path, "phase6-baseline", root / "baseline")

            changed = self._spec()
            changed["nodes"][1]["configuration"]["equivalence_seed"] = 8
            changed_path = self._write_spec(root, changed)
            changed_plan = plan_experiment(changed_path, cache, root / "changed.json")
            states = {item["id"]: item["state"] for item in changed_plan["nodes"]}
            self.assertEqual(states["shared-phase1-5"], "reuse")
            self.assertEqual(states["phase6-baseline"], "ready")
            self.assertEqual(states["phase7-baseline-seed1"], "waiting")
            self.assertEqual(states["phase6-chimew"], "ready")

            changed_source = self._spec()
            changed_source["nodes"][0]["inputs"]["rtl"] = _digest("new-rtl")
            source_path = self._write_spec(root, changed_source)
            source_plan = plan_experiment(source_path, cache, root / "source.json")
            self.assertEqual(
                source_plan["counts"], {"reuse": 0, "ready": 1, "waiting": 5}
            )

    def test_existing_external_result_can_be_imported_without_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = self._write_spec(root)
            plan_path = root / "plan.json"
            plan = plan_experiment(spec, root / "cache", plan_path)
            old = root / "old-phase1-5"
            old.mkdir()
            (old / "phase5.json").write_text("shared", encoding="utf-8")
            imported = import_experiment_checkpoint(
                plan_path, "shared-phase1-5", old
            )
            self.assertEqual(imported["status"], "imported")
            replanned = plan_experiment(spec, root / "cache", root / "next.json")
            self.assertEqual(replanned["nodes"][0]["state"], "reuse")
            (old / "phase5.json").write_text("tampered", encoding="utf-8")
            manifest = (
                root
                / "cache"
                / "objects"
                / plan["nodes"][0]["key"]
                / "checkpoint.json"
            )
            with self.assertRaisesRegex(ValidationError, "seal is broken"):
                validate_experiment_checkpoint(manifest)

    def test_import_and_new_runs_require_independent_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = self._spec()
            invalid["nodes"][0]["validator"] = [
                sys.executable,
                "-c",
                "raise SystemExit(4)",
                "{artifact_root}",
            ]
            spec = self._write_spec(root, invalid)
            plan_path = root / "plan.json"
            plan_experiment(spec, root / "cache", plan_path)
            old = root / "old"
            old.mkdir()
            (old / "phase5.json").write_text("shared", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "validator failed"):
                import_experiment_checkpoint(plan_path, "shared-phase1-5", old)
            report = run_experiment_node(
                plan_path, "shared-phase1-5", root / "run"
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["failure_stage"], "independent-validator")

    def test_farm_spec_contains_only_ready_cache_misses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install" / COMMIT
            install.mkdir(parents=True)
            spec = self._write_spec(root)
            plan_path = root / "plan.json"
            plan_experiment(spec, root / "cache", plan_path)
            farm_path = root / "farm.json"
            report = build_experiment_farm_spec(
                plan_path, install, ["hpc1", "hpc2"], "case6-frontier", farm_path
            )
            self.assertEqual(report["ready_tasks"], 1)
            farm = read_json(farm_path)
            self.assertEqual([task["id"] for task in farm["tasks"]], ["shared-phase1-5"])
            self.assertIn("--expected-plan-sha256", farm["tasks"][0]["command"])

    def test_invalid_dependencies_provider_seed_and_placeholders_are_rejected(self) -> None:
        invalid = self._spec()
        invalid["nodes"][4]["provider"] = "chimew"
        with self.assertRaisesRegex(ValidationError, "matching Phase 6 provider"):
            validate_experiment_spec(invalid)
        invalid = self._spec()
        invalid["nodes"][1]["command"].append("{dependency:phase6-chimew}")
        with self.assertRaisesRegex(ValidationError, "undeclared dependency"):
            validate_experiment_spec(invalid)
        invalid = self._spec()
        invalid["nodes"][4]["physical_seed"] = -1
        with self.assertRaisesRegex(ValidationError, "physical seed"):
            validate_experiment_spec(invalid)
        invalid = self._spec()
        del invalid["nodes"][4]["configuration"]["physical_workers"]
        with self.assertRaisesRegex(ValidationError, "physical_workers"):
            validate_experiment_spec(invalid)
        invalid = self._spec()
        invalid["nodes"][0]["validator"] = ["check-without-artifact-root"]
        with self.assertRaisesRegex(ValidationError, "artifact_root"):
            validate_experiment_spec(invalid)


if __name__ == "__main__":
    unittest.main()
