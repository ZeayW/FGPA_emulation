import hashlib
import io
import json
import urllib.error
import urllib.request
import zipfile

from scripts import fetch_eda2023_benchmarks as fetcher


def _zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_case10_manifest_pins_official_files():
    assert set(fetcher.CASES) == {f"case{index}" for index in range(1, 11)}
    assert sum(size for _, _, size in fetcher.OFFICIAL_CASE10) == 212_202_207
    assert fetcher.OFFICIAL_ARCHIVE_URL.startswith(
        "https://edaicisc.oss-cn-shanghai.aliyuncs.com/"
    )


def test_case10_fetch_extracts_and_verifies_nested_archive(tmp_path, monkeypatch):
    payloads = {
        "design.die.network": b"0 1\n1 0\n",
        "design.die.position": b"a Die0\n",
        "design.fpga.die": b"FPGA0:Die0\n",
        "design.net": b"n a\n",
    }
    manifest = [
        (name, hashlib.sha256(payload).hexdigest(), len(payload))
        for name, payload in payloads.items()
    ]
    inner = _zip_bytes(
        {
            f"TestCase20231027/testcase10/{name}": payload
            for name, payload in payloads.items()
        }
    )
    outer = _zip_bytes({"non-ascii-prefix/TestCase20231027.zip": inner})

    monkeypatch.setattr(fetcher, "OFFICIAL_CASE10", manifest)
    monkeypatch.setattr(fetcher, "OFFICIAL_ARCHIVE_SIZE", len(outer))
    monkeypatch.setattr(
        fetcher, "OFFICIAL_ARCHIVE_SHA256", hashlib.sha256(outer).hexdigest()
    )
    monkeypatch.setattr(
        fetcher, "OFFICIAL_INNER_SHA256", hashlib.sha256(inner).hexdigest()
    )
    monkeypatch.setattr(
        fetcher.urllib.request,
        "urlopen",
        lambda request, timeout: io.BytesIO(outer),
    )

    report = fetcher.download("case10", tmp_path)
    assert report["case"] == "case10"
    assert {record["status"] for record in report["files"]} == {"downloaded"}
    for name, payload in payloads.items():
        assert (tmp_path / name).read_bytes() == payload
    assert json.loads((tmp_path / "SOURCE.json").read_text())["case"] == "case10"

    def unexpected_download(*args, **kwargs):
        raise AssertionError("valid local case10 must not be downloaded again")

    monkeypatch.setattr(fetcher.urllib.request, "urlopen", unexpected_download)
    cached = fetcher.download("case10", tmp_path)
    assert {record["status"] for record in cached["files"]} == {
        "already-present"
    }


def test_official_archive_retries_without_proxy(monkeypatch):
    def reject_proxy(*args, **kwargs):
        raise urllib.error.URLError("proxy rejected endpoint")

    monkeypatch.setattr(
        fetcher.urllib.request,
        "urlopen",
        reject_proxy,
    )

    class DirectOpener:
        def open(self, request, timeout):
            assert timeout == 300
            if request.full_url.startswith("https://"):
                raise urllib.error.URLError("legacy TLS rejected endpoint")
            assert request.full_url == fetcher.OFFICIAL_ARCHIVE_DIRECT_FALLBACK_URL
            return io.BytesIO(b"official archive")

    monkeypatch.setattr(
        fetcher.urllib.request,
        "build_opener",
        lambda handler: DirectOpener(),
    )
    request = urllib.request.Request("https://example.invalid/archive.zip")
    assert fetcher.read_official_archive(request) == b"official archive"
