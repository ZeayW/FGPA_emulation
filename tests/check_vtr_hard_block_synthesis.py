#!/usr/bin/env python3

"""Exercise the source-built Yosys VTR hard-block mapping in CTest."""

from __future__ import annotations

import argparse
from pathlib import Path

from emuflow.vpr import VTR_HARD_BLOCK_PROFILE, run_vtr_yosys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--rtl", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = run_vtr_yosys(
        [args.rtl],
        "vtr_hard_blocks",
        args.output,
        executable=args.yosys,
        hard_blocks=True,
    )
    atoms = report["hard_block_atoms"]
    expected = {
        "dual_port_ram": 0,
        "multiply": 1,
        "single_port_ram": 32,
    }
    if report["mapping_profile"] != VTR_HARD_BLOCK_PROFILE:
        raise RuntimeError("unexpected VTR hard-block mapping profile")
    if atoms != expected:
        raise RuntimeError(
            f"unexpected VTR hard-block atom counts: {atoms!r}"
        )
    dual_report = run_vtr_yosys(
        [args.rtl],
        "vtr_dual_port_ram",
        args.output.with_name(args.output.stem + "_dual" + args.output.suffix),
        executable=args.yosys,
        hard_blocks=True,
    )
    dual_atoms = dual_report["hard_block_atoms"]
    dual_expected = {
        "dual_port_ram": 32,
        "multiply": 0,
        "single_port_ram": 0,
    }
    if dual_atoms != dual_expected:
        raise RuntimeError(
            f"unexpected VTR dual-port RAM atom counts: {dual_atoms!r}"
        )
    print(
        "VTR hard-block synthesis passed:",
        {"single_port_and_multiply": atoms, "dual_port": dual_atoms},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
