import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.experiment_dag import plan_experiment, run_experiment_node
from emuflow.experiment_store import (
    apply_experiment_gc,
    create_experiment_evidence_bundle,
    inventory_experiment_store,
    plan_experiment_gc,
    plan_legacy_run_migration,
    validate_experiment_evidence_bundle,
)
from emuflow.io import write_json


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _closure(label: str) -> dict:
    files = [{"path": f"{label}.py", "bytes": 1, "sha256": _digest(label)}]
    identity = {
        "schema": "emuflow.experiment-implementation-identity/v1",
        "files": files,
    }
    return {
        "schema": "emuflow.experiment-implementation-closure/v1",
        "status": "pass",
        "components": [f"{label}.py"],
        "files": files,
        "implementation_sha256": hashlib.sha256(
            json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
    }


def _spec() -> dict:
    nodes = []
    for index in range(1, 4):
        node_id = f"phase{index}"
        artifact = f"{node_id}.json"
        dependencies = [] if index == 1 else [f"phase{index - 1}"]
        dependency_tokens = [f"{{dependency:{item}}}" for item in dependencies]
        nodes.append(
            {
                "id": node_id,
                "stage": node_id,
                "dependencies": dependencies,
                "inputs": {"source": _digest("source")} if index == 1 else {},
                "configuration": {},
                "implementation": _closure(f"impl{index}"),
                "command": [
                    sys.executable,
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).mkdir(parents=True,exist_ok=True); pathlib.Path(sys.argv[1],sys.argv[2]).write_text('ok')",
                    "{output_dir}",
                    artifact,
                    *dependency_tokens,
                ],
                "validator_implementation": _closure(f"validator{index}"),
                "validator": [
                    sys.executable,
                    "-c",
                    "import pathlib,sys; raise SystemExit(0 if pathlib.Path(sys.argv[1],sys.argv[2]).is_file() else 1)",
                    "{artifact_root}",
                    artifact,
                    *dependency_tokens,
                ],
                "environment": {},
                "storage_estimate": {"peak_bytes": 1024, "retained_bytes": 128},
                "artifacts": [{"path": artifact, "role": "consumer-checkpoint"}],
            }
        )
    return {
        "schema": "emuflow.experiment-dag-spec/v2",
        "experiment_id": "store-test",
        "source_commit": "1" * 40,
        "nodes": nodes,
    }


class ExperimentStoreTest(unittest.TestCase):
    def test_inventory_and_self_contained_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            spec_path = root / "spec.json"
            write_json(spec_path, _spec())
            for node_id in ("phase1", "phase2", "phase3"):
                plan_path = root / f"{node_id}.plan.json"
                plan_experiment(spec_path, cache, plan_path)
                run_experiment_node(plan_path, node_id, root / f"attempt-{node_id}")
            inventory = inventory_experiment_store(cache)
            self.assertEqual(inventory["counts"], {"valid": 3, "invalid": 0})
            plan_path = root / "final.plan.json"
            plan_experiment(spec_path, cache, plan_path)
            evidence_root = root / "evidence"
            evidence = create_experiment_evidence_bundle(
                plan_path, ["phase3"], evidence_root
            )
            self.assertEqual(evidence["nodes"], 3)
            self.assertEqual(evidence["retained_artifacts"], 3)
            # The bundle remains valid after removing the cache, proving that it
            # is not merely a path-based reference to checkpoint objects.
            for path in cache.rglob("*"):
                if path.is_file():
                    path.chmod(0o644)
                elif path.is_dir():
                    path.chmod(0o755)
            import shutil

            shutil.rmtree(cache)
            self.assertEqual(
                validate_experiment_evidence_bundle(evidence_root)["status"],
                "pass",
            )
            artifact = next(evidence_root.glob("checkpoints/*/phase1.json"))
            artifact.chmod(0o644)
            artifact.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "seal is broken"):
                validate_experiment_evidence_bundle(evidence_root)

    def test_gc_requires_exact_approved_plan_and_preserves_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            spec_path = root / "spec.json"
            write_json(spec_path, _spec())
            plan_path = root / "plan.json"
            plan = plan_experiment(spec_path, cache, plan_path)
            run_experiment_node(plan_path, "phase1", root / "attempt")
            protected = cache / "objects" / plan["nodes"][0]["execution_key"]
            failure = cache / "failures/old-attempt"
            failure.mkdir(parents=True)
            (failure / "stderr.log").write_text("failed", encoding="utf-8")
            final_plan_path = root / "final-plan.json"
            plan_experiment(spec_path, cache, final_plan_path)
            gc_path = root / "gc.json"
            gc = plan_experiment_gc(
                cache, [final_plan_path], gc_path, minimum_age_seconds=0
            )
            self.assertEqual(
                [item["path"] for item in gc["candidates"]],
                ["failures/old-attempt"],
            )
            with self.assertRaisesRegex(ValidationError, "approval seal"):
                apply_experiment_gc(gc_path, "0" * 64)
            approved = hashlib.sha256(gc_path.read_bytes()).hexdigest()
            receipt = apply_experiment_gc(gc_path, approved)
            self.assertEqual(receipt["removed_bytes"], 6)
            self.assertFalse(failure.exists())
            self.assertTrue(protected.exists())

    def test_legacy_migration_inventory_is_read_only_and_counts_hardlinks_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = root / "runs"
            first = runs / "full-a"
            second = runs / "full-b"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            marker = first / "multi-fpga-flow-report.json"
            marker.write_text("{}", encoding="utf-8")
            os.link(marker, second / "multi-fpga-flow-report.json")
            output = root / "migration.json"
            report = plan_legacy_run_migration(runs, output)
            self.assertEqual(
                [item["classification"] for item in report["entries"]],
                ["full-flow-candidate", "full-flow-candidate"],
            )
            self.assertFalse(report["safety"]["mutated"])
            self.assertLess(
                report["totals"]["unique_allocated_bytes"],
                report["totals"]["allocated_bytes_before_hardlink_dedup"],
            )
            self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
