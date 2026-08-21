import hashlib
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.io import read_json, write_json
from emuflow.validation_farm import (
    FARM_RETIREMENT_MARKER,
    FARM_RETIREMENT_MARKER_SCHEMA,
    FARM_SPEC_SCHEMA,
    launch_validation_farm,
    prepare_validation_farm,
    reconcile_validation_farm,
    run_validation_farm_task,
    validation_farm_status,
    validate_validation_farm,
)


COMMIT = "1" * 40


class ValidationFarmTest(unittest.TestCase):
    def setUp(self) -> None:
        # Unit tests exercise farm state transitions, not the live account
        # quota.  Validation hosts intentionally enforce the /research quota,
        # so isolate ordinary fixtures from ambient quota exhaustion.  The
        # explicit storage-shortage test below installs its own blocking
        # result and therefore still covers the production gate.
        self._storage_preflight = mock.patch(
            "emuflow.validation_farm.preflight_experiment_storage",
            return_value={
                "status": "pass",
                "estimated_peak_bytes": 0,
                "reserve_bytes": 0,
                "required_available_bytes": 0,
                "root": "/research/d4/gds/ziyiwang21",
                "filesystem_free_bytes": 1 << 40,
                "quota_available_bytes": 1 << 40,
                "available_bytes": 1 << 40,
                "quota_error": None,
            },
        )
        self._storage_preflight.start()

    def tearDown(self) -> None:
        self._storage_preflight.stop()

    def _fixture(self, root: Path, task_count: int = 3) -> tuple[Path, Path]:
        install = root / "install" / COMMIT
        (install / "bin").mkdir(parents=True)
        emuflow = install / "bin" / "emuflow"
        emuflow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        emuflow.chmod(0o755)
        spec = root / "spec.json"
        tasks = []
        for index in range(task_count):
            tasks.append(
                {
                    "id": f"task-{index}",
                    "estimated_peak_bytes": 1024,
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "import json,pathlib,sys; "
                            "pathlib.Path(sys.argv[1]).joinpath('done')"
                            ".write_text(json.dumps({'status': 'ok'}))"
                        ),
                        "{run_dir}",
                    ],
                    "environment": {"PINNED_INSTALL": "{install}"},
                }
            )
        write_json(
            spec,
            {
                "schema": FARM_SPEC_SCHEMA,
                "farm_id": "parallel-regression",
                "source_commit": COMMIT,
                "install_dir": str(install),
                "nodes": ["node-a", "node-b"],
                "slots_per_node": 2,
                "storage_reserve_bytes": 0,
                "tasks": tasks,
            },
        )
        return spec, install

    def test_prepare_pins_version_and_balances_isolated_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, install = self._fixture(root)
            farm = root / "farm"
            report = prepare_validation_farm(spec, farm)
            self.assertEqual(report["status"], "valid")
            self.assertEqual(report["tasks"], 3)
            manifest = read_json(farm / "farm-manifest.json")
            self.assertEqual(
                [task["node"] for task in manifest["tasks"]],
                ["node-a", "node-b", "node-a"],
            )
            self.assertEqual(manifest["install_dir"], str(install.resolve()))
            run_dirs = {task["run_dir"] for task in manifest["tasks"]}
            self.assertEqual(len(run_dirs), 3)
            for record in manifest["tasks"]:
                task = read_json(Path(record["task"]))
                self.assertIn(task["run_dir"], task["command"])
                self.assertEqual(
                    task["environment"]["PINNED_INSTALL"], str(install.resolve())
                )
            self.assertEqual(validate_validation_farm(farm)["status"], "valid")

    def test_prepare_rejects_mutable_install_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, install = self._fixture(root, task_count=1)
            alias = root / "install" / "current"
            alias.symlink_to(install.name)
            value = read_json(spec)
            value["install_dir"] = str(alias)
            write_json(spec, value)
            with self.assertRaisesRegex(ValidationError, "not a symlink"):
                prepare_validation_farm(spec, root / "farm")

    def test_prepare_reserves_output_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            with self.assertRaisesRegex(EmuFlowError, "already exists"):
                prepare_validation_farm(spec, farm)

    def test_prepare_content_seals_explicit_known_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            known_hosts = root / "known_hosts"
            known_hosts.write_text("hpc1 ssh-ed25519 test-key\n", encoding="utf-8")
            farm = root / "farm"
            prepare_validation_farm(
                spec, farm, ssh_known_hosts_file=known_hosts.resolve()
            )
            manifest = read_json(farm / "farm-manifest.json")
            binding = manifest["ssh"]["known_hosts"]
            self.assertEqual(binding["path"], str(known_hosts.resolve()))
            self.assertIn(
                f"UserKnownHostsFile={known_hosts.resolve()}",
                manifest["ssh"]["arguments"],
            )
            self.assertIn(
                "StrictHostKeyChecking=yes", manifest["ssh"]["arguments"]
            )
            self.assertIn("UpdateHostKeys=no", manifest["ssh"]["arguments"])
            known_hosts.write_text("hpc1 ssh-ed25519 replaced\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "seal is broken"):
                validate_validation_farm(farm)

    def test_prepare_rejects_relative_known_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            with self.assertRaisesRegex(ValidationError, "must be absolute"):
                prepare_validation_farm(
                    spec,
                    root / "farm",
                    ssh_known_hosts_file=Path("known_hosts"),
                )

    def test_prepare_content_seals_outer_worker_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, install = self._fixture(root, task_count=1)
            launcher = root / "container-launcher"
            launcher.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
            launcher.chmod(0o755)
            value = read_json(spec)
            value["worker_argv"] = [
                str(launcher.resolve()),
                "{install}/bin/emuflow",
            ]
            value["worker_launcher"] = {
                "path": str(launcher.resolve()),
                "sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
            }
            write_json(spec, value)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            manifest = read_json(farm / "farm-manifest.json")
            self.assertEqual(
                manifest["worker_launcher"], value["worker_launcher"]
            )
            self.assertEqual(
                manifest["worker_argv"],
                [
                    str(launcher.resolve()),
                    str(install.resolve() / "bin" / "emuflow"),
                ],
            )
            launcher.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "seal is broken"):
                validate_validation_farm(farm)

    def test_validation_rejects_tampered_task_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            task_path = farm / "tasks" / "task-0" / "task.json"
            task = read_json(task_path)
            task["command"].append("unexpected")
            write_json(task_path, task)
            with self.assertRaisesRegex(ValidationError, "seal is broken"):
                validate_validation_farm(farm)

    def test_worker_records_version_node_slot_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, install = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            task_path = farm / "tasks" / "task-0" / "task.json"
            with mock.patch.object(
                socket, "gethostname", return_value="node-a.example"
            ):
                result = run_validation_farm_task(task_path)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["slot"], 0)
            self.assertEqual(result["source_commit"], COMMIT)
            self.assertEqual(result["install_dir"], str(install.resolve()))
            self.assertEqual(
                read_json(
                    farm
                    / "runs"
                    / "task-0"
                    / "attempts"
                    / "attempt-0001"
                    / "done"
                )["status"],
                "ok",
            )
            status = validation_farm_status(farm)
            self.assertTrue(status["complete"])
            self.assertEqual(status["counts"], {"pass": 1})

    def test_launch_uses_argv_ssh_and_skips_duplicate_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, install = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"status":"detached"}\n', stderr=""
            )
            with mock.patch(
                "emuflow.validation_farm.subprocess.run", return_value=completed
            ) as runner:
                result = launch_validation_farm(farm)
            self.assertEqual(result["status"], "pass")
            argv = runner.call_args.args[0]
            self.assertEqual(argv[:4], ["ssh", "-o", "BatchMode=yes", "node-a"])
            self.assertIn(str(install / "bin" / "emuflow"), argv[-1])
            self.assertTrue(argv[-1].startswith("nohup setsid "))
            self.assertIn("validation-farm worker", argv[-1])
            self.assertNotIn("--detach", argv[-1])
            self.assertIn("worker-bootstrap.log", argv[-1])
            self.assertIn("</dev/null", argv[-1])
            with mock.patch(
                "emuflow.validation_farm.subprocess.run", return_value=completed
            ) as second_runner:
                second = launch_validation_farm(farm)
            self.assertEqual(second["submitted"], 0)
            self.assertEqual(second["skipped"], 1)
            second_runner.assert_not_called()

    def test_retirement_marker_blocks_validation_and_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            write_json(
                farm / FARM_RETIREMENT_MARKER,
                {
                    "schema": FARM_RETIREMENT_MARKER_SCHEMA,
                    "status": "retirement-pending",
                },
            )
            with self.assertRaisesRegex(ValidationError, "pending retirement"):
                validate_validation_farm(farm)
            with mock.patch("emuflow.validation_farm.subprocess.run") as runner:
                with self.assertRaisesRegex(ValidationError, "pending retirement"):
                    launch_validation_farm(farm)
            runner.assert_not_called()

    def test_launch_rechecks_retirement_after_acquiring_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            from emuflow import validation_farm as farm_module

            original = farm_module._refuse_retiring_farm
            calls = 0

            def inject_marker(candidate: Path) -> None:
                nonlocal calls
                calls += 1
                original(candidate)
                if calls == 1:
                    write_json(
                        candidate / FARM_RETIREMENT_MARKER,
                        {
                            "schema": FARM_RETIREMENT_MARKER_SCHEMA,
                            "status": "retirement-pending",
                        },
                    )

            with mock.patch.object(
                farm_module, "_refuse_retiring_farm", side_effect=inject_marker
            ), mock.patch("emuflow.validation_farm.subprocess.run") as runner:
                with self.assertRaisesRegex(ValidationError, "pending retirement"):
                    launch_validation_farm(farm)
            self.assertEqual(calls, 2)
            runner.assert_not_called()

    def test_submit_failure_can_be_retried_without_duplicate_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            failed = subprocess.CompletedProcess(
                args=[], returncode=255, stdout="", stderr="unreachable"
            )
            passed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"status":"detached"}\n', stderr=""
            )
            with mock.patch(
                "emuflow.validation_farm.subprocess.run",
                side_effect=[failed, passed],
            ):
                first = launch_validation_farm(farm)
                second = launch_validation_farm(farm)
            self.assertEqual(first["submit_failed"], 1)
            self.assertEqual(second["submitted"], 1)
            self.assertEqual(second["submit_failed"], 0)

    def test_storage_shortage_blocks_submission_without_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            blocked = {
                "status": "blocked_storage",
                "estimated_peak_bytes": 1024,
                "reserve_bytes": 0,
                "required_available_bytes": 1024,
                "root": str(root),
                "filesystem_free_bytes": 512,
                "quota_available_bytes": 512,
                "available_bytes": 512,
                "quota_error": None,
            }
            with mock.patch(
                "emuflow.validation_farm.preflight_experiment_storage",
                return_value=blocked,
            ):
                prepared = prepare_validation_farm(spec, farm)
                self.assertEqual(prepared["status"], "valid")
                self.assertEqual(
                    read_json(farm / "tasks/task-0/state.json")["status"],
                    "blocked_storage",
                )
                with mock.patch(
                    "emuflow.validation_farm.subprocess.run"
                ) as runner:
                    launched = launch_validation_farm(farm)
                self.assertEqual(launched["status"], "blocked_storage")
                runner.assert_not_called()

    def test_expired_lease_requires_dead_process_probe_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            state_path = farm / "tasks/task-0/state.json"
            expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            state = read_json(state_path)
            state.update(
                {
                    "status": "running",
                    "pid": 1234,
                    "attempt": 1,
                    "lease_expires_at": expired,
                }
            )
            write_json(state_path, state)
            dead = subprocess.CompletedProcess([], 1, "", "no process")
            with mock.patch(
                "emuflow.validation_farm.subprocess.run", return_value=dead
            ) as probe:
                report = reconcile_validation_farm(farm)
            self.assertEqual(report["retryable"], 1)
            self.assertEqual(read_json(state_path)["status"], "retryable")
            self.assertIn("kill -0 1234", probe.call_args.args[0])

    def test_retry_uses_a_new_attempt_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, _ = self._fixture(root, task_count=1)
            farm = root / "farm"
            prepare_validation_farm(spec, farm)
            task_path = farm / "tasks/task-0/task.json"
            with mock.patch.object(socket, "gethostname", return_value="node-a"):
                first = run_validation_farm_task(task_path)
            self.assertEqual(first["attempt"], 1)
            state_path = farm / "tasks/task-0/state.json"
            state = read_json(state_path)
            state["status"] = "retryable"
            write_json(state_path, state)
            with mock.patch.object(socket, "gethostname", return_value="node-a"):
                second = run_validation_farm_task(task_path)
            self.assertEqual(second["attempt"], 2)
            self.assertTrue(
                (farm / "runs/task-0/attempts/attempt-0001/done").is_file()
            )
            self.assertTrue(
                (farm / "runs/task-0/attempts/attempt-0002/done").is_file()
            )


if __name__ == "__main__":
    unittest.main()
