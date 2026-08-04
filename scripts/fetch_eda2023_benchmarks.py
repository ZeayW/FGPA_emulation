#!/usr/bin/env python3
"""Fetch verified public 2023 EDA Elite die-routing benchmark files."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path


COMMIT = "1f05cfd366b9565eb604380f5feed38b25baaff7"
REPOSITORY = "https://github.com/heyiWF/FPGA-Die-Routing"
RAW_ROOT = f"https://raw.githubusercontent.com/heyiWF/FPGA-Die-Routing/{COMMIT}/TestCase20231027"

CASES = {
    "case1": [("design.die.network", "99373dbaf06899cd9a0530557ec21eebe17854ed", 194), ("design.die.position", "50237c4adbe00922e0a2ef03d54b62358aa6ae97", 72), ("design.fpga.die", "c7c126bb36754fdec6f2cef80cf3aabb61779499", 52), ("design.net", "03fa8ca6f52d5e8f98d4ec0f1ea82f70f8d8ad45", 68)],
    "case2": [("design.die.network", "99373dbaf06899cd9a0530557ec21eebe17854ed", 194), ("design.die.position", "94e80664cff239b39c22d2ecb7826cf83c1226f7", 328), ("design.fpga.die", "c7c126bb36754fdec6f2cef80cf3aabb61779499", 52), ("design.net", "998ccf5f036c74028012288eb0e6a254f3414263", 2385)],
    "case3": [("design.die.network", "cd01913a549f5091e8da091d3f2b770cb2314f3c", 186), ("design.die.position", "c0bfb3e3333fa34077e7394d2564ca607311ebff", 320), ("design.fpga.die", "c7c126bb36754fdec6f2cef80cf3aabb61779499", 52), ("design.net", "f92b24023437e487e44d17ef3fcb8f19fe177adb", 2339)],
    "case4": [("design.die.network", "7f1dcfbd75b07fdfccf86f1a82bb2ae9a645ffa5", 190), ("design.die.position", "a53315a91445bb5a185951bba07050c3544b83a5", 2198), ("design.fpga.die", "c7c126bb36754fdec6f2cef80cf3aabb61779499", 52), ("design.net", "ccb5b936af1a78211c4eb10edcbbb9bea3d1bf12", 15136)],
    "case5": [("design.die.network", "1ada175211ea61a64f6a34f5d3b604a6b7abd018", 382), ("design.die.position", "d17c234d88e831fe63f0ea94cb7694d2931e1c45", 29470), ("design.fpga.die", "375343fead33800b9c678134ef95a298af1cbad1", 81), ("design.net", "dcf852fdfd111d9d208a1c9b3cb8c5cbf1445a5d", 207502)],
    "case6": [("design.die.network", "c343ecfa58af87755bdc7bdbbf7de5aeed96bce5", 430), ("design.die.position", "1d70fec0da6fa813ec17d1e883e1ca0477134329", 905731), ("design.fpga.die", "375343fead33800b9c678134ef95a298af1cbad1", 81), ("design.net", "1c32e929a7446a1da4827d1c6970af202ac0abf6", 6932965)],
    "case7": [("design.die.network", "1115e10ee3b782a2fb889210740cb812c20e4fbb", 686), ("design.die.position", "8affd0c77574b19015aa6f5ab6fcf0fd60dd1b12", 373565), ("design.fpga.die", "0b140dc79c6f6a3e54e135df3ab5187afbb94564", 112), ("design.net", "55b3a3d054d3df2ce471673aea11f9677603726a", 2994297)],
    "case8": [("design.die.network", "d1795547fd7f63499ef2bb00a1ac9da8066bcb20", 686), ("design.die.position", "7c1095fd675f2c6d643b260aca0c625593279e96", 428691), ("design.fpga.die", "0b140dc79c6f6a3e54e135df3ab5187afbb94564", 112), ("design.net", "495b250a17a4f4dd674939a1907310784c3fb41d", 3506986)],
    "case9": [("design.die.network", "c1286cd189f76dbc94590a9cba060305fd458fbd", 758), ("design.die.position", "03eb90ee56b77ad0283a91010804d027907b6dde", 6167466), ("design.fpga.die", "0b140dc79c6f6a3e54e135df3ab5187afbb94564", 112), ("design.net", "b5f729cb638d62003e82ffe31d5abba86c4352fa", 45677854)],
}


def git_blob_id(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def download(case: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, expected_sha, expected_size in CASES[case]:
        destination = output_dir / filename
        payload = destination.read_bytes() if destination.is_file() else b""
        status = "already-present"
        if len(payload) != expected_size or git_blob_id(payload) != expected_sha:
            url = f"{RAW_ROOT}/test{case}/{filename}"
            request = urllib.request.Request(url, headers={"User-Agent": "EmuFlow benchmark fetcher"})
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            if len(payload) != expected_size or git_blob_id(payload) != expected_sha:
                raise RuntimeError(f"verification failed for {url}")
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix=f".{filename}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.replace(temporary, destination)
            status = "downloaded"
        records.append({"name": filename, "bytes": expected_size, "git_blob_sha1": expected_sha, "status": status})
    provenance = {
        "schema": "emuflow.public-benchmark-fetch/v1",
        "repository": REPOSITORY,
        "commit": COMMIT,
        "scope": "contest benchmark data only; participant implementation is not used",
        "case": case,
        "files": records,
    }
    (output_dir / "SOURCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
