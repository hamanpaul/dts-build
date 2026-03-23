from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from dtsbuild.manifest import load_manifest
from dtsbuild.scaffold import init_folder
from dtsbuild.spec import extract_board_spec
from dtsbuild.sufficiency import build_sufficiency_report


class SufficiencyTest(unittest.TestCase):
    def test_sufficiency_reports_missing_ddr_memcfg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = init_folder(
                root=root,
                project="MyBoard",
                profile="test-profile",
                refboard="test-ref",
                family="bcm68575",
            )

            manifest = load_manifest(folder)
            spec = extract_board_spec(folder, manifest, backend="manual")
            report = build_sufficiency_report(folder, manifest, spec)

            self.assertFalse(report["ready_to_generate"])
            self.assertIn("ddr", report["blocking_gaps"])

    def test_sufficiency_becomes_ready_when_core_tables_are_filled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = init_folder(
                root=root,
                project="MyBoard",
                profile="test-profile",
                refboard="test-ref",
                family="bcm68575",
            )

            ddr_path = folder / "tables" / "ddr.csv"
            with ddr_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["field", "value", "notes"])
                writer.writerow(["memcfg_macro", "BP1_DDR_MCBSEL_FORMAT_VER1 | BP1_DDR_TYPE_LPDDR4", ""])
                writer.writerow(["ddr_type", "LPDDR4", ""])
                writer.writerow(["ddr_size", "16Gb", ""])

            gpio_path = folder / "tables" / "gpio_led.csv"
            with gpio_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["category", "name", "signal", "pin_or_gpio", "polarity", "notes"])
                writer.writerow(["gpio", "reset_button", "RESET#", "GPIO_48", "active_low", ""])
                writer.writerow(["gpio", "wan_tx_enable", "WAN_XCVR_TXEN", "GPIO_53", "active_low", ""])

            manifest = load_manifest(folder)
            spec = extract_board_spec(folder, manifest, backend="manual")
            report = build_sufficiency_report(folder, manifest, spec)

            self.assertTrue(report["ready_to_generate"])
            self.assertEqual(report["blocking_gaps"], [])


if __name__ == "__main__":
    unittest.main()
