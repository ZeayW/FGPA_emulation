import json
import tempfile
import unittest
from pathlib import Path

from emuflow.benchmark import BenchmarkRun
from emuflow.errors import EmuFlowError, ValidationError


ROOT = Path(__file__).resolve().parents[1]
SERV_SPEC = ROOT / "benchmarks" / "runs" / "serv_l1.json"


class BenchmarkRunTest(unittest.TestCase):
    def test_serv_l1_spec_and_sources(self) -> None:
        spec = BenchmarkRun.load(SERV_SPEC)
        self.assertEqual(spec.value["top"], "serv_synth_wrapper")
        source_root = ROOT / "third_party" / "rtl" / "serv"
        if source_root.is_dir():
            sources = spec.resolve_sources(source_root)
            self.assertGreaterEqual(len(sources), 18)
            self.assertTrue(all(path.suffix == ".v" for path in sources))

    def test_missing_source_pattern_is_rejected(self) -> None:
        spec = BenchmarkRun.load(SERV_SPEC)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(EmuFlowError, "matched no files"):
                spec.resolve_sources(Path(temporary))

    def test_unknown_policy_is_rejected(self) -> None:
        value = json.loads(SERV_SPEC.read_text(encoding="utf-8"))
        value["synthesis"]["policy"] = "unknown"
        with self.assertRaisesRegex(ValidationError, "unsupported value"):
            BenchmarkRun(value)


if __name__ == "__main__":
    unittest.main()
