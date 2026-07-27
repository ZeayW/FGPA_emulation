import hashlib
import json
import re
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.benchmarks.fetch import fetch_archive


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks/rtl_catalog.json"


class RtlCatalogTest(unittest.TestCase):
    def test_catalog_entries_are_pinned_and_unique(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema"], "emuflow.rtl-catalog/v1")
        identifiers = set()
        priorities = set()
        tiers = set()
        for design in catalog["designs"]:
            self.assertNotIn(design["id"], identifiers)
            self.assertNotIn(design["priority"], priorities)
            identifiers.add(design["id"])
            priorities.add(design["priority"])
            self.assertRegex(design["revision"], re.compile(r"^[0-9a-f]{40}$"))
            self.assertTrue(design["repository"].startswith("https://github.com/"))
            self.assertTrue(design["tops"])
            self.assertTrue(design["sparse_paths"])
            self.assertRegex(design["validation_tier"], re.compile(r"^L[1-7](-L[1-7])?$"))
            self.assertTrue(design["feature_tags"])
            if "archive_url" in design or "archive_sha256" in design:
                self.assertTrue(design["archive_url"].startswith("https://"))
                self.assertRegex(
                    design["archive_sha256"], re.compile(r"^[0-9a-f]{64}$")
                )
            tiers.add(design["validation_tier"])
        self.assertIn("L1", tiers)
        self.assertIn("L7", tiers)

    def test_pinned_archive_fetch_is_hash_checked_and_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "source" / "upstream-revision"
            source_root.mkdir(parents=True)
            (source_root / "rtl.v").write_text(
                "module rtl; endmodule\n", encoding="utf-8"
            )
            archive_path = root / "source.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source_root, arcname=source_root.name)
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            design = {
                "id": "archive-fixture",
                "revision": "a" * 40,
                "archive_url": archive_path.as_uri(),
                "archive_sha256": digest,
            }

            destination = fetch_archive(design, root / "destination")
            self.assertEqual(
                (destination / "rtl.v").read_text(encoding="utf-8"),
                "module rtl; endmodule\n",
            )
            stamp = json.loads(
                (destination / ".emuflow-source.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stamp["revision"], "a" * 40)
            self.assertEqual(stamp["archive_sha256"], digest)


if __name__ == "__main__":
    unittest.main()
