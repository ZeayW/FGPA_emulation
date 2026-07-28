import json
import tempfile
import unittest
from pathlib import Path

from emuflow.bsp import build_bsp_requirements, run_phase8a
from emuflow.errors import ValidationError
from emuflow.platform import Platform


class Phase8ATest(unittest.TestCase):
    def _inputs(self):
        platform = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "bsp_test",
                    "kind": "virtual",
                    "description": "BSP readiness test",
                },
                "fpgas": [
                    {
                        "id": fpga,
                        "part": "xcvu3p-ffvc1517-2-e",
                        "utilization_limit": 0.75,
                        "capacity": {"lut": 100, "ff": 100},
                    }
                    for fpga in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "source_synchronous",
                        "data_lanes_per_direction": 2,
                        "fabric_clock_mhz": 250.0,
                        "latency_cycles": 2,
                    }
                ],
            }
        )
        release = {
            "schema": "emuflow.release-manifest/v1",
            "status": "pass",
            "release_scope": "board-independent-g0-g9",
            "source_commit": "a" * 40,
            "design": "dut",
            "platform": platform.name,
            "gates": {
                f"G{index}": {"status": "pass", "evidence": "test"}
                for index in range(10)
            },
            "artifacts": [
                {
                    "label": f"{fpga}.routed_dcp",
                    "bytes": 1,
                    "sha256": "0" * 64,
                }
                for fpga in ("fpga0", "fpga1")
            ],
            "board_binding": {"status": "virtual"},
        }
        phase6 = {
            "schema": "emuflow.phase6-report/v1",
            "status": "pass",
            "design": "dut",
            "platform": platform.name,
            "validation": {
                "virtual_anchors": 8,
                "unbound_package_pins": 8,
            },
            "board_binding": {"status": "virtual"},
        }
        anchors = {}
        for fpga, peer in (("fpga0", "fpga1"), ("fpga1", "fpga0")):
            records = []
            for direction in ("tx", "rx"):
                for lane in range(2):
                    records.append(
                        {
                            "id": f"link:{fpga}:{direction}:{lane}",
                            "link": "link",
                            "peer": peer,
                            "direction": direction,
                            "logical_lane": lane,
                            "binding_status": "unbound",
                        }
                    )
            anchors[fpga] = {
                "schema": "emuflow.virtual-io-anchors/v1",
                "platform": platform.name,
                "fpga": fpga,
                "part": "xcvu3p-ffvc1517-2-e",
                "anchors": records,
                "required_hardware_binding_fields": [
                    "package_pin",
                    "bank",
                    "iostandard",
                ],
            }
        return platform, release, phase6, anchors

    def test_requirements_seal_board_independent_boundary(self):
        platform, release, phase6, anchors = self._inputs()
        requirements = build_bsp_requirements(
            release, "b" * 64, phase6, platform, anchors
        )
        self.assertEqual(requirements["status"], "awaiting_hardware_bsp")
        self.assertEqual(
            requirements["release"]["gates_closed"],
            [f"G{index}" for index in range(10)],
        )
        self.assertEqual(
            requirements["metrics"],
            {
                "fpgas": 2,
                "links": 1,
                "logical_anchors": 8,
                "physical_data_lane_endpoints": 8,
                "fabric_clock_bindings": 2,
                "link_channel_bindings": 2,
                "bitstreams": 2,
                "pending_g10_checks": 5,
            },
        )

    def test_anchor_outside_boarddb_lane_is_rejected(self):
        platform, release, phase6, anchors = self._inputs()
        anchors["fpga0"]["anchors"][0]["logical_lane"] = 2
        with self.assertRaisesRegex(ValidationError, "legal BoardDB lane"):
            build_bsp_requirements(
                release, "b" * 64, phase6, platform, anchors
            )

    def test_missing_routed_checkpoint_is_rejected(self):
        platform, release, phase6, anchors = self._inputs()
        release["artifacts"].pop()
        with self.assertRaisesRegex(ValidationError, "routed checkpoint"):
            build_bsp_requirements(
                release, "b" * 64, phase6, platform, anchors
            )

    def test_phase8a_outputs_are_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            platform, release, phase6, anchors = self._inputs()
            paths = {
                "release": root / "release.json",
                "phase6": root / "phase6.json",
                "platform": root / "platform.json",
            }
            paths["release"].write_text(
                json.dumps(release), encoding="utf-8"
            )
            paths["phase6"].write_text(json.dumps(phase6), encoding="utf-8")
            paths["platform"].write_text(
                json.dumps(platform.to_dict()), encoding="utf-8"
            )
            anchor_paths = {}
            for fpga, document in anchors.items():
                path = root / f"{fpga}-anchors.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                anchor_paths[fpga] = path
            outputs = []
            for name in ("first", "second"):
                output = root / name
                report = run_phase8a(
                    paths["release"],
                    paths["phase6"],
                    paths["platform"],
                    anchor_paths,
                    output,
                )
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["g10_status"], "not_run")
                outputs.append(
                    (
                        (output / "bsp_requirements.json").read_bytes(),
                        (output / "phase8a_report.json").read_bytes(),
                    )
                )
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
