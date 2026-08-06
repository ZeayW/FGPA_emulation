import tempfile
import unittest
from pathlib import Path

from emuflow.board_arm_mps4 import materialize_arm_mps4_boarddb
from emuflow.errors import ValidationError
from emuflow.platform import Platform
from emuflow.vivado_pin_sites import (
    build_vivado_pin_site_tcl,
    collect_serial_pin_inventory,
    parse_vivado_pin_site_report,
    validate_lane_site_mapping,
)


class VivadoPinSitesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.platform_path = root / "mps4.json"
        materialize_arm_mps4_boarddb(
            self.platform_path,
            name="mps4_pin_query_fixture",
            fabric_clock_mhz=50.0,
            payload_bits_per_lane_per_cycle=64,
            latency_cycles=2,
        )
        self.platform = Platform.load(self.platform_path)
        self.pins_by_part, self.lanes = collect_serial_pin_inventory(
            self.platform
        )

    def _rows(self):
        rows = {}
        function_stem = {
            "tx_p": "MGTYTXP",
            "tx_n": "MGTYTXN",
            "rx_p": "MGTYRXP",
            "rx_n": "MGTYRXN",
        }
        for lane in self.lanes:
            offset = 0 if lane["connector"] == "J49" else 24
            site = f"GTYE4_CHANNEL_X0Y{offset + lane['physical_lane']}"
            bank = 100 + lane["physical_lane"] // 4
            channel = lane["physical_lane"] % 4
            for role, pin in lane["package_pins"].items():
                rows.setdefault(
                    pin,
                    {
                        "pin_function": f"{function_stem[role]}{channel}_{bank}",
                        "site": site,
                    },
                )
        return {next(iter(self.pins_by_part)): rows}

    def test_collects_source_backed_mps4_package_pin_inventory(self) -> None:
        self.assertEqual(len(self.pins_by_part), 1)
        self.assertEqual(len(next(iter(self.pins_by_part.values()))), 96)
        self.assertEqual(len(self.lanes), 72)
        self.assertEqual(
            {record["fpga"] for record in self.lanes},
            {"mps4_1", "mps4_2", "mps4_3"},
        )

    def test_builds_part_specific_checked_vivado_query(self) -> None:
        script = build_vivado_pin_site_tcl(
            part="xcvu13p-fhga2104-1-e",
            pins=["AC40", "AC41"],
            probe_rtl=Path("probe.sv"),
            report_path=Path("pin_sites.tsv"),
        )
        self.assertIn("synth_design -rtl -mode out_of_context", script)
        self.assertIn("get_package_pins -quiet $pin", script)
        self.assertIn("get_sites -quiet -of_objects $package_pin", script)
        self.assertIn("get_property PIN_FUNC", script)
        self.assertIn("package_pin_count", script)
        self.assertIn("site_count", script)

    def test_validates_all_four_pin_roles_and_one_site_per_lane(self) -> None:
        mapped = validate_lane_site_mapping(self.lanes, self._rows())
        self.assertEqual(len(mapped), 72)
        self.assertTrue(
            all(record["site"].startswith("GTYE4_CHANNEL_") for record in mapped)
        )
        rows = self._rows()
        part = next(iter(rows))
        first_pin = self.lanes[0]["package_pins"]["tx_p"]
        rows[part][first_pin]["pin_function"] = "MGTYRXN0_100"
        with self.assertRaisesRegex(ValidationError, "pin function"):
            validate_lane_site_mapping(self.lanes, rows)

    def test_rejects_cross_site_differential_lane(self) -> None:
        rows = self._rows()
        part = next(iter(rows))
        first_pin = self.lanes[0]["package_pins"]["rx_p"]
        rows[part][first_pin]["site"] = "GTYE4_CHANNEL_X0Y99"
        with self.assertRaisesRegex(ValidationError, "span GT sites"):
            validate_lane_site_mapping(self.lanes, rows)

    def test_parses_exact_vivado_report_coverage(self) -> None:
        report = Path(self.temporary_directory.name) / "pin_sites.tsv"
        report.write_text(
            "AC40\tMGTYTXP0_129\tGTYE4_CHANNEL_X0Y36\n"
            "AC41\tMGTYTXN0_129\tGTYE4_CHANNEL_X0Y36\n",
            encoding="utf-8",
        )
        rows = parse_vivado_pin_site_report(report, ["AC40", "AC41"])
        self.assertEqual(rows["AC40"]["site"], "GTYE4_CHANNEL_X0Y36")
        with self.assertRaisesRegex(ValidationError, "coverage"):
            parse_vivado_pin_site_report(report, ["AC40"])


if __name__ == "__main__":
    unittest.main()
