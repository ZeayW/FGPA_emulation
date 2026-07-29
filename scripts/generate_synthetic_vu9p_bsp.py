#!/usr/bin/env python3

"""Generate the explicit synthetic VU9P mesh BSP used by Phase 6B tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(platform: dict) -> dict:
    fpga_by_id = {item["id"]: item for item in platform["fpgas"]}
    banks = {fpga: [] for fpga in fpga_by_id}
    pins = {fpga: [] for fpga in fpga_by_id}
    channels = []
    next_bank = {fpga: 0 for fpga in fpga_by_id}
    pin_by_domain_lane = {}

    domains = []
    for link in sorted(platform["links"], key=lambda item: item["id"]):
        first, second = link["endpoints"]
        if link["direction"] != "full_duplex":
            raise ValueError("synthetic BSP generator expects full-duplex links")
        domains.extend(
            [
                (link, first, second),
                (link, second, first),
            ]
        )
    for domain_index, (link, source, sink) in enumerate(domains):
        bank_ids = {}
        for fpga, direction in ((source, "tx"), (sink, "rx")):
            bank_id = f"SYN_BANK_{next_bank[fpga]}"
            next_bank[fpga] += 1
            bank_ids[(fpga, direction)] = bank_id
            banks[fpga].append(
                {
                    "id": bank_id,
                    "voltage": 1.8,
                    "iostandards": ["LVCMOS18"],
                    "max_pins": link["data_lanes_per_direction"],
                }
            )
        for lane in range(link["data_lanes_per_direction"]):
            for endpoint_index, (fpga, direction) in enumerate(
                ((source, "tx"), (sink, "rx"))
            ):
                bank_id = bank_ids[(fpga, direction)]
                pin_id = (
                    f"{fpga}:{link['id']}:{source}-to-{sink}:"
                    f"{direction}:{lane}"
                )
                permutation = (
                    lane * (13 if endpoint_index == 0 else 19)
                    + domain_index * (7 if endpoint_index == 0 else 11)
                ) % link["data_lanes_per_direction"]
                denominator = max(1, link["data_lanes_per_direction"] - 1)
                pins[fpga].append(
                    {
                        "id": pin_id,
                        "fpga": fpga,
                        "package_pin": (
                            f"SYN_{fpga.upper()}_B{bank_id.rsplit('_', 1)[1]}"
                            f"_P{lane:02d}"
                        ),
                        "bank": bank_id,
                        "connector": f"SYN_CONN_{link['id']}",
                        "connector_pin": (
                            f"{source}_to_{sink}_{lane:02d}"
                        ),
                        "directions": [direction],
                        "iostandards": ["LVCMOS18"],
                        "region_y": permutation / denominator,
                        "clock_capable": False,
                        "reserved": False,
                    }
                )
                pin_by_domain_lane[
                    (link["id"], source, sink, fpga, lane)
                ] = pin_id
            source_pin = pins[source][-1]
            sink_pin = pins[sink][-1]
            channels.append(
                {
                    "id": (
                        f"{link['id']}:{source}-to-{sink}:"
                        f"channel-{lane:02d}"
                    ),
                    "link": link["id"],
                    "source": source,
                    "sink": sink,
                    "source_pin": pin_by_domain_lane[
                        (link["id"], source, sink, source, lane)
                    ],
                    "sink_pin": pin_by_domain_lane[
                        (link["id"], source, sink, sink, lane)
                    ],
                    "iostandard": "LVCMOS18",
                    "max_frequency_mhz": max(
                        300.0, link["fabric_clock_mhz"]
                    ),
                    "skew_ps": round(
                        20.0
                        + 80.0
                        * abs(
                            source_pin["region_y"] - sink_pin["region_y"]
                        )
                        + lane % 5,
                        6,
                    ),
                }
            )
    return {
        "schema": "emuflow.hardware-bsp/v1",
        "platform": platform["platform"]["name"],
        "board": {
            "name": "synthetic_xcvu9p_4fpga_mesh",
            "revision": "generated-v1",
            "qualification": "synthetic_validation",
            "warning": (
                "Synthetic package names and connectivity for algorithm "
                "validation only; not a manufacturable board definition."
            ),
        },
        "fpgas": [
            {
                "id": fpga,
                "part": fpga_by_id[fpga]["part"],
                "banks": sorted(banks[fpga], key=lambda item: item["id"]),
                "pins": sorted(pins[fpga], key=lambda item: item["id"]),
            }
            for fpga in sorted(fpga_by_id)
        ],
        "channels": sorted(channels, key=lambda item: item["id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    platform = json.loads(args.platform.read_text(encoding="utf-8"))
    document = build(platform)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
