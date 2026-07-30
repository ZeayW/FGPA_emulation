import copy
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.io import read_json
from emuflow.packed_netlist import (
    run_packed_netlist_import,
    validate_packed_netlist_contract,
    validate_packed_netlist_file,
)
from tests.native_build import vpr_packed_netlist_importer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/physical/vpr_packed_fixture.net"


class PackedNetlistTest(unittest.TestCase):
    def test_cpp_import_preserves_modes_hierarchy_and_cross_cluster_nets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "packed.json"
            report = run_packed_netlist_import(
                FIXTURE,
                output,
                executable=str(vpr_packed_netlist_importer()),
            )
            value = read_json(output)
            checked = validate_packed_netlist_file(output)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(checked["clusters"], 3)
        self.assertEqual(checked["block_types"], {"clb": 1, "io": 2})
        self.assertEqual(checked["cross_cluster_nets"], 2)
        self.assertEqual(checked["atoms"], 3)
        cluster = next(
            item for item in value["clusters"] if item["id"] == "clb[0]"
        )
        self.assertEqual(cluster["mode"], "default")
        self.assertEqual(cluster["atoms"], ["y"])
        self.assertEqual(
            [block["mode"] for block in cluster["pb_blocks"]],
            ["n1_lut6", "lut6", ""],
        )
        self.assertEqual(
            value["nets"],
            [
                {"id": "a", "driver": "io[0]", "sinks": ["clb[0]"]},
                {"id": "y", "driver": "clb[0]", "sinks": ["io[1]"]},
            ],
        )

    def test_validator_rejects_unknown_net_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "packed.json"
            run_packed_netlist_import(
                FIXTURE,
                output,
                executable=str(vpr_packed_netlist_importer()),
            )
            value = read_json(output)
        tampered = copy.deepcopy(value)
        tampered["nets"][0]["sinks"] = ["missing[0]"]
        with self.assertRaisesRegex(ValidationError, "sinks"):
            validate_packed_netlist_contract(tampered)

    def test_validator_checks_vpr_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "packed.json"
            run_packed_netlist_import(
                FIXTURE,
                output,
                executable=str(vpr_packed_netlist_importer()),
            )
            value = read_json(output)
        with self.assertRaisesRegex(ValidationError, "architecture_id"):
            validate_packed_netlist_contract(
                value,
                expected_architecture_sha256="2" * 64,
            )


if __name__ == "__main__":
    unittest.main()
