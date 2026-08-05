import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import EmuFlowError
from emuflow.native_tools import resolve_native_executable


class NativeToolsTest(unittest.TestCase):
    def test_resolves_only_configured_in_tree_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "bin" / "yosys"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            with patch.dict(
                os.environ,
                {"EMUFLOW_NATIVE_ROOT": str(root), "PATH": "/does/not/matter"},
                clear=False,
            ):
                self.assertEqual(
                    resolve_native_executable("yosys"),
                    str(executable.resolve()),
                )

    def test_does_not_silently_use_path_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path_bin = root / "path-bin"
            path_bin.mkdir()
            executable = path_bin / "repart"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            with patch.dict(
                os.environ,
                {
                    "EMUFLOW_NATIVE_ROOT": str(root / "empty"),
                    "PATH": str(path_bin),
                },
                clear=False,
            ), patch(
                "emuflow.native_tools.REPO_ROOT", root / "empty-repo"
            ):
                with self.assertRaisesRegex(
                    EmuFlowError, "in-tree repart build product"
                ):
                    resolve_native_executable("repart")

    def test_explicit_override_is_preserved_for_comparison_runs(self) -> None:
        self.assertEqual(
            resolve_native_executable("openroad", "/comparison/openroad"),
            "/comparison/openroad",
        )

    def test_relative_override_is_bound_before_backend_changes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "build" / "bin" / "vpr"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                resolved = resolve_native_executable(
                    "vpr", "build/bin/vpr"
                )
            finally:
                os.chdir(previous)
        self.assertEqual(resolved, str(executable.resolve()))


if __name__ == "__main__":
    unittest.main()
