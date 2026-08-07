import copy
import shutil
import subprocess
import tempfile
import unittest
from collections import deque
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.mfspart import (
    _normalise_input,
    build_mfspart_hierarchy,
    validate_mfspart_hierarchy,
)


ROOT = Path(__file__).resolve().parents[1]


class MFSPartCoarsenerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a C++17 compiler is required")
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.executable = (
            Path(cls.temporary_directory.name) / "emuflow_mfspart_coarsener"
        )
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-Werror",
                str(ROOT / "src/native/mfspart_coarsener.cpp"),
                "-o",
                str(cls.executable),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    @staticmethod
    def _fixture():
        nodes = [
            {"id": "a", "weights": {"lut": 1, "ff": 1}},
            {"id": "b", "weights": {"lut": 1, "ff": 1}},
            {"id": "c", "weights": {"lut": 2, "ff": 1}},
            {"id": "d", "weights": {"lut": 2, "ff": 1}},
        ]
        # a-b has the highest Eq. 4 affinity.  The two c->a hyperedges become
        # one weighted hyperedge after transformation.
        nets = [
            {"id": "ab", "weight": 8.0, "source": "a", "sinks": ["b"]},
            {"id": "ac", "weight": 1.0, "source": "a", "sinks": ["c"]},
            {"id": "ca0", "weight": 2.0, "source": "c", "sinks": ["a"]},
            {"id": "ca1", "weight": 3.0, "source": "c", "sinks": ["a"]},
        ]
        return nodes, nets

    def _run(self, root: Path, *, seed: int = 13, bounds=None):
        nodes, nets = self._fixture()
        return build_mfspart_hierarchy(
            nodes,
            nets,
            ["lut", "ff"],
            bounds or {"lut": 4, "ff": 2},
            root,
            max_levels=1,
            seed=seed,
            executable=str(self.executable),
        )

    def test_affinity_hierarchy_is_lossless_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = self._run(root / "first")
            second = self._run(root / "second")
        self.assertEqual(first["validation"]["status"], "pass")
        self.assertEqual(first["fine_to_coarse"], second["fine_to_coarse"])
        self.assertEqual(first["merges"], second["merges"])
        self.assertEqual(first["merges"][0][0]["left"], 0)
        self.assertEqual(first["merges"][0][0]["right"], 1)
        self.assertAlmostEqual(first["merges"][0][0]["affinity"], 8.0)
        self.assertTrue(
            any(
                net["weight"] == 5.0
                for net in first["levels"][1]["nets"]
            )
        )

    def test_multiresource_bound_prevents_pair_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = self._run(
                Path(temporary_directory), bounds={"lut": 4, "ff": 1}
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(len(artifact["levels"]), 1)
        self.assertEqual(artifact["native_metrics"]["coarsest_nodes"], 4)

    def test_different_fixed_parts_never_merge(self) -> None:
        nodes, nets = self._fixture()
        nodes[0]["fixed_part"] = 0
        nodes[1]["fixed_part"] = 1
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut", "ff"],
                {"lut": 4, "ff": 2},
                Path(temporary_directory),
                max_levels=1,
                seed=13,
                executable=str(self.executable),
            )
        self.assertFalse(
            any(
                {merge["left"], merge["right"]} == {0, 1}
                for merge in artifact["merges"][0]
            )
        )

    def test_independent_oracle_rejects_corrupt_mapping(self) -> None:
        nodes, nets = self._fixture()
        native_input = _normalise_input(
            nodes,
            nets,
            ["lut", "ff"],
            {"lut": 4, "ff": 2},
            stop_delta=0,
            max_levels=1,
            seed=13,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = self._run(Path(temporary_directory))
        corrupt = copy.deepcopy(artifact)
        corrupt["fine_to_coarse"][0][0] = corrupt["fine_to_coarse"][0][2]
        with self.assertRaisesRegex(ValidationError, "matching mismatch"):
            validate_mfspart_hierarchy(corrupt, native_input)

    def test_native_rejects_duplicate_node_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "bad.in"
            output_path = root / "bad.out"
            input_path.write_text(
                "\n".join(
                    [
                        "EMUFLOW_MFSPART_COARSENER_INPUT_V2",
                        "PARAM 1 1 0 0 1 0",
                        "MODE A",
                        "BOUND 0 2",
                        "NODE 0 -1 1",
                        "NODE 0 -1 1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [str(self.executable), str(input_path), str(output_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("duplicate NODE", completed.stdout)

    @staticmethod
    def _path_fixture(length: int, fixed_parts=(0, 1)):
        nodes = [
            {"id": f"n{index}", "weights": {"lut": 1}, "fixed_part": -1}
            for index in range(length + 1)
        ]
        nodes[0]["fixed_part"] = fixed_parts[0]
        nodes[-1]["fixed_part"] = fixed_parts[1]
        nets = [
            {
                "id": f"e{index}",
                "source": f"n{index}",
                "sinks": [f"n{index + 1}"],
                "weight": 1.0,
            }
            for index in range(length)
        ]
        return nodes, nets

    def test_same_fpga_fixed_anchors_are_premerged(self) -> None:
        nodes, nets = self._path_fixture(3, fixed_parts=(0, 0))
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 8},
                Path(temporary_directory),
                max_levels=1,
                fixed_part_distances=[[0]],
                fixed_radius=0,
                fixed_margin=0,
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(artifact["fine_to_coarse"][0][0], artifact["fine_to_coarse"][0][3])
        self.assertEqual(
            artifact["fixed_merges"][0],
            [{"coarse": 0, "members": [0, 3]}],
        )

    def test_fixed_radius_protects_anchor_neighborhood(self) -> None:
        nodes, nets = self._path_fixture(4)
        nodes.extend(
            [
                {"id": "u", "weights": {"lut": 1}, "fixed_part": -1},
                {"id": "v", "weights": {"lut": 1}, "fixed_part": -1},
            ]
        )
        nets.append({"id": "uv", "source": "u", "sinks": ["v"], "weight": 1.0})
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 8},
                Path(temporary_directory),
                max_levels=1,
                fixed_part_distances=[[0, 1], [1, 0]],
                fixed_radius=1,
                fixed_margin=0,
                executable=str(self.executable),
            )
        protected = [node["protected_radius"] for node in artifact["levels"][0]["nodes"]]
        self.assertEqual(protected, [True, True, False, True, True, False, False])
        self.assertEqual(artifact["native_metrics"]["rejected_protected"], 4)
        self.assertEqual(len(artifact["merges"][0]), 1)

    def test_margin_rejects_single_distance_shortening(self) -> None:
        nodes, nets = self._path_fixture(5)
        nodes.extend(
            [
                {"id": "u", "weights": {"lut": 1}, "fixed_part": -1},
                {"id": "v", "weights": {"lut": 1}, "fixed_part": -1},
            ]
        )
        nets.append({"id": "uv", "source": "u", "sinks": ["v"], "weight": 1.0})
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 8},
                Path(temporary_directory),
                max_levels=1,
                fixed_part_distances=[[0, 2], [2, 0]],
                fixed_radius=0,
                fixed_margin=3,
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(artifact["native_metrics"]["rejected_margin"], 3)
        self.assertEqual(
            [
                {merge["left"], merge["right"]}
                for merge in artifact["merges"][0]
            ],
            [{6, 7}],
        )

    def test_cumulative_same_level_merges_preserve_margin(self) -> None:
        nodes, nets = self._path_fixture(7)
        for index, net in enumerate(nets):
            net["weight"] = 10.0 if index in (1, 3, 5) else 1.0
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 8},
                Path(temporary_directory),
                max_levels=1,
                fixed_part_distances=[[0, 2], [2, 0]],
                fixed_radius=0,
                fixed_margin=3,
                seed=17,
                executable=str(self.executable),
            )
        graph = artifact["levels"][-1]
        anchors = {
            node["fixed_part"]: index
            for index, node in enumerate(graph["nodes"])
            if node["fixed_part"] >= 0
        }
        adjacency = [[] for _ in graph["nodes"]]
        for net in graph["nets"]:
            for sink in net["sinks"]:
                adjacency[net["source"]].append(sink)
                adjacency[sink].append(net["source"])
        distances = [-1] * len(graph["nodes"])
        distances[anchors[0]] = 0
        queue = deque([anchors[0]])
        while queue:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if distances[neighbor] < 0:
                    distances[neighbor] = distances[node] + 1
                    queue.append(neighbor)
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertGreater(artifact["native_metrics"]["rejected_margin"], 0)
        self.assertGreaterEqual(distances[anchors[1]], 5)

    def test_preexisting_short_anchor_distance_does_not_freeze_other_components(self) -> None:
        nodes, nets = self._path_fixture(2)
        nodes.extend(
            [
                {"id": "u", "weights": {"lut": 1}, "fixed_part": -1},
                {"id": "v", "weights": {"lut": 1}, "fixed_part": -1},
            ]
        )
        nets.append({"id": "uv", "source": "u", "sinks": ["v"], "weight": 1.0})
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 8},
                Path(temporary_directory),
                max_levels=1,
                fixed_part_distances=[[0, 1], [1, 0]],
                fixed_radius=0,
                fixed_margin=3,
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(
            [{merge["left"], merge["right"]} for merge in artifact["merges"][0]],
            [{3, 4}],
        )

    def test_fixed_anchor_batch_margin_repair_is_linear_in_rounds(self) -> None:
        length = 2_000
        nodes, nets = self._path_fixture(length)
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": length + 1},
                Path(temporary_directory),
                max_levels=1,
                seed=31,
                fixed_part_distances=[[0, 1_994], [1_994, 0]],
                fixed_radius=0,
                fixed_margin=3,
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(artifact["native_metrics"]["margin_repair_rounds"], 1)
        self.assertEqual(
            artifact["native_metrics"]["margin_distance_searches"], 2
        )
        graph = artifact["levels"][1]
        anchors = {
            node["fixed_part"]: index
            for index, node in enumerate(graph["nodes"])
            if node["fixed_part"] >= 0
        }
        adjacency = [[] for _ in graph["nodes"]]
        for net in graph["nets"]:
            for sink in net["sinks"]:
                adjacency[net["source"]].append(sink)
                adjacency[sink].append(net["source"])
        distance = [-1] * len(graph["nodes"])
        distance[anchors[0]] = 0
        queue = deque([anchors[0]])
        while queue:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if distance[neighbor] < 0:
                    distance[neighbor] = distance[node] + 1
                    queue.append(neighbor)
        self.assertGreaterEqual(distance[anchors[1]], 1_997)
        self.assertLessEqual(len(artifact["merges"][0]), 3)

    def test_batch_margin_repair_handles_alternate_shortest_paths(self) -> None:
        nodes = [
            {"id": f"n{index}", "weights": {"lut": 1}, "fixed_part": -1}
            for index in range(10)
        ]
        nodes[0]["fixed_part"] = 0
        nodes[1]["fixed_part"] = 1
        paths = ([0, 2, 3, 4, 5, 1], [0, 6, 7, 8, 9, 1])
        nets = []
        for path_index, path in enumerate(paths):
            for edge_index, (source, sink) in enumerate(zip(path, path[1:])):
                nets.append(
                    {
                        "id": f"p{path_index}e{edge_index}",
                        "source": f"n{source}",
                        "sinks": [f"n{sink}"],
                        "weight": 1.0,
                    }
                )
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = build_mfspart_hierarchy(
                nodes,
                nets,
                ["lut"],
                {"lut": 10},
                Path(temporary_directory),
                max_levels=1,
                seed=5,
                fixed_part_distances=[[0, 1], [1, 0]],
                fixed_radius=0,
                fixed_margin=3,
                executable=str(self.executable),
            )
        self.assertEqual(artifact["validation"]["status"], "pass")
        self.assertEqual(artifact["native_metrics"]["margin_repair_rounds"], 1)
        self.assertEqual(
            artifact["native_metrics"]["margin_distance_searches"], 2
        )


if __name__ == "__main__":
    unittest.main()
