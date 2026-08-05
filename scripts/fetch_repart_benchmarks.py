#!/usr/bin/env python3
"""Fetch selected public RePart contest cases at a verified fixed commit."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path


COMMIT = "211a9d8fd526576387cad7ac6dd3531354aeb31c"
REPOSITORY = "https://github.com/Welement-zyf/RePart"
RAW_ROOT = f"https://raw.githubusercontent.com/Welement-zyf/RePart/{COMMIT}"

# Git blob ids and byte sizes come from the fixed upstream commit.  Verifying
# the Git object hash detects both truncated downloads and changed contents.
CASES = {
    "case01": {
        "design.are": ("ae6a13034d0eeb250602a00463bf257b0c75d037", 315),
        "design.info": ("4e151d5fa699cb81cbf0a8ec1cfe4f2b9e3d1fa9", 107),
        "design.net": ("05570814cc36c15ead016377239bdcbc7b12399e", 163),
        "design.topo": ("7978b223bc05b93b420ce60340baf28f21c4f76c", 37),
    },
    "case03": {
        "design.are": ("a27d8747512686dc9d14a2024a2ea9f72b1740bc", 254412),
        "design.info": ("a35c7ec373d44a568a5bf25dc46c8cfd0b754fb7", 1079),
        "design.net": ("eaa88986e766f371f5339df59f9abe3c7f1f8112", 1091241),
        "design.topo": ("6a1740032e9426f0748d130ef8d2584501118563", 1187),
    },
    "case04": {
        "design.are": ("2bbcadf9512d072e44aea56a77ae5cddf6cd031c", 23888896),
        "design.info": ("fda75f162af7d724811002f64b6e555fe113dc64", 2359),
        "design.net": ("4d526c3d0ddcbcb3ed2e8154e0c88545678512a9", 29224302),
        "design.topo": ("bc6b5c03b72494990547e1514b397662731fe8fa", 2384),
    },
    "case05": {
        "design.are": ("087aae6a88f9f5c6f638540d97623c612cda4ffc", 35256),
        "design.info": ("69ac55cf0411ca4077f0f8e6d241f15e82fa4b8d", 132),
        "design.net": ("3fd0a6d76ff1fc42552b3f4b387542975eb4e539", 57775),
        "design.topo": ("e930ca5b8f4c4331749bcdaecc59bb4692b0f66e", 38),
    },
}


def git_blob_id(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def verified(payload: bytes, expected_sha: str, expected_size: int) -> bool:
    return len(payload) == expected_size and git_blob_id(payload) == expected_sha


def download(case: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for filename, (expected_sha, expected_size) in CASES[case].items():
        destination = output_dir / filename
        if destination.is_file():
            payload = destination.read_bytes()
            if verified(payload, expected_sha, expected_size):
                files.append(
                    {
                        "name": filename,
                        "bytes": expected_size,
                        "git_blob_sha1": expected_sha,
                        "status": "already-present",
                    }
                )
                continue
        url = f"{RAW_ROOT}/testcase/{case}/{filename}"
        request = urllib.request.Request(
            url, headers={"User-Agent": "EmuFlow benchmark fetcher"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        if not verified(payload, expected_sha, expected_size):
            raise RuntimeError(
                f"verification failed for {url}: bytes={len(payload)}, "
                f"git_blob_sha1={git_blob_id(payload)}"
            )
        with tempfile.NamedTemporaryFile(
            dir=output_dir, prefix=f".{filename}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, destination)
        files.append(
            {
                "name": filename,
                "bytes": expected_size,
                "git_blob_sha1": expected_sha,
                "status": "downloaded",
            }
        )
    provenance = {
        "schema": "emuflow.public-benchmark-fetch/v1",
        "repository": REPOSITORY,
        "commit": COMMIT,
        "case": case,
        "files": files,
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
