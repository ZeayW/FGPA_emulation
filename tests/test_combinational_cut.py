import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from emuflow.cli import main
from emuflow.combinational_cut import (
    characterize_combinational_cuts,
    validate_combinational_cut_characterization,
)
from emuflow.errors import ValidationError
from emuflow.ir import EmuIR


def _endpoint(instance, port):
    return {"instance": instance, "port": port, "bit": 0}


def _chain_ir():
    instances = [
        {"id": "q0", "type": "FDRE", "resources": {"ff": 1}},
        {"id": "l0", "type": "LUT2", "resources": {"lut": 1}},
        {"id": "l1", "type": "LUT2", "resources": {"lut": 1}},
        {"id": "l2", "type": "LUT2", "resources": {"lut": 1}},
        {"id": "q1", "type": "FDRE", "resources": {"ff": 1}},
    ]
    nets = [
        {
            "id": "q",
            "name": "q",
            "cut_class": "register_output",
            "drivers": [_endpoint("q0", "Q")],
            "sinks": [_endpoint("l0", "I0")],
        },
        {
            "id": "n0",
            "name": "n0",
            "cut_class": "combinational",
            "drivers": [_endpoint("l0", "O")],
            "sinks": [_endpoint("l1", "I0")],
        },
        {
            "id": "n1",
            "name": "n1",
            "cut_class": "combinational",
            "drivers": [_endpoint("l1", "O")],
            "sinks": [_endpoint("l2", "I0")],
        },
        {
            "id": "d",
            "name": "d",
            "cut_class": "register_input",
            "drivers": [_endpoint("l2", "O")],
            "sinks": [_endpoint("q1", "D")],
        },
    ]
    return EmuIR(
        {
            "schema": "emuflow.emuir/v1",
            "design": {"name": "cut_chain", "top": "cut_chain", "source_format": "test"},
            "ports": [],
            "instances": instances,
            "nets": nets,
            "clocks": [],
            "warnings": [],
        }
    )


class CombinationalCutCharacterizationTest(unittest.TestCase):
    def test_chain_has_stable_dependency_depth_and_split_upper_bounds(self):
        ir = _chain_ir()
        report = characterize_combinational_cuts(ir)
        self.assertFalse(report["behavior_change"])
        cuts = {item["net"]: item for item in report["eligible_cuts"]}
        self.assertEqual(cuts["n0"]["dependency_level"], 1)
        self.assertEqual(cuts["n0"]["predecessor_cut_nets"], [])
        self.assertEqual(cuts["n1"]["dependency_level"], 2)
        self.assertEqual(cuts["n1"]["predecessor_cut_nets"], ["n0"])
        self.assertEqual(
            report["current_sequential_only_atomic_components"]["maximum_instances"],
            3,
        )
        by_limit = {
            item["max_dependency_depth"]: item for item in report["depth_limits"]
        }
        self.assertEqual(by_limit[1]["atomic_components"]["maximum_instances"], 2)
        self.assertEqual(by_limit[2]["atomic_components"]["maximum_instances"], 1)
        self.assertEqual(
            validate_combinational_cut_characterization(ir, report)["status"],
            "pass",
        )

    def test_cycle_is_fail_closed_and_stays_atomic(self):
        value = _chain_ir().to_dict()
        value["nets"][2]["sinks"].append(_endpoint("l0", "I1"))
        ir = EmuIR(value)
        report = characterize_combinational_cuts(ir)
        self.assertEqual(report["metrics"]["cyclic_combinational_sccs"], 1)
        self.assertEqual(report["eligible_cuts"], [])
        reasons = {
            item["net"]: item["reasons"]
            for item in report["ineligible_combinational_cuts"]
        }
        self.assertIn("driver-in-combinational-cycle", reasons["n0"])
        self.assertIn("sink-in-combinational-cycle", reasons["n1"])

    def test_opaque_driver_is_not_eligible(self):
        value = _chain_ir().to_dict()
        instance = next(item for item in value["instances"] if item["id"] == "l0")
        instance["type"] = "VTR_MULTIPLY"
        instance["resources"] = {"dsp": 1}
        report = characterize_combinational_cuts(EmuIR(value))
        reasons = {
            item["net"]: item["reasons"]
            for item in report["ineligible_combinational_cuts"]
        }
        self.assertIn("driver-not-supported-soft-logic", reasons["n0"])

    def test_tampered_report_is_rejected(self):
        ir = _chain_ir()
        report = characterize_combinational_cuts(ir)
        tampered = copy.deepcopy(report)
        tampered["eligible_cuts"][1]["dependency_level"] = 1
        with self.assertRaisesRegex(ValidationError, "independent EmuIR"):
            validate_combinational_cut_characterization(ir, tampered)

    def test_invalid_depth_limit_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "subset of"):
            characterize_combinational_cuts(_chain_ir(), (3,))

    def test_cli_writes_and_independently_validates_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "design.emuir.json"
            report_path = root / "characterization.json"
            ir_path.write_text(
                json.dumps(_chain_ir().to_dict()), encoding="utf-8"
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "combinational-cut",
                            "characterize",
                            "--ir",
                            str(ir_path),
                            "--depth-limit",
                            "1",
                            "--depth-limit",
                            "2",
                            "--output",
                            str(report_path),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "combinational-cut",
                            "validate",
                            "--ir",
                            str(ir_path),
                            str(report_path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
