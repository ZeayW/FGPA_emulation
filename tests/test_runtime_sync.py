import copy
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.io import read_json
from emuflow.platform import Platform
from emuflow.runtime_sync import (
    build_runtime_sync_topology,
    validate_runtime_sync_provider,
    validate_runtime_sync_topology,
)


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "providers/runtime_sync_tree/provider.json"
PLATFORM = ROOT / "platforms/virtual/academic_vtr_4fpga_mesh.json"


class RuntimeSyncTest(unittest.TestCase):
    def test_source_visible_provider_and_tree_topology(self) -> None:
        provider_result = validate_runtime_sync_provider(
            read_json(PROVIDER), PROVIDER
        )
        self.assertEqual(provider_result["status"], "pass")
        self.assertEqual(provider_result["sources"], 1)
        platform = Platform.load(PLATFORM)
        topology = build_runtime_sync_topology(
            platform, provider_result["normalized"]
        )
        report = validate_runtime_sync_topology(
            topology, platform, provider_result["normalized"]
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["fpgas"], 4)
        self.assertEqual(topology["root"], "fpga0")
        self.assertEqual(
            {node["fpga"] for node in topology["nodes"]},
            {"fpga0", "fpga1", "fpga2", "fpga3"},
        )
        self.assertGreaterEqual(
            topology["parameters"]["start_margin_cycles"],
            topology["parameters"]["max_broadcast_latency_cycles"] + 2,
        )
        self.assertEqual(
            topology["hardware_release_status"],
            "blocked_on_board_sync_binding",
        )

    def test_disconnected_platform_is_rejected(self) -> None:
        provider = validate_runtime_sync_provider(
            read_json(PROVIDER), PROVIDER
        )["normalized"]
        raw = read_json(PLATFORM)
        raw["links"] = raw["links"][:1]
        platform = Platform.from_dict(raw)
        with self.assertRaisesRegex(ValidationError, "unreachable"):
            build_runtime_sync_topology(platform, provider)

    def test_source_digest_is_enforced(self) -> None:
        manifest = copy.deepcopy(read_json(PROVIDER))
        manifest["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "digest mismatch"):
            validate_runtime_sync_provider(manifest, PROVIDER)

    def test_topology_is_reproducible(self) -> None:
        provider = validate_runtime_sync_provider(
            read_json(PROVIDER), PROVIDER
        )["normalized"]
        platform = Platform.load(PLATFORM)
        topology = build_runtime_sync_topology(platform, provider)
        topology["parameters"]["start_margin_cycles"] += 1
        with self.assertRaisesRegex(ValidationError, "not reproducible"):
            validate_runtime_sync_topology(topology, platform, provider)


if __name__ == "__main__":
    unittest.main()
