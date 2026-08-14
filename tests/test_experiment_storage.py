import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from emuflow.errors import ValidationError
from emuflow.experiment_storage import (
    _quota_available_bytes,
    preflight_experiment_storage,
    prepare_experiment_scratch,
    storage_budget,
    validate_experiment_write_path,
)


class ExperimentStorageTest(unittest.TestCase):
    def test_validation_hosts_reject_every_external_write_root(self) -> None:
        with self.assertRaisesRegex(ValidationError, "restricted"):
            validate_experiment_write_path(
                Path("/tmp/emuflow"), hostname="hpc8"
            )
        accepted = validate_experiment_write_path(
            Path("/research/d4/gds/ziyiwang21/emuflow/runs/x"),
            hostname="linux10-2",
        )
        self.assertEqual(
            accepted,
            Path("/research/d4/gds/ziyiwang21/emuflow/runs/x"),
        )

    def test_task_scratch_overrides_all_common_temporary_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scratch, environment = prepare_experiment_scratch(
                root, require_research=False
            )
            self.assertEqual(scratch, root.resolve() / "scratch")
            self.assertTrue((scratch / "tmp").is_dir())
            self.assertTrue((scratch / "cache").is_dir())
            self.assertEqual(set(environment), {"TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME"})
            self.assertTrue(
                all(
                    Path(value).is_relative_to(root.resolve())
                    for value in environment.values()
                )
            )

    def test_quota_parser_uses_the_tighter_nonzero_limit(self) -> None:
        output = """
Disk quotas for user test:
Filesystem blocks quota limit grace files quota limit grace
/dev/storage 900000 950000 1000000 - 1 0 0
"""
        self.assertEqual(_quota_available_bytes(output), 50_000 * 1024)

    def test_quota_parser_selects_only_the_storage_filesystem(self) -> None:
        output = """
Disk quotas for user test:
Filesystem blocks quota limit grace files quota limit grace
uranus:/d0/data 2764824* 2500000 2505000 none 1 0 0
rdata8:/s1/d4 937823428 1000000000 1000000000 - 1 0 0
"""
        self.assertEqual(
            _quota_available_bytes(output, filesystem="rdata8:/s1/d4"),
            (1_000_000_000 - 937_823_428) * 1024,
        )

    def test_storage_budget_accepts_selected_row_when_other_mount_is_over_quota(self) -> None:
        quota_output = """
Disk quotas for user test:
Filesystem blocks quota limit grace files quota limit grace
uranus:/d0/data 2764824* 2500000 2505000 none 1 0 0
rdata8:/s1/d4 938188088 1000000000 1000000000 - 1 0 0
"""
        calls = [
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                    "rdata8:/s1/d4 1 1 1 1% /research/d4\n"
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=1, stdout=quota_output, stderr=""),
        ]
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "emuflow.experiment_storage.subprocess.run", side_effect=calls
        ):
            result = storage_budget(Path(temporary))
        self.assertEqual(result["quota_filesystem"], "rdata8:/s1/d4")
        self.assertEqual(
            result["quota_available_bytes"],
            (1_000_000_000 - 938_188_088) * 1024,
        )
        self.assertIsNone(result["quota_error"])

    def test_storage_boundary_rejects_internal_symlink_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real")
            with mock.patch(
                "emuflow.experiment_storage.VALIDATION_STORAGE_ROOT", root
            ):
                with self.assertRaisesRegex(ValidationError, "symlink"):
                    validate_experiment_write_path(
                        root / "alias/run", require_research=True
                    )

    def test_validation_server_never_falls_back_when_quota_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            budget = {
                "root": str(root),
                "filesystem_free_bytes": 10**12,
                "quota_available_bytes": None,
                "quota_filesystem": "test:/research",
                "available_bytes": 10**12,
                "quota_error": "quota command failed",
            }
            with mock.patch(
                "emuflow.experiment_storage.VALIDATION_STORAGE_ROOT", root
            ), mock.patch(
                "emuflow.experiment_storage.storage_budget", return_value=budget
            ):
                result = preflight_experiment_storage(
                    root, 1, reserve_bytes=0, require_research=True
                )
            self.assertEqual(result["status"], "blocked_storage")
            self.assertEqual(result["block_reason"], "quota-unavailable")


if __name__ == "__main__":
    unittest.main()
