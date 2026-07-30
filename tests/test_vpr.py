import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emuflow.errors import EmuFlowError, ValidationError
from emuflow.vpr import (
    build_vtr_yosys_script,
    run_vpr_route_packed,
    validate_vpr_outputs,
)


class VprTest(unittest.TestCase):
    def test_logic_only_script_lowers_ff_variants_to_latches(self) -> None:
        script = build_vtr_yosys_script(
            [Path("rtl/cpu.v")],
            "cpu",
            Path("build/cpu.eblif"),
        )
        self.assertIn("synth -top cpu -noabc", script)
        self.assertEqual(script.count("dffunmap"), 2)
        self.assertIn("abc -lut 6", script)
        self.assertIn('write_blif -attr -cname "build/cpu.eblif"', script)

    def test_empty_source_list_is_rejected(self) -> None:
        with self.assertRaisesRegex(EmuFlowError, "at least one RTL source"):
            build_vtr_yosys_script([], "cpu", Path("cpu.eblif"))

    def test_route_report_requires_success_and_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            netlist = root / "cpu.net"
            placement = root / "cpu.place"
            route = root / "cpu.route"
            for path in (netlist, placement, route):
                path.write_text(path.name, encoding="utf-8")
            report = validate_vpr_outputs(
                """
                Netlist num_nets: 2330
                Netlist num_blocks: 605
                Netlist io blocks: 342.
                Netlist clb blocks: 263.
                Netlist mult_36 blocks: 0.
                Netlist memory blocks: 0.
                Device Utilization: 0.69 (target 1.00)
                Total wirelength: 29761, average net length: 12.7894
                Final critical path delay (least slack): 8.08208 ns,
                Fmax: 123.731 MHz
                VPR succeeded
                """,
                packed_netlist=netlist,
                placement=placement,
                route=route,
            )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["metrics"]["packed_blocks"], 605)
        self.assertEqual(report["metrics"]["clb_blocks"], 263)
        self.assertEqual(report["metrics"]["wirelength"], 29761)
        self.assertEqual(report["metrics"]["fmax_mhz"], 123.731)
        self.assertEqual(
            report["stages"], ["pack", "place", "route", "analysis"]
        )

    def test_route_report_rejects_missing_success_marker(self) -> None:
        with self.assertRaisesRegex(ValidationError, "success marker"):
            validate_vpr_outputs(
                "Netlist num_nets: 1",
                packed_netlist=Path("missing.net"),
                placement=Path("missing.place"),
                route=Path("missing.route"),
            )

    def test_route_packed_uses_existing_netlist_and_placement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            architecture = root / "arch.xml"
            circuit = root / "cpu.eblif"
            netlist = root / "cpu.net"
            placement = root / "cpu.place"
            for path in (architecture, circuit, netlist, placement):
                path.write_text(path.name, encoding="utf-8")

            def fake_run(arguments, **_kwargs):
                route_index = arguments.index("--route_file") + 1
                Path(arguments[route_index]).write_text(
                    "route", encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    """
                    Netlist num_nets: 2
                    Netlist num_blocks: 3
                    Total wirelength: 12
                    VPR succeeded
                    """,
                )

            with patch("emuflow.vpr.subprocess.run", side_effect=fake_run):
                report = run_vpr_route_packed(
                    architecture,
                    circuit,
                    netlist,
                    placement,
                    root / "route",
                    executable="/source-built/vpr",
                )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["stages"], ["route", "analysis"])
        self.assertIn("--net_file", report["command"])
        self.assertIn("--place_file", report["command"])


if __name__ == "__main__":
    unittest.main()
