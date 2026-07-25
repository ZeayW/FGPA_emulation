import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks/rtl_catalog.json"


class RtlCatalogTest(unittest.TestCase):
    def test_catalog_entries_are_pinned_and_unique(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema"], "emuflow.rtl-catalog/v1")
        identifiers = set()
        priorities = set()
        for design in catalog["designs"]:
            self.assertNotIn(design["id"], identifiers)
            self.assertNotIn(design["priority"], priorities)
            identifiers.add(design["id"])
            priorities.add(design["priority"])
            self.assertRegex(design["revision"], re.compile(r"^[0-9a-f]{40}$"))
            self.assertTrue(design["repository"].startswith("https://github.com/"))
            self.assertTrue(design["tops"])
            self.assertTrue(design["sparse_paths"])


if __name__ == "__main__":
    unittest.main()
