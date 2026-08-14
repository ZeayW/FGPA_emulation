import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.experiment_identity import (
    build_implementation_closure,
    validate_implementation_closure,
)


class ExperimentIdentityTest(unittest.TestCase):
    def test_closure_is_portable_and_detects_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/stage.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tool").write_bytes(b"binary")
            closure = build_implementation_closure(root, ["tool", "src"])
            self.assertEqual(
                [item["path"] for item in closure["files"]],
                ["src/stage.py", "tool"],
            )
            validated = validate_implementation_closure(closure, root=root)
            self.assertEqual(
                validated["implementation_sha256"],
                closure["implementation_sha256"],
            )
            (root / "src/stage.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "source root"):
                validate_implementation_closure(closure, root=root)

    def test_directory_closure_detects_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/a.py").write_text("a\n", encoding="utf-8")
            closure = build_implementation_closure(root, ["src"])
            (root / "src/b.py").write_text("b\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "source root"):
                validate_implementation_closure(closure, root=root)

    def test_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "real").write_text("value", encoding="utf-8")
            (root / "link").symlink_to(root / "real")
            with self.assertRaisesRegex(ValidationError, "symlink"):
                build_implementation_closure(root, ["link"])


if __name__ == "__main__":
    unittest.main()
