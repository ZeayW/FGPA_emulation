import json
import tempfile
import unittest
from pathlib import Path

from emuflow.contest_public import (
    PUBLIC_CONTEST_FETCH_REPORT_SCHEMA,
    build_contest_fetch_farm_spec,
    fetch_public_contest_case,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json


class PublicContestFetchTest(unittest.TestCase):
    def _fixture(self, root: Path, revision: str = "1" * 40) -> tuple[Path, Path]:
        scripts = root / "scripts"
        scripts.mkdir()
        fetcher = scripts / "fetch_fixture.py"
        fetcher.write_text(
            """import argparse, hashlib, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--case', required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True)
payload = b'abc'
(a.out / 'design.net').write_bytes(payload)
(blob := hashlib.sha1(f'blob {len(payload)}\\0'.encode('ascii') + payload).hexdigest())
(a.out / 'SOURCE.json').write_text(json.dumps({
    'schema': 'emuflow.public-benchmark-fetch/v1',
    'commit': '1111111111111111111111111111111111111111',
    'case': a.case,
    'files': [{'name': 'design.net', 'bytes': 3, 'git_blob_sha1': blob, 'status': 'downloaded'}],
}))
""",
            encoding="utf-8",
        )
        matrix = {
            "schema": "emuflow.contest-validation-matrix/v1",
            "cases": [
                {
                    "id": "eda2023.case1",
                    "suite": "eda2023",
                    "case": "case1",
                    "source": {
                        "fetcher": "scripts/fetch_fixture.py",
                        "revision_kind": "git-commit",
                        "revision": revision,
                    },
                    "input_bytes": 3,
                    "tier": "smoke",
                    "qualification": "catalogued",
                    "target_gates": ["fetch", "import"],
                    "evidence": [],
                }
            ],
        }
        matrix_path = root / "matrix.json"
        matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
        return matrix_path, root

    def test_fetch_runs_pinned_script_and_checks_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix, fetcher_root = self._fixture(root)
            report = fetch_public_contest_case(
                matrix,
                "eda2023.case1",
                root / "run",
                fetcher_root=fetcher_root,
            )
            self.assertEqual(report["schema"], PUBLIC_CONTEST_FETCH_REPORT_SCHEMA)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["validation"]["input_bytes"], 3)
            self.assertEqual(
                read_json(root / "run" / "fetch_report.json"), report
            )

    def test_fetch_rejects_revision_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix, fetcher_root = self._fixture(root, revision="2" * 40)
            with self.assertRaisesRegex(ValidationError, "revision"):
                fetch_public_contest_case(
                    matrix,
                    "eda2023.case1",
                    root / "run",
                    fetcher_root=fetcher_root,
                )

    def test_matrix_compiles_to_version_pinned_farm_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix, _ = self._fixture(root)
            spec_path = root / "farm.json"
            report = build_contest_fetch_farm_spec(
                matrix,
                source_commit="a" * 40,
                install_dir=root / "installs" / ("a" * 40),
                nodes=["hpc1", "hpc2"],
                output_path=spec_path,
                farm_id="contest-smoke",
            )
            spec = read_json(spec_path)
            self.assertEqual(report["tasks"], 1)
            self.assertEqual(spec["source_commit"], "a" * 40)
            self.assertEqual(spec["tasks"][0]["id"], "fetch-eda2023-case1")
            self.assertEqual(spec["tasks"][0]["command"][0], "{install}/bin/emuflow")
            self.assertIn("{run_dir}", spec["tasks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
