import copy
import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.board_link_timing import build_board_link_timing_model
from emuflow.errors import EmuFlowError, ValidationError
from emuflow.io import read_json, write_json
from emuflow.multi_fpga_flow import (
    finalize_multi_fpga_physical_checkpoint,
    run_multi_fpga_flow,
    validate_multi_fpga_flow_report,
)
from emuflow.platform import Platform
from emuflow.tdm import reconstruct_tdm_schedule_timing_paths
from emuflow.timing_routing import GLOBAL_CANDIDATE_PROVIDER
from tests.native_build import (
    tdm_partition_feedback,
    tdm_ratio_optimizer,
    tdm_timing_dag_optimizer,
    tlr_router,
)


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = (
    ROOT / "platforms/virtual/academic_vtr_2fpga_p2p.json"
)


class MultiFpgaFlowTest(unittest.TestCase):
    def test_finalizes_checked_independent_physical_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "multi"
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=root,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                frame_slots=32,
                equivalence_cycles=2,
            )
            physical_root = root / "physical-resumed"
            physical_root.mkdir()
            physical_report = {
                "status": "pass",
                "design": report["stages"]["frontend"]["design"],
                "platform": report["stages"]["frontend"]["platform"],
            }
            write_json(
                physical_root / "multi-fpga-physical-flow-report.json",
                physical_report,
            )
            write_json(
                physical_root / "physical-summary.json",
                {"status": "pass"},
            )

            def fake_phase7c(*args, **kwargs):
                runtime = args[6]
                runtime.mkdir(parents=True)
                write_json(runtime / "runtime_contract.json", {})
                write_json(runtime / "qor_report.json", {})
                return {"status": "pass"}

            with (
                patch(
                    "emuflow.multi_fpga_flow.validate_multi_fpga_physical_report",
                    return_value={"status": "pass"},
                ),
                patch(
                    "emuflow.multi_fpga_flow.validate_multi_fpga_flow_report",
                    return_value={"status": "pass"},
                ),
                patch(
                    "emuflow.multi_fpga_flow.run_phase7c",
                    side_effect=fake_phase7c,
                ),
            ):
                finalized = finalize_multi_fpga_physical_checkpoint(
                    root, physical_root
                )
            self.assertEqual(finalized["summary"]["status"], "pass")
            self.assertEqual(
                finalized["artifacts"]["physical_flow_report"]["path"],
                "physical-resumed/multi-fpga-physical-flow-report.json",
            )
            with self.assertRaisesRegex(ValidationError, "runtime directory"):
                finalize_multi_fpga_physical_checkpoint(
                    root, physical_root, runtime_directory="../escape"
                )

    def test_complete_flow_selects_parallel_global_route_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "global-route"
            timing_paths = root / "paths.json"
            write_json(
                timing_paths,
                {
                    "schema": "emuflow.sta-paths/v1",
                    "design": "counter",
                    "paths": [
                        {
                            "id": "counter-cut",
                            "clock_domain": "clk",
                            "clock_period_ns": 20.0,
                            "slack_ns": 1.0,
                            "fixed_delay_ns": 0.0,
                            "cut_nets": ["q[0]", "q[2]"],
                        }
                    ],
                },
            )
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                route_provider=GLOBAL_CANDIDATE_PROVIDER,
                route_candidate_workers=2,
                timing_paths=timing_paths,
                frame_slots=32,
                tdm_provider=(
                    "deterministic-round-barrier-earliest-slot-v2"
                ),
                equivalence_cycles=2,
            )
            phase4 = report["stages"]["system_route"]
            self.assertEqual(phase4["provider"], GLOBAL_CANDIDATE_PROVIDER)
            self.assertEqual(
                phase4["candidate_generation"]["requested_workers"], 2
            )
            self.assertEqual(
                phase4["candidate_generation"]["ordering"],
                "demand-index-then-generator-index",
            )

    def test_checked_board_independent_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "multi"
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                frame_slots=32,
                equivalence_cycles=8,
            )
            self.assertEqual(report["summary"]["used_fpgas"], 2)
            self.assertEqual(report["summary"]["instances"], 8)
            self.assertEqual(report["summary"]["equivalence_mismatches"], 0)
            self.assertEqual(
                report["runtime"]["validation"]["status"], "pass"
            )
            self.assertEqual(report["summary"]["frame_slots"], 32)
            self.assertEqual(
                report["stages"]["frontend"]["synthesis"]["mode"],
                "provided-yosys-json",
            )
            persisted = json.loads(
                (output / "multi-fpga-flow-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                validate_multi_fpga_flow_report(persisted)["status"],
                "pass",
            )
            for relative in (
                "multi-fpga-flow-report.json",
                "frontend/phase1/design.emuir.json",
                "partition/assignment.json",
                "system-route/routes.json",
                "tdm/schedule.json",
                "split/manifest.json",
                "split/fpga0/netlist.json",
                "split/fpga1/netlist.json",
                "runtime/runtime_contract.json",
                "runtime/qor_report.json",
            ):
                self.assertTrue((output / relative).is_file(), relative)

    def test_report_rejects_cross_stage_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=Path(temporary_directory) / "multi",
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                router=str(tlr_router()),
                frame_slots=32,
                equivalence_cycles=2,
            )
            broken = copy.deepcopy(report)
            broken["stages"]["tdm"]["platform"] = "different"
            with self.assertRaisesRegex(
                ValidationError, "platform identity disagrees"
            ):
                validate_multi_fpga_flow_report(broken)

    def test_timing_driven_pipeline_connects_sta_through_tdm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_sta = root / "sta"
            fake_sta.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path

