import hashlib
import unittest

from scripts.fetch_repart_benchmarks import CASES, git_blob_id


class RePartBenchmarkFetcherTest(unittest.TestCase):
    def test_every_pinned_git_blob_id_is_complete_hex(self):
        for case, files in CASES.items():
            for filename, (blob_id, size) in files.items():
                with self.subTest(case=case, filename=filename):
                    self.assertRegex(blob_id, r"^[0-9a-f]{40}$")
                    self.assertGreater(size, 0)

    def test_git_blob_id_uses_git_object_framing(self):
        payload = b"EmuFlow\n"
        expected = hashlib.sha1(b"blob 8\0" + payload).hexdigest()
        self.assertEqual(git_blob_id(payload), expected)


if __name__ == "__main__":
    unittest.main()
