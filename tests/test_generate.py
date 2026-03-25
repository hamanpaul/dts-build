from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from dtsbuild.generator import generate_dts
from dtsbuild.manifest import write_manifest
from dtsbuild.scaffold import init_folder


class GenerateTest(unittest.TestCase):
    def test_generate_dts_writes_editable_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = init_folder(
                root=root,
                project="MyBoard",
                profile="968375GWO_WL25DX_WLMLO",
                refboard="968375GO",
                family="bcm68575",
                model="MyBoardModel",
            )

            generated = generate_dts(folder, backend="manual")
            content = generated.read_text(encoding="utf-8")

            self.assertEqual(generated.parent, root / "dtsout_MyBoard")
            self.assertIn('/dts-v1/;', content)
            self.assertIn('model = "MyBoardModel";', content)
            self.assertIn('#include "inc/68375.dtsi"', content)
            self.assertIn("Block diagram interfaces", content)
            self.assertIn("TODO: network topology input rows", content)
            self.assertIn("spec backend: manual", content)
            self.assertTrue((generated.parent / "MyBoard.spec.json").exists())
            self.assertTrue((generated.parent / "MyBoard.sufficiency.json").exists())
            self.assertTrue((generated.parent / "MyBoard.gaps.json").exists())

    def test_generate_dts_uses_public_reference_renderer_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "dtsin_MyBoard"
            folder.mkdir()
            (root / "dtsout_MyBoard").mkdir()

            ref_path = root / "ref-board.dts"
            ref_path.write_text(
                textwrap.dedent(
                    """\
                    #include "inc/68375.dtsi"

                    / {
                        model = "RefBoard";
                        memory_controller {
                            memcfg = <(BP1_DDR_MCBSEL_FORMAT_VER1 | BP1_DDR_TYPE_LPDDR4)>;
                        };
                        buttons {}
                        wan_sfp: wan_sfp {}
                        tod {}
                    };

                    &wan_serdes {}
                    &i2c0 {}
                    &switch0 {}
                    &mdio {}
                    &mdio_bus {}
                    &pcie0 {}
                    """
                ),
                encoding="utf-8",
            )

            write_manifest(
                folder / "manifest.yaml",
                {
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
                        "public_ref_dts": str(ref_path),
                        "blockdiag_table": "tables/blockdiag.csv",
                        "ddr_table": "tables/ddr.csv",
                        "network_table": "tables/network.csv",
                        "gpio_led_table": "tables/gpio_led.csv",
                    },
                },
            )
            (folder / "tables").mkdir()
            (folder / "tables" / "blockdiag.csv").write_text(
                "domain,interface,present,controller,endpoint,page_ref,notes\n"
                "led_button,gpio_leds_buttons,true,gpio,leds/buttons,test,\n"
                "pcie_wifi,pcie0_wifi,true,pcie0,wifi,test,\n",
                encoding="utf-8",
            )
            (folder / "tables" / "ddr.csv").write_text(
                "field,value,notes\n"
                "memcfg_macro,BP1_DDR_MCBSEL_FORMAT_VER1 | BP1_DDR_TYPE_LPDDR4 | BP1_DDR_TOTAL_SIZE_16Gb,\n",
                encoding="utf-8",
            )
            (folder / "tables" / "network.csv").write_text(
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,notes\n"
                "lan_gphy0,true,LAN,test,gphy0,internal-2.5gphy,PHY1,port_xgphy0,slan_sd,1,pending_audit,\n"
                "lan_gphy1,true,LAN,test,gphy1,internal-2.5gphy,PHY1,port_xgphy1,slan_sd,1,pending_audit,\n"
                "lan_gphy2,true,LAN,test,gphy2,internal-2.5gphy,PHY2,port_xgphy2,slan_sd,1,pending_audit,\n"
                "lan_gphy3,true,LAN,test,gphy3,internal-2.5gphy,PHY2,port_xgphy3,slan_sd,1,pending_audit,\n"
                "wan_10g,true,WAN,test,xphy10g,xfi,PHY3,port_wan@xpon_ae,xpon_ae,1,pending_audit,\n",
                encoding="utf-8",
            )
            (folder / "tables" / "gpio_led.csv").write_text(
                "category,name,signal,pin_or_gpio,polarity,io,notes\n"
                "gpio,SCL_M2,RBR,GPIO_48,active_low,I,\"<10s Normal Reset\\n>10s Factory Reset\"\n"
                "gpio,ROGUE_ONU_IN,RBR_FB,GPIO_26,active_low,O,\n"
                "gpio,WAN_SFP_RX_LOS,WAN_SFP_RX_LOS,GPIO_03,active_low,I,\"LOW = Signal Detect\\nHIGH = Loss of Signal\"\n"
                "gpio,WAN_SFP_PRESENT,WAN_SFP_PRESENT,GPIO_04,active_low,I,\"LOW = SFP plug-in\\nHIGH = SFP plug out\"\n"
                "gpio,WAN_XCVR_RXEN,WAN_XCVR_RXEN,GPIO_06,active_low,O,\"0=On, 1=Off\"\n"
                "gpio,PCIE13_WiFi_PWR_DIS,NA,GPIO_11,active_low,O,\n"
                "gpio,WAN_SFP_PD_RST,WAN_SFP_PD_RST,GPIO_52,,O,\n"
                "gpio,WAN_XCVR_TXEN,WAN_XCVR_TXEN,GPIO_53,active_low,O,\"0=On, 1=Off\"\n"
                "gpio,SDA_M2,2G_PEWAKE,GPIO_58,,O,\n"
                "gpio,2G_RF_DISABLE_L,2G_RF_DISABLE_L,GPIO_76,active_low,O,Low=disable\n"
                "gpio,5G_RF_DISABLE_L,5G_RF_DISABLE_L,GPIO_77,active_low,O,Low=disable\n"
                "gpio,6G_RF_DISABLE_L,6G_RF_DISABLE_L,GPIO_78,active_low,O,Low=disable\n"
                "gpio,5G_PEWAKE,5G_PEWAKE,GPIO_79,,O,\n"
                "gpio,6G_PEWAKE,6G_PEWAKE,GPIO_80,,O,\n"
                "gpio,1V88_PS_EN,1V88_PS_EN,GPIO_89,active_high,O,High=enable\n"
                "gpio,CPU_VDD_PS_EN,CPU_VDD_PS_EN,GPIO_90,active_high,O,High=enable\n"
                "gpio,PCIE02_WiFi_PWR_DIS,NA,GPIO_51,active_low,O,\n",
                encoding="utf-8",
            )

            generated = generate_dts(folder, backend="manual")
            content = generated.read_text(encoding="utf-8")

            self.assertIn('model = "MyBoard";', content)
            self.assertIn("BP1_DDR_TOTAL_SIZE_16Gb", content)
            self.assertIn("&switch0 {", content)
            self.assertIn("Reset/button evidence", content)
            self.assertIn("PCIe/Wi-Fi evidence", content)
            self.assertIn("#define PCIE0_REG_GPIO     51", content)
            self.assertNotIn("gpio-keys", content)
            self.assertNotIn("Generated by dts-build", content)


if __name__ == "__main__":
    unittest.main()
