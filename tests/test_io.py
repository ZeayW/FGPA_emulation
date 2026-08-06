import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.io import read_json, write_json


class JsonIoTest(unittest.TestCase):
    def test_write_json_replaces_only_after_complete_serialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "artifact.json"
            write_json(output, {"generation": 1})

            with patch(
                "emuflow.io.json.dump",
                side_effect=RuntimeError("injected serialization failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    write_json(output, {"generation": 2})

            self.assertEqual(read_json(output), {"generation": 1})
            self.assertEqual(list(root.glob(".*.tmp")), [])

            write_json(output, {"generation": 3})
            self.assertEqual(read_json(output), {"generation": 3})


if __name__ == "__main__":
    unittest.main()
