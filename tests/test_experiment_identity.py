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

    def test_python_symbol_closure_ignores_unrelated_stage_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "stage.py").write_text(
                "from math import sqrt\n"
                "VALUE = 2\n"
                "def helper(value):\n    return sqrt(value) + VALUE\n"
                "def selected(value):\n    return helper(value)\n"
                "def unrelated():\n    return 1\n",
                encoding="utf-8",
            )
            component = "stage.py::selected"
            closure = build_implementation_closure(root, [component])
            (root / "stage.py").write_text(
                "from math import sqrt\n"
                "from math import ceil\n"
                "VALUE = 2\n"
                "def helper(value):\n    return sqrt(value) + VALUE\n"
                "def selected(value):\n    return helper(value)\n"
                "def unrelated():\n    return ceil(2.5)\n",
                encoding="utf-8",
            )
            self.assertEqual(
                closure["implementation_sha256"],
                build_implementation_closure(root, [component])[
                    "implementation_sha256"
                ],
            )
            validate_implementation_closure(closure, root=root)

            (root / "stage.py").write_text(
                (root / "stage.py")
                .read_text(encoding="utf-8")
                .replace("sqrt(value) + VALUE", "sqrt(value) * VALUE"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "source root"):
                validate_implementation_closure(closure, root=root)

    def test_python_symbol_components_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "stage.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "symbols are missing"):
                build_implementation_closure(root, ["stage.py::absent"])
            with self.assertRaisesRegex(ValidationError, "sorted unique"):
                build_implementation_closure(root, ["stage.py::run,run"])


if __name__ == "__main__":
    unittest.main()
