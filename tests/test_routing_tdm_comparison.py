import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import ValidationError
from emuflow.io import write_json
from emuflow.routing_tdm_comparison import (
    build_system_route_tdm_ab_comparison,
    build_system_route_tdm_scale_comparison,
    validate_system_route_tdm_ab_comparison,
    validate_system_route_tdm_scale_comparison,
)
from emuflow.tdm import TDM_ACADEMIC_SCHEDULE_PROVIDER, TDM_BASELINE_PROVIDER
from emuflow.timing_routing import GLOBAL_CANDIDATE_PROVIDER, ROUTE_TDM_PROVIDER


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _physical(value: float):
    return {
        "fpgas": [
            {
                "critical_path_ns": 10.0 - value,
                "stages": {"vpr_route": {"metrics": {"wirelength": 100 - int(value)}}},
                "physical_result": {
                    "timing": {
                        "wns_ns": value,
                        "tns_ns": min(0.0, value * 2.0),
                        "failing_endpoints": 1 if value < 0 else 0,
                        "failing_endpoint_constraints": 1 if value < 0 else 0,
                    },
                    "closure": {"unrouted_nets": 0, "drc_violations": 0},
                },
            }
        ]
    }


class RoutingTdmComparisonTest(unittest.TestCase):
    def test_builds_independently_replayed_scale_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = {}
            for name in ("assignment", "platform", "timing"):
                path = root / f"{name}.json"
                write_json(path, {"name": name})
                sources[name] = path
            arms = {}
            for label, route_provider, tdm_provider in (
                ("baseline", ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER),
                ("upgrade", GLOBAL_CANDIDATE_PROVIDER, TDM_ACADEMIC_SCHEDULE_PROVIDER),
            ):
                route_root = root / f"{label}-route"
                tdm_root = root / f"{label}-tdm"
                route_root.mkdir()
                tdm_root.mkdir()
                write_json(route_root / "routes.json", {"provider": route_provider})
                write_json(tdm_root / "schedule.json", {"provider": tdm_provider})
                arms[label] = (route_root, tdm_root)
            with (
                patch(
                    "emuflow.routing_tdm_comparison.validate_phase4",
                    return_value={"status": "pass", "routed_sinks": 17},
                ),
                patch(
                    "emuflow.routing_tdm_comparison.validate_phase5",
                    return_value={"status": "pass", "scheduled_bit_hops": 23},
                ),
            ):
                report = build_system_route_tdm_scale_comparison(
                    sources["assignment"], sources["platform"], sources["timing"],
                    *arms["baseline"], *arms["upgrade"], root / "scale.json",
                )
            self.assertEqual(report["validation"]["baseline_routed_sinks"], 17)
            broken = copy.deepcopy(report)
            broken["arms"]["upgrade"]["tdm_provider"] = "wrong"
            with self.assertRaises(ValidationError):
                validate_system_route_tdm_scale_comparison(broken)

    def _report(self, root: Path, route: str, tdm: str, value: float):
        artifacts = {}
        for key in ("emuir", "assignment", "timing_path_database", "partition_net_weights"):
            path = root / f"{key}.json"
            write_json(path, {"kind": key})
            artifacts[key] = {"path": path.name, "sha256": _sha256(path)}
        return {
            "status": "pass",
            "stages": {
                "frontend": {"design": "d", "platform": "p"},
                "partition": {"provider": "partition", "design": "d", "platform": "p"},
                "system_route": {
                    "provider": route,
                    "design": "d",
                    "platform": "p",
                    "validation": {
                        "demands": 1,
                        "routed_sinks": 1,
                        "tree_edges": 1,
                        "total_link_bit_hops": 1,
                        "max_link_utilization": 0.5,
                        "overloaded_links": 0,
                        "estimated_worst_tdm_slack_ns": value,
                    },
                },
                "tdm": {
                    "provider": tdm,
                    "design": "d",
                    "platform": "p",
                    "validation": {
                        "frame_slots": 8,
                        "completion_slot": 1,
                        "max_domain_utilization": 0.5,
                        "scheduled_bit_hops": 1,
                        "collisions": 0,
                    },
                },
                "split": {
                    "provider": "deterministic-cut-shadow-split-v1",
                    "design": "d",
                    "platform": "p",
                },
            },
            "artifacts": artifacts,
            "physical": _physical(value),
        }

    def test_builds_source_bound_complete_phase7_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            upgrade = root / "upgrade"
            baseline.mkdir()
            upgrade.mkdir()
            base_report = self._report(baseline, ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER, -1.0)
            up_report = self._report(upgrade, GLOBAL_CANDIDATE_PROVIDER, TDM_ACADEMIC_SCHEDULE_PROVIDER, -0.25)
            write_json(baseline / "multi-fpga-flow-report.json", base_report)
            write_json(upgrade / "multi-fpga-flow-report.json", up_report)
            output = root / "comparison.json"
            with patch(
                "emuflow.routing_tdm_comparison.validate_multi_fpga_flow_report",
                return_value={"status": "pass"},
            ):
                result = build_system_route_tdm_ab_comparison(baseline, upgrade, output)
            self.assertEqual(result["validation"]["wns_improvement_ns"], 0.75)
            self.assertEqual(result["validation"]["tns_improvement_ns"], 1.5)
            self.assertTrue(output.is_file())
            tampered = copy.deepcopy(result)
            tampered["physical_delta_upgrade_minus_baseline"]["worst_wns_ns"] = 99
            with self.assertRaises(ValidationError):
                validate_system_route_tdm_ab_comparison(tampered)

    def test_rejects_mixed_upstream(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            upgrade = root / "upgrade"
            baseline.mkdir()
            upgrade.mkdir()
            base_report = self._report(baseline, ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER, -1.0)
            up_report = self._report(upgrade, GLOBAL_CANDIDATE_PROVIDER, TDM_ACADEMIC_SCHEDULE_PROVIDER, -0.25)
            (upgrade / "assignment.json").write_text("other\n", encoding="utf-8")
            up_report["artifacts"]["assignment"]["sha256"] = _sha256(upgrade / "assignment.json")
            write_json(baseline / "multi-fpga-flow-report.json", base_report)
            write_json(upgrade / "multi-fpga-flow-report.json", up_report)
            with patch(
                "emuflow.routing_tdm_comparison.validate_multi_fpga_flow_report",
                return_value={"status": "pass"},
            ):
                with self.assertRaises(ValidationError):
                    build_system_route_tdm_ab_comparison(baseline, upgrade, root / "comparison.json")

    def test_normalizes_only_each_flow_root_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline"
            upgrade = root / "upgrade"
            baseline.mkdir()
            upgrade.mkdir()
            base_report = self._report(
                baseline, ROUTE_TDM_PROVIDER, TDM_BASELINE_PROVIDER, -1.0
            )
            up_report = self._report(
                upgrade,
                GLOBAL_CANDIDATE_PROVIDER,
                TDM_ACADEMIC_SCHEDULE_PROVIDER,
                -0.25,
            )
            for flow_root, report in ((baseline, base_report), (upgrade, up_report)):
                emuir = flow_root / "emuir.json"
                write_json(
                    emuir,
                    {"source": str(flow_root / "frontend/phase1/frontend.json")},
                )
                report["artifacts"]["emuir"]["sha256"] = _sha256(emuir)
                write_json(flow_root / "multi-fpga-flow-report.json", report)
            with patch(
                "emuflow.routing_tdm_comparison.validate_multi_fpga_flow_report",
                return_value={"status": "pass"},
            ):
                result = build_system_route_tdm_ab_comparison(
                    baseline, upgrade, root / "comparison.json"
                )
            self.assertEqual(result["validation"]["status"], "pass")
            write_json(upgrade / "emuir.json", {"source": "/unrelated/input.json"})
            up_report["artifacts"]["emuir"]["sha256"] = _sha256(
                upgrade / "emuir.json"
            )
            write_json(upgrade / "multi-fpga-flow-report.json", up_report)
            with patch(
                "emuflow.routing_tdm_comparison.validate_multi_fpga_flow_report",
                return_value={"status": "pass"},
            ):
                with self.assertRaisesRegex(ValidationError, "emuir.*differs"):
                    build_system_route_tdm_ab_comparison(
                        baseline, upgrade, root / "second.json"
                    )


if __name__ == "__main__":
    unittest.main()
