#!/usr/bin/env python3
"""Fetch hash-pinned public 2025 EDA Elite multi-FPGA benchmarks."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path


COMMIT = "45315b739e6678bf04605aaa246285c768bc8e13"
REPOSITORY = "https://github.com/nsyw705/EDA-2025-git"
LICENSE = "MIT"
LICENSE_URL = f"{REPOSITORY}/blob/{COMMIT}/LICENSE"
RAW_ROOT = (
    "https://raw.githubusercontent.com/nsyw705/EDA-2025-git/"
    f"{COMMIT}/data_case"
)
OFFICIAL_SPECIFICATION = (
    "https://edaoss.icisc.cn/file/cacheFile/2025/8/11/"
    "1e213a00cbd94e2b91e997740753cb60.pdf"
)

# SHA-256 and byte length are intentionally independent of GitHub's object
# metadata.  A valid cache is therefore safe to reuse offline.
CASES = {
    "case01": [
        ("design.info", "9f63692a31df600224c5c7a925fba5935677f093ace4dd51ac75989b7aa8502f", 19),
        ("design.net", "25864402d5215716acffbbe13d7be3ea22a30fb6fba5de0b9e3306ea1bd86f79", 163),
        ("design.topo", "736c929ee91ca2708db40f7d3c8688232e520dbdb5835bb5e2f3945602335659", 48),
        ("design.fpga.out", "d32808090282795e6ee2b24cafdd6abef788959ea614397de75fe47522fe5742", 71),
    ],
    "case02": [
        ("design.info", "0668b01b54cab185920c7b637c84bd8338d48277ed3e18a7a1218ce2a10e094f", 40),
        ("design.net", "5f949fda97cda233dc6b2cc908bd5656a3a9b708d698cbe4e06c9b0b4eed6ba7", 44051),
        ("design.topo", "5ec109fe92ed4bbeaddbe9d1d7556fb4462fe6c7fd837f3f436c997ee126c7ac", 160),
        ("design.fpga.out", "c888554ed52e11b1b302c1dd77c1eeaa2d8f312a869dec2dfd66a16632239614", 2924),
    ],
    "case03": [
        ("design.info", "a313b6ed129f48d99f5970a1030d205b5726503d77d3e693f4f9a97745c0ff8b", 215),
        ("design.net", "43938c6145f18da94bf1eb131d9b4deb619f44201de767786356bdc95fb9c86a", 1060154),
        ("design.topo", "83d517ba6011fc5083cfedab684b37afb8d30ce718ce9d2d810411214ae89cb7", 2198),
        ("design.fpga.out", "40a88f0a647436877a602200b9c6a5be4c429e1b8060f4ffcd2ca0917dda7cc6", 69202),
    ],
    "case04": [
        ("design.info", "fd4978e2cc2784d8800c96a0d35fc9e9509f356b203f160fe7c901d9f11a54f1", 439),
        ("design.net", "cbc7e54a2657d1bdfe0a90a637f6128254354ef039def9bc1857177e780bcfdd", 28270485),
        ("design.topo", "ba3e78b6c6cdc72a6eccc47b9fa06411f4331c5757f5c193f6ef0bfaf63b80f6", 8503),
        ("design.fpga.out", "a9245b4156781310c5c933503161d101b740a5ced9a34529e5c029a02ca5ea1b", 7889207),
    ],
}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_atomic(destination: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
    os.replace(temporary, destination)


def download(case: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, expected_sha256, expected_size in CASES[case]:
        destination = output_dir / filename
        payload = destination.read_bytes() if destination.is_file() else b""
        status = "already-present"
        if len(payload) != expected_size or sha256(payload) != expected_sha256:
            url = f"{RAW_ROOT}/{case}/{filename}"
            request = urllib.request.Request(
                url, headers={"User-Agent": "EmuFlow benchmark fetcher"}
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
            if len(payload) != expected_size or sha256(payload) != expected_sha256:
                raise RuntimeError(f"verification failed for {url}")
            write_atomic(destination, payload)
            status = "downloaded"
        records.append(
            {
                "name": filename,
                "bytes": expected_size,
                "sha256": expected_sha256,
                "status": status,
            }
        )
    provenance = {
        "schema": "emuflow.public-benchmark-fetch/v1",
        "official_specification": OFFICIAL_SPECIFICATION,
        "repository": REPOSITORY,
        "commit": COMMIT,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "scope": "public contest benchmark inputs only; participant implementation and checker binary are not incorporated",
        "case": case,
        "files": records,
    }
    (output_dir / "SOURCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(download(args.case, args.out), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
