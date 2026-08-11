from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from emuflow.chimew_pipeline import CHIMEW_SOURCE_BOUND_PIPELINE_PROVIDER
from emuflow.contest_eda2023_chimew import (
    EDA2023_CONTEST_CHIMEW_AB_SCHEMA,
    EDA2023_CONTEST_CHIMEW_QUALIFICATION,
    materialize_eda2023_contest_chimew_inputs,
    run_eda2023_contest_chimew_ab,
)
from emuflow.errors import ValidationError
from emuflow.io import read_json, write_json


ROOT = Path(__file__).resolve().parents[1]


class Eda2023ContestChimewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("c++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("a C++17 compiler is required")
        cls.native_root = tempfile.TemporaryDirectory()
        cls.executables = {}
        for source, label in (
            ("chimew_signal_grouper.cpp", "grouper"),
            ("chimew_position_refiner.cpp", "refiner"),
            ("chimew_rudy.cpp", "rudy"),
            ("chimew_bank_channel_assigner.cpp", "assigner"),
            ("placement_aware_pin_planner.cpp", "pin_planner"),
        ):
            executable = Path(cls.native_root.name) / label
            subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-O2",
                    str(ROOT / "src/native" / source),
                    "-o",
                    str(executable),
                ],
                check=True,
            )
            cls.executables[label] = str(executable)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.native_root.cleanup()

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        imported = root / "imported"
        imported.mkdir(parents=True)
        dies = [f"Die{index}" for index in range(4)]
        links = [
            {
                "id": f"die_link_{index:03d}_{index + 1:03d}",
                "endpoints": [dies[index], dies[index + 1]],
                "capacity": 16,
                "kind": "sll",
            }
            for index in range(3)
        ]
        nets = [
            {
                "id": index,
                "source_node": f"g{2 * index}",
                "source_die": dies[index % 3],
                "sink_nodes": [f"g{2 * index + 1}"],
                "sink_dies": [dies[index % 3 + 1]],
            }
            for index in range(8)
        ]
        write_json(
            imported / "contest_instance.json",
            {
                "schema": "emuflow.contest-eda2023-instance/v1",
                "name": "eda2023-chimew-fixture",
                "fpgas": ["FPGA0"],
                "dies": dies,
                "die_to_fpga": {die: "FPGA0" for die in dies},
                "links": links,
                "nets": nets,
                "node_positions": {
                    node: die
                    for net in nets
                    for node, die in [
                        (net["source_node"], net["source_die"]),
                        (net["sink_nodes"][0], net["sink_dies"][0]),
                    ]
                },
                "parameters": {},
                "source": {},
            },
        )
        write_json(
            imported / "die_hierarchy.json",
            {
                "schema": "emuflow.die-hierarchy/v1",
                "platform": "eda2023-chimew-fixture",
                "physical_fpgas": [{"id": "FPGA0", "dies": dies}],
                "links": [
                    {"id": link["id"], "capacity": 16, "kind": "sll"}
                    for link in links
                ],
            },
        )
        write_json(
            imported / "boarddb.json",
            {
                "schema": "emuflow.boarddb/v1",
                "platform": {
                    "name": "eda2023-chimew-fixture",
                    "kind": "virtual",
                    "description": "test contest die graph",
                },
                "fpgas": [
                    {
                        "id": die,
                        "part": "academic-contest-die",
                        "utilization_limit": 1.0,
                        "capacity": {"lut": 1},
                    }
                    for die in dies
                ],
                "links": [
                    {
                        "id": link["id"],
                        "endpoints": link["endpoints"],
                        "direction": "full_duplex",
                        "mode": "abstract",
                        "data_lanes_per_direction": 16,
                        "fabric_clock_mhz": 1000.0,
                        "latency_cycles": 0,
                    }
                    for link in links
                ],
            },
        )
        routes = root / "routes.json"
        write_json(
            routes,
            {
                "schema": "emuflow.system-routes/v1",
                "design": "eda2023-chimew-fixture",
                "platform": "eda2023-chimew-fixture",
                "constraints": {},
                "routes": [],
            },
        )
        tdm = root / "tdm_plan.json"
        write_json(
            tdm,
            {
                "schema": "emuflow.contest-eda2023-tdm/v1",
                "instance": "eda2023-chimew-fixture",
                "provider": "cpp-lagrangian-kkt-direction-separated-v1",
                "hops": [
                    {
                        "index": index,
                        "net": f"net_{index:07d}",
                        "official_net_id": index,
                        "link": links[index % 3]["id"],
                        "from": links[index % 3]["endpoints"][index % 2],
                        "to": links[index % 3]["endpoints"][1 - index % 2],
                        "direction": index % 2,
                        "lane": index % 4,
                        "ratio": 2,
                        "continuous_ratio": 2.0,
                    }
                    for index in range(8)
                ],
            },
        )
        return imported, routes, tdm

    def test_source_bound_contest_ab_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            imported, routes, tdm = self._fixture(root)
            report = run_eda2023_contest_chimew_ab(
                import_dir=imported,
                routes_path=routes,
                tdm_plan_path=tdm,
                output_dir=root / "ab",
                **self.executables,
            )
            self.assertEqual(report["schema"], EDA2023_CONTEST_CHIMEW_AB_SCHEMA)
            self.assertEqual(
                report["qualification"], EDA2023_CONTEST_CHIMEW_QUALIFICATION
            )
            self.assertEqual(report["metrics"]["signals"], 8)
            self.assertEqual(
                read_json(root / "ab/chimew/pipeline_report.json")["provider"],
                CHIMEW_SOURCE_BOUND_PIPELINE_PROVIDER,
            )
            self.assertIn("synthetic package pins", report["claim_boundary"])
            self.assertTrue((root / "ab/baseline_pin_plan.json").is_file())

    def test_invalid_tdm_link_is_rejected_before_native_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            imported, routes, tdm = self._fixture(root)
            document = read_json(tdm)
            document["hops"][0]["link"] = "missing"
            write_json(tdm, document)
            with self.assertRaisesRegex(ValidationError, "TDM hop is invalid"):
                materialize_eda2023_contest_chimew_inputs(
                    import_dir=imported,
                    routes_path=routes,
                    tdm_plan_path=tdm,
                    output_dir=root / "materialized",
                    grouper=self.executables["grouper"],
                    refiner=self.executables["refiner"],
                )


if __name__ == "__main__":
    unittest.main()
