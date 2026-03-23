from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from dtsbuild.bootstrap_tables import bootstrap_tables
from dtsbuild.generator import generate_dts
from dtsbuild.manifest import load_manifest, write_manifest
from dtsbuild.spec import extract_board_spec
from dtsbuild.sufficiency import build_sufficiency_report


class BootstrapTablesTest(unittest.TestCase):
    def test_bootstrap_tables_generates_sufficient_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "dtsin_MyBoard"
            folder.mkdir()
            (root / "dtsout_MyBoard").mkdir()

            manifest_payload = {
                "project": "MyBoard",
                "family": "bcm68575",
                "profile": "test-profile",
                "refboard": "test-ref",
                "model": "MyBoard",
                "output_dts": "MyBoard.dts",
                "output_dir": "dtsout_MyBoard",
                "base_include": "inc/68375.dtsi",
                "compatible": "brcm,bcm968375",
                "artifacts": {
                    "public_ref_dts": "968575REF1.dts",
                    "gpio_led_table": "GPIO_R0A.xlsx",
                    "schematic_pdfs": [
                        "MAINBOARD.pdf",
                        "DAUGHTER.pdf",
                    ],
                },
                "notes": [
                    "Current best-fit network reference is 968375REF2.dts, but this is still an inference rather than a proven match."
                ],
            }
            write_manifest(folder / "manifest.yaml", manifest_payload)

            workbook = Workbook()
            ws = workbook.active
            ws["D2"] = "BCM68575"
            ws.append([])
            ws.append(["BCM68575公版 Pinout", "Pinmux", "", "Pin", "Function Description", "Pinmux", "I/O", "Active", "Note"])
            ws.append(["WAN_SFP_RX_LOS", "0", "", "GPIO_03", "WAN_SFP_RX_LOS", "0", "I", "Low", "LOW = Signal Detect"])
            ws.append(["PCIE13_WiFi_PWR_DIS", "4", "", "GPIO_11", "NA", "4", "O", "Low", "WiFi power disable"])
            ws.append(["Reset Button", "0", "", "GPIO_48", "RESET#", "0", "I", "Low", "Reset button"])
            ws.append(["WAN_LED_G", "0", "", "GPIO_49", "WAN_LED_G", "0", "O", "Low", "WAN LED"])
            workbook.save(folder / "GPIO_R0A.xlsx")

            for name in ("MAINBOARD.pdf", "DAUGHTER.pdf", "K4F6E3S4HM-MGCJ.pdf"):
                (folder / name).write_bytes(b"%PDF-1.4\n")
            (folder / "968575REF1.dts").write_text(
                """
#include "inc/68375.dtsi"
/ {
    model = "968575REF1";
    memory_controller {
        memcfg = <(BP1_DDR_MCBSEL_FORMAT_VER1 | BP1_DDR_TYPE_LPDDR4 | BP1_DDR_SPEED_2133_36_39_39 | BP1_DDR_WIDTH_32BIT | BP1_DDR_TOTAL_SIZE_16Gb | BP1_DDR_SSC_CONFIG_1)>;
    };
    buttons {
        compatible = "brcm,buttons";
    };
    wan_sfp: wan_sfp {
        compatible = "brcm,sfp";
    };
    tod {
        compatible = "brcm,tod";
    };
};
&wan_serdes {
    status = "okay";
};
&i2c0 {
    status = "okay";
};
                """.strip()
                + "\n",
                encoding="utf-8",
            )

            pdf_text_map = {
                ("MAINBOARD.pdf", 1, 1): """
                    06_LPDDR4                          6
                    08_eMMC                            8
                    10_BCM68575_PCIE0/1/2             10
                    13_GPHY/10GPHY_RJ45               13
                    14_10G_WAN_CAGE                   14
                    15_USB2.0                         15
                    17_LED                            17
                """,
                ("MAINBOARD.pdf", 6, 6): """
                    LPDDR4 - Dual Die
                    Data mapped per Pine LPDDR4 setting
                    K4F6E3S4HM-MGCJ
                """,
                ("MAINBOARD.pdf", 8, 8): """
                    eMMC
                    KLM8G1GETF-B041 eMMC 64Gb
                """,
                ("MAINBOARD.pdf", 13, 13): """
                    2.5GPHY 0
                    2.5GPHY 1
                    2.5GPHY 2
                    2.5GPHY 3
                    8P8C
                """,
                ("MAINBOARD.pdf", 14, 14): """
                    10GPHY
                    XPHY10G_TRD0_P
                    10G WAN CAGE
                """,
                ("DAUGHTER.pdf", 1, 1): """
                    SYSTEM_BLOCK_DIAGRAM                 2
                """,
                ("DAUGHTER.pdf", 2, 2): """
                    PCIE2 (single lane)
                    2G_RF_DISABLE_L
                    PCIE3 (dual lane)
                    5G_RF_DISABLE_L
                    PCIE1 (dual lane)
                    6G_RF_DISABLE_L
                """,
                ("K4F6E3S4HM-MGCJ.pdf", 1, 8): """
                    K4F6E3S4HM-MGCJ datasheet LPDDR4 SDRAM
                    16Gb LPDDR4 SDRAM
                    200FBGA, 10x15
                    Organization per channel x32
                """,
            }

            def fake_extract_pdf_text(path: Path, *, first_page: int | None = None, last_page: int | None = None) -> str:
                return pdf_text_map.get((path.name, first_page, last_page), "")

            with patch("dtsbuild.bootstrap_tables.extract_pdf_text", side_effect=fake_extract_pdf_text):
                result = bootstrap_tables(folder, force=True)

            self.assertTrue((folder / "tables" / "blockdiag.csv").exists())
            self.assertTrue((folder / "tables" / "ddr.csv").exists())
            self.assertTrue((folder / "tables" / "gpio_led.csv").exists())
            self.assertTrue((folder / "tables" / "network.csv").exists())
            self.assertIn("ddr_table", result.updated_artifacts)

            manifest = load_manifest(folder)
            self.assertEqual(manifest.artifacts["blockdiag_table"], "tables/blockdiag.csv")
            self.assertEqual(manifest.artifacts["ddr_table"], "tables/ddr.csv")
            self.assertEqual(manifest.artifacts["network_table"], "tables/network.csv")
            self.assertEqual(manifest.artifacts["gpio_led_table"], "tables/gpio_led.csv")
            self.assertEqual(manifest.artifacts["public_ref_dts"], "968575REF1.dts")
            self.assertFalse(any("968375REF2" in note for note in manifest.notes))

            with (folder / "tables" / "ddr.csv").open("r", encoding="utf-8", newline="") as fh:
                ddr_rows = list(csv.DictReader(fh))
            self.assertTrue(any(row["field"] == "memcfg_macro" and "BP1_DDR_TYPE_LPDDR4" in row["value"] for row in ddr_rows))
            self.assertTrue(any(row["field"] == "memcfg_macro" and "public_ref_dts" in row["notes"] for row in ddr_rows))

            spec = extract_board_spec(folder, manifest, backend="manual")
            self.assertEqual(spec["public_reference"]["path"], "968575REF1.dts")
            self.assertIn("wan_serdes", spec["public_reference"]["patterns"])
            report = build_sufficiency_report(folder, manifest, spec)
            self.assertTrue(report["ready_to_generate"])
            self.assertEqual(report["blocking_gaps"], [])
            self.assertNotIn(
                "Please provide a block diagram table listing interfaces such as DDR, WAN/SFP, xPHY/LAN, PCIe/Wi-Fi, USB, storage, LEDs, buttons, I2C devices, and TOD.",
                report["questions"],
            )

            generated = generate_dts(folder, backend="manual")
            content = generated.read_text(encoding="utf-8")
            self.assertIn("memcfg = <(BP1_DDR_MCBSEL_FORMAT_VER1", content)
            self.assertIn("wan_10g", content)
            self.assertIn("lan_gphy0", content)
            self.assertIn("Public reference patterns", content)
            self.assertIn("path=968575REF1.dts", content)


if __name__ == "__main__":
    unittest.main()
