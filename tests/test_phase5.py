import copy
import json
import tempfile
import unittest
from pathlib import Path

from emuflow.errors import ValidationError
from emuflow.partition import PARTITION_ASSIGNMENT_SCHEMA
from emuflow.phase5 import run_phase5
from emuflow.platform import Platform
from emuflow.routing import normalize_route_constraints, route_system
from emuflow.tdm import (
    build_tdm_schedule,
    schedule_to_systemverilog_testbench,
    simulate_tdm_schedule,
    validate_tdm_schedule,
)


ROOT = Path(__file__).resolve().parents[1]


def _platform_value(name, fpga_ids, links):
    return {
        "schema": "emuflow.boarddb/v1",
        "platform": {
            "name": name,
            "kind": "virtual",
            "description": "Phase 5 test topology",
        },
        "fpgas": [
            {
                "id": fpga_id,
                "part": "xcvu3p-ffvc1517-2-e",
                "utilization_limit": 1.0,
                "capacity": {"lut": 100, "ff": 100},
            }
            for fpga_id in fpga_ids
        ],
        "links": links,
    }


def _link(link_id, left, right, lanes=1, latency=1, direction="full_duplex"):
    return {
        "id": link_id,
        "endpoints": [left, right],
        "direction": direction,
        "mode": "abstract",
        "data_lanes_per_direction": lanes,
        "fabric_clock_mhz": 250.0,
        "latency_cycles": latency,
    }


def _assignment(platform, cuts):
    return {
        "schema": PARTITION_ASSIGNMENT_SCHEMA,
        "design": "tdm_test",
        "platform": platform.name,
        "cut_nets": [
            {
                "net": net,
                "cut_class": "register_output",
                "source_fpgas": [source],
                "sink_fpgas": list(sinks),
                "sink_endpoints": len(sinks),
            }
            for net, source, sinks in cuts
        ],
    }


def _routes(platform, cuts, frame_slots):
    assignment = _assignment(platform, cuts)
    constraints = normalize_route_constraints(
        {
            "schema": "emuflow.system-route-constraints/v1",
            "frame_slots": frame_slots,
        },
        platform,
    )
    return route_system(assignment, platform, constraints)


