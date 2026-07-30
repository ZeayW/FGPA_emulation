import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.open_physical_flow import (
    run_open_physical_flow,
    validate_open_physical_flow_report,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpenPhysicalFlowTest(unittest.TestCase):
    def test_one_command_flow_binds_every_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rtl = root / "design.v"
            architecture = root / "architecture.xml"
            rtl.write_text("module top; endmodule\n", encoding="utf-8")
            architecture.write_text("<architecture/>\n", encoding="utf-8")

            def fake_synthesis(_sources, _top, output, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(".model design\n.end\n", encoding="utf-8")
                return {
                    "status": "pass",
                    "sha256": _sha256(output),
                }

            def fake_vpr(arch, circuit, output_dir, **_kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                netlist = output_dir / "design.net"
                placement = output_dir / "design.place"
                route = output_dir / "design.route"
                netlist.write_text("<block/>\n", encoding="utf-8")
                placement.write_text(
                    "Array size: 8 x 9 logic blocks\n",
                    encoding="utf-8",
                )
                route.write_text("route\n", encoding="utf-8")
                return {
                    "status": "pass",
                    "architecture": {
                        "path": str(arch),
                        "sha256": _sha256(arch),
                    },
                    "circuit": {
                        "path": str(circuit),
                        "sha256": _sha256(circuit),
                    },
                    "artifacts": {
                        "packed_netlist": {
                            "path": str(netlist),
                            "sha256": _sha256(netlist),
                        },
                        "placement": {
                            "path": str(placement),
                            "sha256": _sha256(placement),
                        },
                        "route": {
                            "path": str(route),
                            "sha256": _sha256(route),
                        },
                    },
                }

            def fake_architecture_import(**kwargs):
                kwargs["architecture_output_path"].write_text(
                    "{}\n", encoding="utf-8"
                )
                kwargs["timing_output_path"].write_text(
                    "{}\n", encoding="utf-8"
                )
                self.assertEqual(kwargs["width"], 8)
                self.assertEqual(kwargs["height"], 9)
                return {"status": "pass"}

            def fake_packed(netlist, output, **_kwargs):
                output.write_text("{}\n", encoding="utf-8")
                return {
                    "status": "pass",
                    "design": "design",
                    "source_sha256": _sha256(netlist),
                }

            def fake_placement(_packed, _architecture, output_dir, **_kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                placement = output_dir / "design.place"
                placement.write_text(
                    "Array size: 8 x 9 logic blocks\n",
                    encoding="utf-8",
                )
                return {
                    "status": "pass",
                    "design": "design",
                    "artifacts": {"vpr_placement": str(placement)},
                    "vpr_placement": {
                        "array": {"width": 8, "height": 9}
                    },
                }

            def fake_route(
                arch,
                circuit,
                _netlist,
                _contract,
                _placement,
                _output,
                **_kwargs,
            ):
                return {
                    "status": "pass",
                    "architecture": {"sha256": _sha256(arch)},
                    "circuit": {"sha256": _sha256(circuit)},
                    "route_check": {
                        "status": "pass",
                        "design": "design",
                    },
                }

            with (
                patch(
                    "emuflow.open_physical_flow.run_vtr_yosys",
                    side_effect=fake_synthesis,
                ),
                patch(
                    "emuflow.open_physical_flow.run_vpr",
                    side_effect=fake_vpr,
                ),
                patch(
                    "emuflow.open_physical_flow.run_vtr_architecture_import",
                    side_effect=fake_architecture_import,
                ),
                patch(
                    "emuflow.open_physical_flow.run_packed_netlist_import",
                    side_effect=fake_packed,
                ),
                patch(
                    "emuflow.open_physical_flow."
                    "run_packed_openparf_placement",
                    side_effect=fake_placement,
                ),
                patch(
                    "emuflow.open_physical_flow.run_vpr_route_packed",
                    side_effect=fake_route,
                ),
            ):
                report = run_open_physical_flow(
                    [rtl],
                    "top",
                    root / "flow",
                    architecture=architecture,
                )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["design"], "design")
        self.assertEqual(report["array"], {"width": 8, "height": 9})
        self.assertTrue(report["hard_blocks"])

    def test_report_rejects_cross_stage_circuit_mismatch(self) -> None:
        report = {
            "schema": "emuflow.open-physical-flow/v1",
            "status": "pass",
            "hard_blocks": True,
            "array": {"width": 8, "height": 8},
            "stages": {
                "synthesis": {"status": "pass", "sha256": "a"},
                "baseline_vpr": {
                    "status": "pass",
                    "circuit": {"sha256": "b"},
                },
                "architecture_import": {"status": "pass"},
                "packed_contract": {"status": "pass"},
                "openparf_placement": {"status": "pass"},
                "final_vpr_route": {"status": "pass"},
            },
        }
        with self.assertRaisesRegex(ValidationError, "synthesized eBLIF"):
            validate_open_physical_flow_report(report)

    def test_nonempty_output_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "flow"
            output.mkdir()
            (output / "stale").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(EmuFlowError, "empty directory"):
                run_open_physical_flow(
                    [root / "missing.v"],
                    "top",
                    output,
                    architecture=root / "missing.xml",
                )


if __name__ == "__main__":
    unittest.main()
