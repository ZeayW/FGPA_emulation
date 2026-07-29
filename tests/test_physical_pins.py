import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.physical_pins import (
    PACKAGE_PIN_PROVIDER,
    binding_to_xdc,
    build_package_pin_binding,
    validate_hardware_bsp,
    validate_package_pin_binding,
)
from emuflow.io import read_json
from emuflow.pin_planning import build_pin_plan
from emuflow.platform import Platform


ROOT = Path(__file__).resolve().parents[1]


class PhysicalPinBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(cls.temporary_directory.name)
        cls.pin_planner = root / "emuflow_pin_planner"
        cls.bsp_solver = root / "emuflow_bsp_pin_solver"
        for source, output in (
            ("placement_aware_pin_planner.cpp", cls.pin_planner),
            ("bsp_pin_solver.cpp", cls.bsp_solver),
        ):
            subprocess.run(
                [
                    "c++",
                    "-std=c++17",
                    "-O2",
                    str(ROOT / "src/native" / source),
                    "-o",
                    str(output),
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.platform = Platform.from_dict(
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "physical_pin_fixture",
                    "kind": "virtual",
                },
                "fpgas": [
                    {
                        "id": fpga,
                        "part": "xcvu9p-flga2104-2L-e",
                        "utilization_limit": 0.75,
                        "capacity": {"lut": 100, "ff": 100},
                    }
                    for fpga in ("fpga0", "fpga1")
                ],
                "links": [
                    {
                        "id": "link0",
                        "endpoints": ["fpga0", "fpga1"],
                        "direction": "full_duplex",
                        "mode": "source_synchronous",
                        "data_lanes_per_direction": 2,
                        "fabric_clock_mhz": 250,
                        "latency_cycles": 2,
                    }
                ],
            }
        )
        self.schedule = {
            "design": "physical_pin_fixture",
            "platform": self.platform.name,
            "entries": [
                {
                    "id": f"entry{index}",
                    "link": "link0",
                    "from": "fpga0",
                    "to": "fpga1",
                    "tdm_ratio": 2,
                    "slot": index % 2,
                    "lane": index // 2,
                }
                for index in range(4)
            ],
        }
        self.positions = {
            "schema": "emuflow.signal-position-hints/v1",
            "design": self.schedule["design"],
            "platform": self.platform.name,
            "provider": "openparf-lookahead-centroid-v1",
            "region_count": 3,
            "metrics": {
                "signals": 4,
                "endpoint_centroid_fallbacks": 0,
            },
            "entries": [
                {
                    "schedule_entry": f"entry{index}",
                    "source_y": 0.1 if index < 2 else 0.9,
                    "sink_y": 0.15 if index < 2 else 0.85,
                    "source_region": 0 if index < 2 else 2,
                    "sink_region": 0 if index < 2 else 2,
                    "source_fallback": False,
                    "sink_fallback": False,
                }
                for index in range(4)
            ],
        }
        self.plan = build_pin_plan(
            self.schedule,
            self.platform,
            self.positions,
            executable=str(self.pin_planner),
            refinement_iterations=8,
        )
        self.anchors = {
            fpga: {
                "schema": "emuflow.virtual-io-anchors/v1",
                "platform": self.platform.name,
                "fpga": fpga,
                "part": "xcvu9p-flga2104-2L-e",
                "anchors": [],
                "required_hardware_binding_fields": [
                    "package_pin",
                    "bank",
                    "iostandard",
                ],
            }
            for fpga in ("fpga0", "fpga1")
        }
        by_group = {}
        for item in self.plan["entries"]:
            by_group.setdefault(item["group"], item["physical_lane"])
        for lane in sorted(by_group.values()):
            self.anchors["fpga0"]["anchors"].append(
                {
                    "id": f"link0:fpga0:tx:{lane}",
                    "link": "link0",
                    "peer": "fpga1",
                    "direction": "tx",
                    "logical_lane": lane,
                    "binding_status": "unbound",
                }
            )
            self.anchors["fpga1"]["anchors"].append(
                {
                    "id": f"link0:fpga1:rx:{lane}",
                    "link": "link0",
                    "peer": "fpga0",
                    "direction": "rx",
                    "logical_lane": lane,
                    "binding_status": "unbound",
                }
            )
        self.bsp = self._bsp()

    def _bsp(self, iostandard: str = "LVCMOS18"):
        voltage = {
            "LVCMOS12": 1.2,
            "LVCMOS18": 1.8,
        }[iostandard]
        fpgas = []
        for fpga, direction in (("fpga0", "tx"), ("fpga1", "rx")):
            pins = []
            for index, region_y in enumerate((0.9, 0.1)):
                pins.append(
                    {
                        "id": f"{fpga}:pin{index}",
                        "fpga": fpga,
                        "package_pin": f"{fpga.upper()}_P{index}",
                        "bank": "BANK0",
                        "connector": "J0",
                        "connector_pin": index,
                        "directions": [direction],
                        "iostandards": [iostandard],
                        "region_y": region_y,
                        "clock_capable": False,
                        "reserved": False,
                    }
                )
            fpgas.append(
                {
                    "id": fpga,
                    "part": "xcvu9p-flga2104-2L-e",
                    "banks": [
                        {
                            "id": "BANK0",
                            "voltage": voltage,
                            "iostandards": [iostandard],
                            "max_pins": 2,
                        }
                    ],
                    "pins": pins,
                }
            )
        return {
            "schema": "emuflow.hardware-bsp/v1",
            "platform": self.platform.name,
            "board": {
                "name": "synthetic_fixture",
                "revision": "1",
                "qualification": "synthetic_validation",
            },
            "fpgas": fpgas,
            "channels": [
                {
                    "id": f"channel{index}",
                    "link": "link0",
                    "source": "fpga0",
                    "sink": "fpga1",
                    "source_pin": f"fpga0:pin{index}",
                    "sink_pin": f"fpga1:pin{index}",
                    "iostandard": iostandard,
                    "max_frequency_mhz": 300,
                    "skew_ps": index,
                }
                for index in range(2)
            ],
        }

    def _build(self):
        return build_package_pin_binding(
            self.schedule,
            self.platform,
            self.positions,
            self.plan,
            self.anchors,
            self.bsp,
            executable=str(self.bsp_solver),
        )

    def test_exact_binding_is_reproducible_and_placement_aware(self) -> None:
        first = self._build()
        second = self._build()
        self.assertEqual(first, second)
        self.assertEqual(first["provider"], PACKAGE_PIN_PROVIDER)
        self.assertEqual(first["status"], "synthetic_validation")
        validation = validate_package_pin_binding(
            self.schedule,
            self.platform,
            self.positions,
            self.plan,
            self.anchors,
            self.bsp,
            first,
        )
        self.assertEqual(validation["status"], "pass")
        low = min(first["entries"], key=lambda item: item["source_y"])
        high = max(first["entries"], key=lambda item: item["source_y"])
        self.assertEqual(low["channel"], "channel1")
        self.assertEqual(high["channel"], "channel0")
        xdc = binding_to_xdc(first, "fpga0")
        self.assertIn("SYNTHETIC VALIDATION BSP", xdc)
        self.assertIn("get_ports {tx_link0_fpga1[", xdc)

    def test_binding_corruption_is_rejected(self) -> None:
        binding = self._build()
        broken = copy.deepcopy(binding)
        broken["entries"][0]["source_package_pin"] = "WRONG"
        with self.assertRaisesRegex(ValidationError, "independently agree"):
            validate_package_pin_binding(
                self.schedule,
                self.platform,
                self.positions,
                self.plan,
                self.anchors,
                self.bsp,
                broken,
            )

    def test_electrically_incompatible_bsp_is_infeasible(self) -> None:
        incompatible = self._bsp("LVCMOS12")
        with self.assertRaisesRegex(EmuFlowError, "no complete electrically"):
            build_package_pin_binding(
                self.schedule,
                self.platform,
                self.positions,
                self.plan,
                self.anchors,
                incompatible,
                executable=str(self.bsp_solver),
                iostandard="LVCMOS18",
            )

    def test_checked_in_synthetic_bsp_is_reproducible(self) -> None:
        platform_path = (
            ROOT / "platforms/virtual/xcvu9p_4fpga_mesh.json"
        )
        checked = (
            ROOT
            / "platforms/synthetic/xcvu9p_4fpga_mesh_bsp.json"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            generated = Path(temporary_directory) / "bsp.json"
            subprocess.run(
                [
                    "python3",
                    str(
                        ROOT
                        / "scripts/generate_synthetic_vu9p_bsp.py"
                    ),
                    str(platform_path),
                    str(generated),
                ],
                check=True,
            )
            self.assertEqual(checked.read_bytes(), generated.read_bytes())
        validation = validate_hardware_bsp(
            read_json(checked), Platform.load(platform_path)
        )
        self.assertEqual(validation["pins"], 512)
        self.assertEqual(validation["channels"], 256)
        self.assertEqual(validation["domains"], 8)


if __name__ == "__main__":
    unittest.main()