class Phase5Test(unittest.TestCase):
    def test_schedule_validate_simulate_and_write_artifacts(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "two",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=4, latency=2)],
            )
        )
        routes = _routes(
            platform,
            [
                ("n0", "a", ["b"]),
                ("n1", "a", ["b"]),
                ("n2", "b", ["a"]),
            ],
            frame_slots=8,
        )
        schedule = build_tdm_schedule(routes, platform)
        validation = validate_tdm_schedule(routes, platform, schedule)
        simulation = simulate_tdm_schedule(routes, schedule, frames=9)
        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["scheduled_bit_hops"], 3)
        self.assertEqual(validation["collisions"], 0)
        self.assertEqual(simulation["delivered_sink_values"], 27)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            routes_path = root / "routes.json"
            platform_path = root / "platform.json"
            routes_path.write_text(json.dumps(routes), encoding="utf-8")
            platform_path.write_text(
                json.dumps(platform.to_dict()), encoding="utf-8"
            )
            report = run_phase5(
                routes_path=routes_path,
                platform_path=platform_path,
                output_dir=root / "phase5",
                simulation_frames=9,
            )
            self.assertEqual(report["status"], "pass")
            for filename in (
                "schedule.json",
                "schedule.tsv",
                "transport_manifest.json",
                "transport_schedule_tb.sv",
                "phase5_report.json",
            ):
                self.assertTrue((root / "phase5" / filename).is_file())

    def test_multihop_precedence_includes_store_and_forward_cycle(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "line",
                ["a", "b", "c"],
                [
                    _link("ab", "a", "b", lanes=2, latency=1),
                    _link("bc", "b", "c", lanes=2, latency=1),
                ],
            )
        )
        routes = _routes(
            platform,
            [("n0", "a", ["c"])],
            frame_slots=8,
        )
        schedule = build_tdm_schedule(routes, platform)
        validate_tdm_schedule(routes, platform, schedule)
        entries = sorted(schedule["entries"], key=lambda entry: entry["hop"])
        self.assertEqual(entries[0]["slot"], 0)
        self.assertEqual(entries[0]["arrival_slot"], 1)
        self.assertEqual(entries[1]["ready_slot"], 2)
        self.assertEqual(entries[1]["slot"], 2)
        self.assertEqual(entries[1]["arrival_slot"], 3)

    def test_latency_can_make_route_capacity_schedule_infeasible(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "tight",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=1, latency=1)],
            )
        )
        routes = _routes(
            platform,
            [("n0", "a", ["b"]), ("n1", "a", ["b"])],
            frame_slots=2,
        )
        with self.assertRaisesRegex(ValidationError, "infeasible"):
            build_tdm_schedule(routes, platform)

    def test_half_duplex_opposing_directions_do_not_collide(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "half",
                ["a", "b"],
                [
                    _link(
                        "ab",
                        "a",
                        "b",
                        lanes=1,
                        latency=1,
                        direction="half_duplex",
                    )
                ],
            )
        )
        routes = _routes(
            platform,
            [("n0", "a", ["b"]), ("n1", "b", ["a"])],
            frame_slots=4,
        )
        schedule = build_tdm_schedule(routes, platform)
        validation = validate_tdm_schedule(routes, platform, schedule)
        self.assertEqual(validation["collisions"], 0)
        slots = sorted(entry["slot"] for entry in schedule["entries"])
        self.assertEqual(slots, [0, 1])

    def test_collision_is_rejected(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "collision",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=2, latency=1)],
            )
        )
        routes = _routes(
            platform,
            [("n0", "a", ["b"]), ("n1", "a", ["b"])],
            frame_slots=4,
        )
        schedule = build_tdm_schedule(routes, platform)
        broken = copy.deepcopy(schedule)
        broken["entries"][1]["slot"] = broken["entries"][0]["slot"]
        broken["entries"][1]["lane"] = broken["entries"][0]["lane"]
        with self.assertRaisesRegex(ValidationError, "collision"):
            validate_tdm_schedule(routes, platform, broken)

    def test_precedence_violation_is_rejected(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "precedence",
                ["a", "b", "c"],
                [
                    _link("ab", "a", "b", lanes=2, latency=1),
                    _link("bc", "b", "c", lanes=2, latency=1),
                ],
            )
        )
        routes = _routes(platform, [("n0", "a", ["c"])], frame_slots=8)
        schedule = build_tdm_schedule(routes, platform)
        broken = copy.deepcopy(schedule)
        child = next(entry for entry in broken["entries"] if entry["hop"] == 1)
        child["ready_slot"] -= 1
        child["slot"] -= 1
        child["arrival_slot"] -= 1
        with self.assertRaisesRegex(ValidationError, "ready-slot mismatch"):
            validate_tdm_schedule(routes, platform, broken)

    def test_generated_testbench_contains_real_schedule(self) -> None:
        platform = Platform.from_dict(
            _platform_value(
                "sv",
                ["a", "b"],
                [_link("ab", "a", "b", lanes=2, latency=2)],
            )
        )
        routes = _routes(platform, [("n0", "a", ["b"])], frame_slots=8)
        schedule = build_tdm_schedule(routes, platform)
        testbench = schedule_to_systemverilog_testbench(
            routes,
            schedule,
            platform,
            frames=5,
        )
        self.assertIn("emuflow_tdm_link", testbench)
        self.assertIn("EMUFLOW_TDM_RTL_SIM status=pass", testbench)
        self.assertIn("delivery mismatch", testbench)


if __name__ == "__main__":
    unittest.main()