rows = Path(os.environ["EMUFLOW_STA_NET_MAP"]).read_text().splitlines()[1:]
header = (
    "path_id_hex\\tclock_domain_hex\\tclock_period_ns\\t"
    "slack_ns\\tfixed_delay_ns\\tpath_nets_hex"
)
records = [header]
clock = "clk".encode().hex()
for index, row in enumerate(rows):
    _, emuir_hex = row.split("\\t")
    path_id = f"path-{index}".encode().hex()
    records.append(
        f"{path_id}\\t{clock}\\t10\\t9.5\\t0.5\\t{emuir_hex}"
    )
Path(os.environ["EMUFLOW_STA_OUTPUT"]).write_text(
    "\\n".join(records) + "\\n"
)
""",
                encoding="utf-8",
            )
            fake_sta.chmod(fake_sta.stat().st_mode | stat.S_IXUSR)
            platform = Platform.load(PLATFORM)
            link_timing = build_board_link_timing_model(platform)
            link_timing["links"][0]["delay_bound_ns"] = 12.0
            link_timing_path = root / "board-link-timing.json"
            write_json(link_timing_path, link_timing)
            output = root / "multi"
            report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                timing_driven=True,
                board_link_timing_db=link_timing_path,
                clock_periods={"clk": 10.0},
                opensta=str(fake_sta),
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                frame_slots=32,
                optimize_frame_slots=True,
                cross_stage_iterations=1,
                cross_stage_feedback_optimizer=str(
                    tdm_partition_feedback()
                ),
                partition_seed_attempts=2,
                partition_repair_min_used_fpgas=True,
                partition_repair_balance=True,
                equivalence_cycles=2,
            )
            self.assertEqual(report["timing"]["status"], "pass")
            self.assertEqual(
                report["board_link_timing"]["routing_projection"][
                    "maximum_route_link_delay_ns"
                ],
                12.0,
            )
            routes = read_json(output / "system-route/routes.json")
            self.assertEqual(
                routes["constraints"]["directed_link_delay_ns"]
                [platform.links[0].id][link_timing["links"][0]["from"]]
                [link_timing["links"][0]["to"]],
                12.0,
            )
            schedule = read_json(output / "tdm/schedule.json")
            ratio_plan = read_json(output / "tdm/ratio_plan.json")
            delay_by_arc = {
                (item["link"], item["from"], item["to"]): item[
                    "delay_bound_ns"
                ]
                for item in link_timing["links"]
            }
            for hop in ratio_plan["hops"]:
                self.assertEqual(
                    hop["base_delay_ns"],
                    delay_by_arc[(hop["link"], hop["from"], hop["to"])],
                )
            reconstructed = reconstruct_tdm_schedule_timing_paths(
                routes, platform, schedule
            )
            self.assertTrue(reconstructed)
            for path in reconstructed:
                for hop in path["scheduled_hops"]:
                    self.assertEqual(
                        hop["base_link_delay_ns"],
                        delay_by_arc[(hop["link"], hop["from"], hop["to"])],
                    )
            self.assertFalse(
                report["timing"]["partition_weights_applied"]
            )
            self.assertFalse(
                report["timing"]["partition_provider_weights_applied"]
            )
            self.assertFalse(
                report["timing"]["hop_refinement_weights_applied"]
            )
            self.assertEqual(
                report["timing"]["partition_weight_consumers"],
                [],
            )
            self.assertGreater(
                report["timing"]["cut_path_projection"][
                    "projected_paths"
                ],
                0,
            )
            self.assertEqual(
                Path(
                    report["timing"]["cut_path_projection"]["output"]
                ).read_text(encoding="utf-8"),
                (output / "timing/cut-timing-paths.json").read_text(
                    encoding="utf-8"
                ),
            )
            projected = read_json(output / "timing/cut-timing-paths.json")
            self.assertEqual(
                Path(projected["source"]["input"]).resolve(),
                (output / "timing/path-database.json").resolve(),
            )
            self.assertIn(
                "timing_validation", report["stages"]["tdm"]
            )
            self.assertLess(
                report["frame_search"]["selected_frame_slots"], 32
            )
            self.assertEqual(
                report["summary"]["frame_slots"],
                report["frame_search"]["selected_frame_slots"],
            )
            selected_iteration = report["cross_stage"][
                "selected_iteration"
            ]
            selected = report["cross_stage"]["candidates"][
                selected_iteration
            ]
            self.assertEqual(
                report["summary"]["cross_stage_iteration"],
                selected_iteration,
            )
            self.assertEqual(
                selected["phase3_validation"],
                report["stages"]["partition"]["validation"],
            )
            self.assertEqual(
                selected["phase4_validation"],
                report["stages"]["system_route"]["validation"],
            )
            self.assertEqual(
                selected["phase5_validation"],
                report["stages"]["tdm"]["validation"],
            )
            self.assertEqual(
                report["runtime"]["validation"]["status"], "pass"
            )
            self.assertEqual(
                report["cross_stage"]["configuration"][
                    "partition_seed_attempts"
                ],
                2,
            )
            self.assertTrue(
                report["cross_stage"]["configuration"][
                    "partition_repair_min_used_fpgas"
                ]
            )
            self.assertTrue(
                report["cross_stage"]["configuration"][
                    "partition_repair_balance"
                ]
            )
            broken_cross_stage = copy.deepcopy(report)
            broken_cross_stage["cross_stage"]["selected_candidate_id"] = (
                "tampered"
            )
            with self.assertRaisesRegex(
                ValidationError, "selected cross-stage candidate"
            ):
                validate_multi_fpga_flow_report(broken_cross_stage)
            broken = copy.deepcopy(report)
            broken["frame_search"]["selected_frame_slots"] = 31
            with self.assertRaisesRegex(
                ValidationError, "selected frame-search candidate"
            ):
                validate_multi_fpga_flow_report(broken)
            for relative in (
                "timing/path-database.json",
                "timing/partition-net-weights.json",
                "timing/cut-timing-paths.json",
                "timing/board-link-timing.json",
                "timing/board-link-route-constraints.json",
                "frame-search/frame-search-report.json",
                "cross-stage/cross_stage_report.json",
                "runtime/runtime_contract.json",
            ):
                self.assertTrue((output / relative).is_file(), relative)

            direct_output = root / "multi-direct"
            direct_report = run_multi_fpga_flow(
                platform_path=PLATFORM,
                output_dir=direct_output,
                yosys_json=ROOT / "examples/yosys/counter.json",
                top="counter",
                clocks=["clk"],
                partition_provider="greedy",
                timing_driven=True,
                board_link_timing_db=link_timing_path,
                clock_periods={"clk": 10.0},
                opensta=str(fake_sta),
                router=str(tlr_router()),
                ratio_optimizer=str(tdm_ratio_optimizer()),
                timing_dag_optimizer=str(tdm_timing_dag_optimizer()),
                cross_stage_iterations=0,
                equivalence_cycles=2,
            )
            direct_projection = direct_report["timing"][
                "cut_path_projection"
            ]
            self.assertEqual(
                Path(direct_projection["output"]).read_text(
                    encoding="utf-8"
                ),
                (direct_output / "timing/cut-timing-paths.json").read_text(
                    encoding="utf-8"
                ),
            )
            direct_projected = read_json(
                direct_output / "timing/cut-timing-paths.json"
            )
            self.assertEqual(
                Path(direct_projected["source"]["input"]).resolve(),
                (direct_output / "timing/path-database.json").resolve(),
            )

    def test_nonempty_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            (output / "keep").write_text("user data", encoding="utf-8")
            with self.assertRaisesRegex(EmuFlowError, "empty directory"):
                run_multi_fpga_flow(
                    platform_path=PLATFORM,
                    output_dir=output,
                    yosys_json=ROOT / "examples/yosys/counter.json",
                    top="counter",
                )

    def test_compile_can_continue_into_checked_serial_bsp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "multi"
            platform_name = Platform.load(PLATFORM).name
            bsp_report = {
                "schema": "emuflow.multi-fpga-bsp-flow/v1",
                "status": "pass",
                "design": "counter",
                "platform": platform_name,
                "qualification": "source_bound_bsp_structure_validation",
                "hardware_release_status": "blocked_on_board_proof",
                "source_flow_report_sha256": "0" * 64,
                "stages": {
                    "phase6b": {
                        "status": "pass",
                        "design": "counter",
                        "platform": platform_name,
                    },
                    "runtime_sync": {"status": "pass"},
                    "phase6c": {
                        "status": "pass",
                        "design": "counter",
                        "platform": platform_name,
                    },
                    "phy_elaboration": {
                        "status": "pass",
                        "design": "counter",
                        "platform": platform_name,
                        "tool": {"name": "yosys"},
                    },
                },
                "validation": {
                    "fpgas": 2,
                    "elaboration_failures": 0,
                    "hardware_release_authorized": False,
                    "gt_site_map_status": "not-provided",
                },
                "artifacts": {},
            }

            def fake_bsp(**kwargs):
                destination = kwargs["output_dir"]
                destination.mkdir(parents=True)
                source_report = (
                    kwargs["flow_root"]
                    / "board-independent-flow-report.json"
                )
                bsp_report["source_flow_report_sha256"] = hashlib.sha256(
                    source_report.read_bytes()
                ).hexdigest()
                write_json(
                    destination / "multi-fpga-bsp-flow-report.json",
                    bsp_report,
                )
                return bsp_report

            with patch(
                "emuflow.multi_fpga_flow.run_multi_fpga_bsp_flow",
                side_effect=fake_bsp,
            ):
                report = run_multi_fpga_flow(
                    platform_path=PLATFORM,
                    output_dir=output,
                    yosys_json=ROOT / "examples/yosys/counter.json",
                    top="counter",
                    clocks=["clk"],
                    partition_provider="greedy",
                    router=str(tlr_router()),
                    frame_slots=32,
                    equivalence_cycles=2,
                    serial_bsp_phy_provider=Path("provider.json"),
                    serial_bsp_runtime_sync_provider=Path("runtime.json"),
                    serial_bsp_yosys=Path("yosys"),
                )
            self.assertEqual(report["summary"]["hardware_bsp_status"], "pass")
            self.assertTrue(
                (output / "board-independent-flow-report.json").is_file()
            )
            self.assertEqual(report["hardware_bsp"], bsp_report)


if __name__ == "__main__":
    unittest.main()
