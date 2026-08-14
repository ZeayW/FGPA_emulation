import tempfile
import unittest
from pathlib import Path
from unittest import mock

from emuflow.errors import ValidationError
from emuflow.experiment_storage import (
    _quota_available_bytes,
    preflight_experiment_storage,
    prepare_experiment_scratch,
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
