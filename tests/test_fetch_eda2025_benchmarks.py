import hashlib
import io
import json

from scripts import fetch_eda2025_benchmarks as fetcher


def test_public_manifest_pins_all_four_cases_and_license():
    assert set(fetcher.CASES) == {"case01", "case02", "case03", "case04"}
    assert sum(size for files in fetcher.CASES.values() for _, _, size in files) == 37_347_879
    assert len(fetcher.COMMIT) == 40
    assert fetcher.LICENSE == "MIT"


def test_fetch_verifies_sha256_and_reuses_valid_cache(tmp_path, monkeypatch):
    payloads = {
        "design.info": b"F1 3\n",
        "design.net": b"g1 1 g2\n",
        "design.topo": b"F1: 0\n",
        "design.fpga.out": b"F1: g1 g2\n",
    }
    manifest = [
        (name, hashlib.sha256(payload).hexdigest(), len(payload))
        for name, payload in payloads.items()
    ]
    monkeypatch.setitem(fetcher.CASES, "case01", manifest)

    def provide(request, timeout):
        assert timeout == 300
        return io.BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", provide)
    report = fetcher.download("case01", tmp_path)
    assert {record["status"] for record in report["files"]} == {"downloaded"}
    assert report["commit"] == fetcher.COMMIT
    assert report["scope"].endswith("are not incorporated")
    assert json.loads((tmp_path / "SOURCE.json").read_text())["license"] == "MIT"

    def unexpected_download(*args, **kwargs):
        raise AssertionError("valid local files must not be downloaded again")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", unexpected_download)
    cached = fetcher.download("case01", tmp_path)
    assert {record["status"] for record in cached["files"]} == {
        "already-present"
    }
