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
from emuflow.partition import (
    CUT_MODE_STATIC_EXACT,
    assign_clusters,
    build_clusters,
    build_partition_assignment,
    normalize_partition_constraints,
    validate_partition_artifacts,
)
from emuflow.platform import Platform
from emuflow.phase4 import run_phase4


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PATH = ROOT / "platforms" / "virtual" / "xcvu3p_2fpga_p2p.json"


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


def _wide_fanout_ir(width=32):
    instances = [
        {"id": "q0", "type": "FDRE", "resources": {"ff": 1}},
        {"id": "source_lut", "type": "LUT2", "resources": {"lut": 1}},
    ]
    nets = [
        {
            "id": "q",
            "name": "q",
            "cut_class": "register_output",
            "drivers": [_endpoint("q0", "Q")],
            "sinks": [_endpoint("source_lut", "I0")],
        }
    ]
    fanout_sinks = []
    for index in range(width):
        lut = f"sink_lut_{index:03d}"
        register = f"sink_ff_{index:03d}"
        instances.extend(
            [
                {"id": lut, "type": "LUT2", "resources": {"lut": 1}},
                {"id": register, "type": "FDRE", "resources": {"ff": 1}},
            ]
        )
        fanout_sinks.append(_endpoint(lut, "I0"))
        nets.append(
            {
                "id": f"d_{index:03d}",
                "name": f"d_{index:03d}",
                "cut_class": "register_input",
                "drivers": [_endpoint(lut, "O")],
                "sinks": [_endpoint(register, "D")],
            }
        )
    nets.append(
        {
            "id": "wide_boundary",
            "name": "wide_boundary",
            "cut_class": "combinational",
            "drivers": [_endpoint("source_lut", "O")],
            "sinks": fanout_sinks,
        }
    )
    return EmuIR(
        {
            "schema": "emuflow.emuir/v1",
            "design": {
                "name": "wide_fanout",
                "top": "wide_fanout",
                "source_format": "test",
            },
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


class StaticExactCombinationalCutPartitionTest(unittest.TestCase):
    def setUp(self):
        self.ir = _chain_ir()
        self.platform = Platform.load(PLATFORM_PATH)
        self.constraints = normalize_partition_constraints(
            None, self.ir, self.platform
        )

    def _exact_artifacts(self):
        clusters = build_clusters(
            self.ir,
            self.constraints,
            cut_mode=CUT_MODE_STATIC_EXACT,
            max_cross_fpga_dependency_depth=1,
            comb_segment_budget_slots=1,
            frame_slots=16,
        )
        cluster_for = {
            instance: cluster["id"]
            for cluster in clusters["clusters"]
            for instance in cluster["instances"]
        }
        assignment = build_partition_assignment(
            self.ir,
            self.platform,
            clusters,
            self.constraints,
            {
                cluster_for["q0"]: "fpga0",
                cluster_for["l0"]: "fpga0",
                cluster_for["l1"]: "fpga1",
                cluster_for["q1"]: "fpga1",
            },
            provider="test-static-exact-v1",
            seed=0,
        )
        return clusters, assignment

    def test_safe_default_is_identical_to_explicit_safe_mode(self):
        implicit = build_clusters(self.ir, self.constraints)
        explicit = build_clusters(
            self.ir, self.constraints, cut_mode="sequential-only"
        )
        self.assertEqual(implicit, explicit)
        self.assertNotIn("cut_mode", implicit["policy"])

    def test_depth_one_cut_has_independently_validated_contract(self):
        safe = build_clusters(self.ir, self.constraints)
        clusters, assignment = self._exact_artifacts()
        self.assertGreater(
            len(clusters["clusters"]), len(safe["clusters"])
        )
        combinational = [
            item
            for item in assignment["cut_nets"]
            if item["cut_class"] == "combinational"
        ]
        self.assertEqual([item["net"] for item in combinational], ["n0"])
        self.assertEqual(combinational[0]["predecessor_cut_nets"], [])
        self.assertEqual(combinational[0]["combinational_dependency_depth"], 1)
        contract = assignment["semantic_contract"]
        self.assertEqual(
            contract["qualification"],
            "partition-legality-only-provisional",
        )
        self.assertEqual(contract["metrics"]["combinational_cut_nets"], 1)
        self.assertTrue(contract["capture_requirements"])
        validation = validate_partition_artifacts(
            self.ir, self.platform, clusters, assignment
        )
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(
            validation["qualification"],
            "partition-legality-only-provisional",
        )

    def test_contract_tamper_is_rejected(self):
        clusters, assignment = self._exact_artifacts()
        tampered = copy.deepcopy(assignment)
        tampered["semantic_contract"]["commit_slot"] -= 1
        with self.assertRaisesRegex(ValidationError, "semantic_contract"):
            validate_partition_artifacts(
                self.ir, self.platform, clusters, tampered
            )

    def test_exact_cluster_policy_tamper_is_rejected(self):
        clusters, assignment = self._exact_artifacts()
        tampered = copy.deepcopy(clusters)
        tampered["policy"]["eligible_combinational_cut_nets"].append("n1")
        with self.assertRaisesRegex(ValidationError, "reconstruction"):
            validate_partition_artifacts(
                self.ir, self.platform, tampered, assignment
            )

    def test_depth_two_is_not_silently_enabled(self):
        with self.assertRaisesRegex(ValidationError, "currently support"):
            build_clusters(
                self.ir,
                self.constraints,
                cut_mode=CUT_MODE_STATIC_EXACT,
                max_cross_fpga_dependency_depth=2,
            )

    def test_wide_cone_splits_and_improves_checked_balance(self):
        ir = _wide_fanout_ir()
        constraints = normalize_partition_constraints(None, ir, self.platform)
        safe_clusters = build_clusters(ir, constraints)
        exact_clusters = build_clusters(
            ir,
            constraints,
            cut_mode=CUT_MODE_STATIC_EXACT,
            frame_slots=16,
        )
        self.assertEqual(
            max(len(item["instances"]) for item in safe_clusters["clusters"]),
            33,
        )
        self.assertEqual(
            max(len(item["instances"]) for item in exact_clusters["clusters"]),
            1,
        )
        safe_assignment = assign_clusters(
            ir, self.platform, safe_clusters, constraints, seed=9
        )
        exact_assignment = assign_clusters(
            ir, self.platform, exact_clusters, constraints, seed=9
        )
        safe_validation = validate_partition_artifacts(
            ir, self.platform, safe_clusters, safe_assignment
        )
        exact_validation = validate_partition_artifacts(
            ir, self.platform, exact_clusters, exact_assignment
        )
        self.assertLess(
            exact_validation["effective_balance_percent"],
            safe_validation["effective_balance_percent"],
        )
        self.assertGreater(
            exact_validation["combinational_cut_nets"], 0
        )

    def test_phase3_cli_emits_opt_in_provisional_qualification(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ir_path = root / "design.emuir.json"
            output = root / "phase3"
            ir_path.write_text(
                json.dumps(self.ir.to_dict()), encoding="utf-8"
            )
            with redirect_stdout(io.StringIO()):
                status = main(
                    [
                        "phase3",
                        "--ir",
                        str(ir_path),
                        "--platform",
                        str(PLATFORM_PATH),
                        "--provider",
                        "greedy",
                        "--cut-mode",
                        "static-exact-combinational",
                        "--max-cross-fpga-dependency-depth",
                        "1",
                        "--out",
                        str(output),
                    ]
                )
            self.assertEqual(status, 0)
            report = json.loads(
                (output / "phase3_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                report["qualification"],
                "partition-legality-only-provisional",
            )
            self.assertEqual(
                report["validation"]["cut_mode"],
                "static-exact-combinational",
            )

    def test_unqualified_exact_assignment_cannot_enter_phase4(self):
        _, assignment = self._exact_artifacts()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assignment_path = root / "assignment.json"
            assignment_path.write_text(
                json.dumps(assignment), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "stop after the Phase 3"):
                run_phase4(
                    assignment_path=assignment_path,
                    platform_path=PLATFORM_PATH,
                    output_dir=root / "phase4",
                )


if __name__ == "__main__":
    unittest.main()
