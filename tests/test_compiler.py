"""Focused tests for dtsbuild.agents.compiler helpers."""

from __future__ import annotations

import asyncio

from dtsbuild.agents.refdiff import parse_dts_document
from dtsbuild.agents.compiler import (
    _compile_direct,
    _render_buttons,
    _render_cpufreq,
    _render_ethphy,
    _render_gpioc_wifi_hogs,
    _render_i2c,
    _render_mdio,
    _render_mdio_bus,
    _render_pcie,
    _render_phy_wan_serdes,
    _render_power_ctrl,
    _render_serdes,
    _render_serdes_core,
    _render_switch0,
    _render_usb,
    _render_wdt,
    _render_wan_sfp,
    _render_xport,
)
from dtsbuild.schema import Device, DtsHint, HardwareSchema, Provenance, Signal


def _prov() -> Provenance:
    return Provenance(
        pdfs=["board.pdf"],
        pages=[1],
        refs=["U1"],
        method="net_trace",
        confidence=0.9,
    )


def _sig(name: str, role: str, soc_pin: str) -> Signal:
    return Signal(
        name=name,
        soc_pin=soc_pin,
        traced_path="U1→R1",
        role=role,
        status="VERIFIED",
        provenance=_prov(),
    )


def test_render_power_ctrl_aggregates_primary_and_phy_outputs():
    rendered = _render_power_ctrl(
        [
            _sig("1V88_PS_EN", "POWER_CONTROL", "GPIO_89"),
            _sig("CPU_VDD_PS_EN", "POWER_CONTROL", "GPIO_90"),
        ]
    )

    assert rendered.count("&ext_pwr_ctrl {") == 1
    assert "pwr-ctrl-0-gpios = <&gpioc 90 GPIO_ACTIVE_HIGH>;" in rendered
    assert "phy-pwr-ctrl-gpios = <&gpioc 89 GPIO_ACTIVE_HIGH>;" in rendered
    assert 'status = "okay";' in rendered


def test_render_power_ctrl_keeps_extra_controls_as_numbered_properties():
    rendered = _render_power_ctrl(
        [
            _sig("CPU_VDD_PS_EN", "POWER_CONTROL", "GPIO_90"),
            _sig("PHY_PWR_EN", "POWER_CONTROL", "GPIO_89"),
            _sig("WLAN_PWR_EN", "POWER_CONTROL", "GPIO_91"),
        ]
    )

    assert rendered.count("&ext_pwr_ctrl {") == 1
    assert "pwr-ctrl-0-gpios = <&gpioc 90 GPIO_ACTIVE_HIGH>;" in rendered
    assert "phy-pwr-ctrl-gpios = <&gpioc 89 GPIO_ACTIVE_HIGH>;" in rendered
    assert "pwr-ctrl-1-gpios = <&gpioc 91 GPIO_ACTIVE_HIGH>;" in rendered


def test_render_power_ctrl_ignores_usb_power_signals_and_ball_names():
    rendered = _render_power_ctrl(
        [
            _sig("CPU_VDD_PS_EN", "POWER_CONTROL", "GPIO_90"),
            _sig("1V88_PS_EN", "POWER_CONTROL", "GPIO_89"),
            _sig("USB0_PWRON_N", "USB_POWER", "K3"),
            _sig("USB1_PWRON", "USB_POWER", "M31"),
        ]
    )

    assert "pwr-ctrl-0-gpios = <&gpioc 90 GPIO_ACTIVE_HIGH>;" in rendered
    assert "phy-pwr-ctrl-gpios = <&gpioc 89 GPIO_ACTIVE_HIGH>;" in rendered
    assert "<&gpioc 3 GPIO_ACTIVE_HIGH>" not in rendered
    assert "<&gpioc 31 GPIO_ACTIVE_HIGH>" not in rendered


def test_render_buttons_inherits_reset_behavior_when_button_exists():
    rendered = _render_buttons([_sig("RST_BTN", "RESET_BUTTON", "GPIO_48")])

    assert "reset_button {" in rendered
    assert "linux,code" not in rendered
    assert "press {" in rendered
    assert "rst_to_dflt = <5>;" in rendered
    assert 'print = "Button Press -- Hold for 5s to do restore to default";' in rendered
    assert "reset = <0>;" in rendered


def test_render_wdt_enables_builtin_watchdog_for_supported_chip():
    schema = HardwareSchema(project="TEST", chip="BCM68575")

    rendered = _render_wdt(schema, [])

    assert "&wdt {" in rendered
    assert 'status = "okay";' in rendered


def test_render_cpufreq_emits_dvfs_policy_for_supported_chip():
    schema = HardwareSchema(project="TEST", chip="BCM68575")

    rendered = _render_cpufreq(schema)

    assert "&cpufreq {" in rendered
    assert 'op-mode = "dvfs";' in rendered


def test_render_xport_enables_builtin_node_when_network_topology_is_proven():
    schema = HardwareSchema(project="TEST", chip="BCM68575")

    rendered = _render_xport(
        schema,
        [_sig("WAN_SFP_PRESENT", "SFP", "GPIO_04")],
        [],
    )

    assert "&xport {" in rendered
    assert 'status = "okay";' in rendered


def test_render_switch0_emits_proven_ports_from_topology_hints():
    rendered = _render_switch0(
        [
            DtsHint(
                target="&switch0/ports/port_xgphy0",
                property="status",
                value='"okay"',
                reason="Stable topology row lan_gphy0",
                provenance=_prov(),
            ),
            DtsHint(
                target="&switch0/ports/port_wan@xpon_ae",
                property="status",
                value='"okay"',
                reason="Stable topology row wan_10g",
                provenance=_prov(),
            ),
        ]
    )

    assert "&switch0 {" in rendered
    assert "ports {" in rendered
    assert "port_xgphy0 {" in rendered
    assert "port_wan@xpon_ae {" in rendered
    assert rendered.count('status = "okay";') == 2


def test_render_i2c_emits_u41_gpio_expander():
    rendered = _render_i2c(
        [],
        [
            Device(
                refdes="U41",
                part_number="TCA9555PWR",
                compatible="nxp,pca9555",
                bus="i2c0",
                address="0x27",
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
    )

    assert "&i2c0 {" in rendered
    assert 'pinctrl-names = "default";' in rendered
    assert "pinctrl-0 = <&bsc_m0_scl_pin_28 &bsc_m0_sda_pin_29>;" in rendered
    assert "u41: gpio@27 {" in rendered
    assert 'compatible = "nxp,pca9555";' in rendered
    assert "reg = <0x27>;" in rendered


def test_render_i2c_prefers_reference_alias_for_matching_gpio_expander(tmp_path):
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "};",
                "",
                "&i2c0 {",
                '    status = "okay";',
                "",
                "    gpiocext_wlan: gpio@27 {",
                '        compatible = "nxp,pca9555";',
                "        reg = <0x27>;",
                "    };",
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rendered = _render_i2c(
        [],
        [
            Device(
                refdes="U41",
                part_number="TCA9555PWR",
                compatible="nxp,pca9555",
                bus="i2c0",
                address="0x27",
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
        reference_doc=parse_dts_document(ref_path),
    )

    assert "gpiocext_wlan: gpio@27 {" in rendered
    assert "u41: gpio@27 {" not in rendered


def test_render_i2c_limits_bsc_pinctrl_to_i2c0():
    rendered = _render_i2c(
        [],
        [
            Device(
                refdes="U99",
                part_number="PCA9555",
                compatible="nxp,pca9555",
                bus="i2c1",
                address="0x20",
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
    )

    assert "&i2c1 {" in rendered
    assert "pinctrl-0 = <&bsc_m0_scl_pin_28 &bsc_m0_sda_pin_29>;" not in rendered


def test_render_led_ctrl_keeps_control_bus_without_emitting_child_leds():
    from dtsbuild.agents.compiler import _render_led_ctrl

    rendered = _render_led_ctrl(
        [
            _sig("SER_LED_DATA", "LED_CONTROL", "GPIO_55"),
            _sig("SER_LED_CLK", "LED_CONTROL", "GPIO_56"),
            _sig("SER_LED_MASK", "LED_CONTROL", "GPIO_57"),
        ],
        [
            Device(
                refdes="U12",
                part_number="U74HC595AG",
                compatible=None,
                bus=None,
                address=None,
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
    )

    assert "&led_ctrl {" in rendered
    assert "serial-shifters-installed = <1>;" in rendered
    assert "serial-ser_led_data" not in rendered
    assert "crossbar-output" not in rendered


def test_render_led_ctrl_does_not_guess_child_leds_from_endpoint_signals():
    from dtsbuild.agents.compiler import _render_led_ctrl

    rendered = _render_led_ctrl(
        [
            _sig("WAN_LED", "LED_WAN", "GPIO_49"),
            _sig("LAN1_LED", "LED_LAN", "GPIO_50"),
            _sig("SER_LED_DATA", "LED_CONTROL", "GPIO_55"),
            _sig("SER_LED_CLK", "LED_CONTROL", "GPIO_56"),
            _sig("SER_LED_MASK", "LED_CONTROL", "GPIO_57"),
        ],
        [
            Device(
                refdes="U12",
                part_number="U74HC595AG",
                compatible=None,
                bus=None,
                address=None,
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
    )

    assert "&led_ctrl {" in rendered
    assert "serial-shifters-installed = <1>;" in rendered
    assert "led0:" not in rendered
    assert "serial-wan_led" not in rendered
    assert "crossbar-output" not in rendered


def test_render_hsspi_accepts_spis_signals_with_spi_role():
    from dtsbuild.agents.compiler import _render_hsspi

    rendered = _render_hsspi(
        [
            _sig("SPIS_CLK", "SPI", "GPIO_8"),
            _sig("SPIS_MISO", "SPI", "GPIO_9"),
            _sig("SPIS_MOSI", "SPI", "GPIO_10"),
            _sig("SPIS_SS_B", "SPI", "GPIO_11"),
        ]
    )

    assert "&hsspi {" in rendered
    assert 'status = "okay";' in rendered


def test_render_ethphy_uses_mdio_lane_swap_hints_without_ethernet_phy_signals():
    rendered = _render_ethphy(
        [],
        [
            DtsHint(
                target="&mdio_bus/xphy0",
                property="enet-phy-lane-swap",
                value="GPHY0: Pair 0 swapped",
                reason="Lane swap detected for GPHY0",
                provenance=_prov(),
            ),
            DtsHint(
                target="&mdio_bus/xphy2",
                property="enet-phy-lane-swap",
                value="GPHY2: Pair 1 swapped",
                reason="Lane swap detected for GPHY2",
                provenance=_prov(),
            ),
        ],
    )

    assert "&ethphytop {" in rendered
    assert "xphy0-enabled;" in rendered
    assert "xphy2-enabled;" in rendered
    assert "enet-phy-lane-swap;" not in rendered
    assert 'status = "okay";' in rendered


def test_render_ethphy_deduplicates_topology_and_lane_swap_evidence():
    rendered = _render_ethphy(
        [],
        [
            DtsHint(
                target="&ethphytop",
                property="xphy0-enabled",
                reason="Stable topology row lan_gphy0",
                provenance=_prov(),
            ),
            DtsHint(
                target="&ethphytop",
                property="xphy3-enabled",
                reason="Stable topology row lan_gphy3",
                provenance=_prov(),
            ),
            DtsHint(
                target="&mdio_bus/xphy0",
                property="enet-phy-lane-swap",
                value="GPHY0: Pair 0 swapped",
                reason="Lane swap detected for GPHY0",
                provenance=_prov(),
            ),
        ],
    )

    assert rendered.count("xphy0-enabled;") == 1
    assert rendered.count("xphy3-enabled;") == 1


def test_render_mdio_enables_builtin_node_when_lane_swap_path_is_proven():
    schema = HardwareSchema(project="TEST", chip="BCM68575")

    rendered = _render_mdio(
        schema,
        [],
        [
            DtsHint(
                target="&mdio_bus/xphy1",
                property="enet-phy-lane-swap",
                value="GPHY1: Pair 0 swapped",
                reason="Lane swap detected for GPHY1",
                provenance=_prov(),
            )
        ],
    )

    assert "&mdio {" in rendered
    assert 'status = "okay";' in rendered


def test_render_mdio_bus_emits_per_xphy_lane_swap_children():
    rendered = _render_mdio_bus(
        [],
        [
            DtsHint(
                target="&mdio_bus/xphy0",
                property="enet-phy-lane-swap",
                value="GPHY0: Pair 0 swapped",
                reason="Lane swap detected for GPHY0",
                provenance=_prov(),
            ),
            DtsHint(
                target="&mdio_bus/xphy2",
                property="enet-phy-lane-swap",
                value="GPHY2: Pair 1 swapped",
                reason="Lane swap detected for GPHY2",
                provenance=_prov(),
            ),
        ],
    )

    assert "&mdio_bus {" in rendered
    assert "xphy0 {" in rendered
    assert "xphy2 {" in rendered
    assert rendered.count("enet-phy-lane-swap;") == 2
    assert "xphy1 {" not in rendered


def test_render_wan_sfp_emits_full_node_from_verified_sfp_signals():
    rendered = _render_wan_sfp(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
            _sig("WAN_XCVR_RXEN", "SFP", "GPIO_06"),
            _sig("WAN_SFP_PD_RST", "SFP", "GPIO_52"),
            _sig("WAN_XCVR_TXEN", "SFP", "GPIO_53"),
        ]
    )

    assert "wan_sfp: wan_sfp {" in rendered
    assert 'compatible = "brcm,sfp";' in rendered
    assert "los-gpio = <&gpioc 3 GPIO_ACTIVE_HIGH>;" in rendered
    assert "mod-def0-gpio = <&gpioc 4 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-power-gpio = <&gpioc 53 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-power-down-gpio = <&gpioc 52 GPIO_ACTIVE_HIGH>;" in rendered
    assert "rx-power-gpio = <&gpioc 6 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-disable-gpio" not in rendered
    assert "pinctrl-names" not in rendered


def test_render_wan_sfp_partially_renders_only_proven_gpio_fields():
    rendered = _render_wan_sfp(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
        ]
    )

    assert "wan_sfp: wan_sfp {" in rendered
    assert 'compatible = "brcm,sfp";' in rendered
    assert "los-gpio = <&gpioc 3 GPIO_ACTIVE_HIGH>;" in rendered
    assert "mod-def0-gpio = <&gpioc 4 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-power-gpio" not in rendered
    assert "tx-power-down-gpio" not in rendered
    assert "rx-power-gpio" not in rendered


def test_render_wan_sfp_emits_i2c_bus_when_hint_proves_bus_mapping():
    rendered = _render_wan_sfp(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
        ],
        [
            DtsHint(
                target="wan_sfp",
                property="i2c-bus",
                value="<&i2c0>",
                reason="SFP page scan proves bus 0",
                provenance=_prov(),
            )
        ],
    )

    assert "wan_sfp: wan_sfp {" in rendered
    assert "i2c-bus = <&i2c0>;" in rendered
    assert "los-gpio = <&gpioc 3 GPIO_ACTIVE_HIGH>;" in rendered
    assert "mod-def0-gpio = <&gpioc 4 GPIO_ACTIVE_LOW>;" in rendered


def test_render_serdes_core_enables_when_sfp_path_is_proven():
    rendered = _render_serdes_core(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
        ]
    )

    assert "&serdes {" in rendered
    assert 'status = "okay";' in rendered


def test_render_wan_serdes_only_emits_proven_serdes0_child():
    rendered = _render_serdes(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
            _sig("WAN_XCVR_RXEN", "SFP", "GPIO_06"),
            _sig("WAN_SFP_PD_RST", "SFP", "GPIO_52"),
            _sig("WAN_XCVR_TXEN", "SFP", "GPIO_53"),
        ]
    )

    assert "&wan_serdes {" in rendered
    assert "serdes0 {" in rendered
    assert "trx = <&wan_sfp>;" in rendered
    assert "serdes1 {" not in rendered


def test_render_phy_wan_serdes_enables_when_serdes0_path_exists():
    rendered = _render_phy_wan_serdes(
        [
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
        ]
    )

    assert "&phy_wan_serdes {" in rendered
    assert 'status = "okay";' in rendered


def test_render_usb_emits_port1_disabled_when_hint_proves_single_port_population():
    rendered = _render_usb(
        [
            _sig("USB0_PWRON_N", "USB_POWER", "GPIO_33"),
            _sig("USB1_PWRON", "USB_POWER", "GPIO_31"),
            _sig("USB0_SSRXN", "USB_SUPERSPEED", "GPIO_32"),
        ],
        [
            DtsHint(
                target="&usb_ctrl",
                property="port1-disabled",
                value=None,
                reason="Mainboard USB page shows only USB0 populated",
                provenance=_prov(),
            )
        ],
    )

    assert "&usb_ctrl {" in rendered
    assert "xhci-enable;" in rendered
    assert "port1-disabled;" in rendered


def test_render_pcie_stays_empty_without_tri_band_corrobation():
    rendered = _render_pcie(
        [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
        ]
    )

    assert rendered == ""


def test_render_pcie_enables_all_hosts_from_grouped_controls_and_tri_band_signals():
    rendered = _render_pcie(
        [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
            _sig("6G_PEWAKE", "PCIE_WIFI", "GPIO_80"),
        ]
    )

    assert "&pcie0 {" in rendered
    assert "&pcie1 {" in rendered
    assert "&pcie2 {" in rendered
    assert "&pcie02 {" not in rendered
    assert "&pcie13 {" not in rendered
    assert rendered.count('status = "okay";') == 3


def test_render_pcie_rejects_partial_tri_band_evidence():
    rendered = _render_pcie(
        [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
        ]
    )

    assert rendered == ""


def test_render_pcie_requires_pewake_and_rf_disable_per_band():
    rendered = _render_pcie(
        [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
        ]
    )

    assert rendered == ""


def test_render_pcie_renders_regulator_macros_from_grfic_controls():
    rendered = _render_pcie(
        [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
            _sig("6G_PEWAKE", "PCIE_WIFI", "GPIO_80"),
            _sig("GPIO_2GRFIC", "GENERAL_GPIO", "GPIO_24"),
            _sig("GPIO_5GRFIC", "GENERAL_GPIO", "GPIO_54"),
            _sig("GPIO_6GRFIC", "GENERAL_GPIO", "GPIO_25"),
        ]
    )

    assert '#if defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)' in rendered
    assert "#define PCIE_REG_GPIOC     gpioc" in rendered
    assert "#define PCIE0_REG_GPIO    54" in rendered
    assert "#define PCIE1_REG_GPIO    25" in rendered
    assert "#define PCIE2_REG_GPIO    24" in rendered
    assert rendered.count("GPIO_ACTIVE_LOW") == 3
    assert '#include "../bcm_pcie_regulator.dtsi"' in rendered
    assert "&pcie0 {" in rendered
    assert "&pcie1 {" in rendered
    assert "&pcie2 {" in rendered
    assert rendered.count('status = "okay";') == 3
    assert rendered.rstrip().endswith(
        "#endif // defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)"
    )


def test_render_gpioc_wifi_hogs_emits_rf_disable_and_pewake_lines():
    rendered = _render_gpioc_wifi_hogs(
        [
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
            _sig("6G_PEWAKE", "PCIE_WIFI", "GPIO_80"),
        ]
    )

    assert "&gpioc {" in rendered
    assert "pin76 {" in rendered
    assert "gpios = <76 GPIO_ACTIVE_LOW>;" in rendered
    assert 'line-name = "2G_RF_DISABLE_L";' in rendered
    assert "pin58 {" in rendered
    assert "gpios = <58 GPIO_ACTIVE_HIGH>;" in rendered
    assert 'line-name = "2G_PEWAKE";' in rendered


def test_compile_direct_renders_per_xphy_lane_swap_under_mdio_bus(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        dts_hints=[
            DtsHint(
                target="&mdio_bus/xphy1",
                property="enet-phy-lane-swap",
                value="GPHY1: Pair 0 swapped",
                reason="Lane swap detected for GPHY1",
                provenance=_prov(),
            )
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert rendered.count("&ethphytop {") == 1
    assert "xphy1-enabled;" in rendered
    assert "&mdio_bus {" in rendered
    assert "xphy1 {" in rendered
    assert "enet-phy-lane-swap;  /* Lane swap traced for GPHY1 */" in rendered
    assert "&ethphytop {\n    xphy1-enabled;\n    enet-phy-lane-swap;" not in rendered


def test_compile_direct_renders_switch0_ports_from_topology_hints(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        dts_hints=[
            DtsHint(
                target="&switch0/ports/port_xgphy0",
                property="status",
                value='"okay"',
                reason="Stable topology row lan_gphy0",
                provenance=_prov(),
            ),
            DtsHint(
                target="&switch0/ports/port_wan@xpon_ae",
                property="status",
                value='"okay"',
                reason="Stable topology row wan_10g",
                provenance=_prov(),
            ),
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")
    parsed = parse_dts_document(output_path)

    assert "&switch0 {" in rendered
    assert "port_xgphy0 {" in rendered
    assert "port_wan@xpon_ae {" in rendered
    assert "/&switch0" in parsed.node_index()


def test_compile_direct_renders_wan_sfp_root_node_and_serdes_reference(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
            _sig("WAN_XCVR_RXEN", "SFP", "GPIO_06"),
            _sig("WAN_SFP_PD_RST", "SFP", "GPIO_52"),
            _sig("WAN_XCVR_TXEN", "SFP", "GPIO_53"),
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "wan_sfp: wan_sfp {" in rendered
    assert "trx = <&wan_sfp>;" in rendered
    assert rendered.index("wan_sfp: wan_sfp {") < rendered.index("&wan_serdes {")


def test_compile_direct_keeps_wan_sfp_i2c_bus_inside_root_node(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
        ],
        dts_hints=[
            DtsHint(
                target="wan_sfp",
                property="i2c-bus",
                value="<&i2c0>",
                reason="SFP page scan proves bus 0",
                provenance=_prov(),
            )
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "wan_sfp: wan_sfp {" in rendered
    assert "i2c-bus = <&i2c0>;" in rendered
    assert "&wan_sfp {" not in rendered


def test_compile_direct_keeps_usb_ctrl_port1_disabled_inside_usb_block(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("USB0_PWRON_N", "USB_POWER", "GPIO_33"),
            _sig("USB1_PWRON", "USB_POWER", "GPIO_31"),
            _sig("USB0_SSRXN", "USB_SUPERSPEED", "GPIO_32"),
        ],
        dts_hints=[
            DtsHint(
                target="&usb_ctrl",
                property="port1-disabled",
                value=None,
                reason="Mainboard USB page shows only USB0 populated",
                provenance=_prov(),
            )
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert rendered.count("&usb_ctrl {") == 1
    assert "port1-disabled;" in rendered


def test_compile_direct_renders_gpioc_wifi_hogs(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
            _sig("6G_PEWAKE", "PCIE_WIFI", "GPIO_80"),
        ],
    )

    output_path = tmp_path / "test.dts"
    asyncio.run(_compile_direct(schema, output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "&gpioc {" in rendered
    assert 'line-name = "2G_RF_DISABLE_L";' in rendered
    assert 'line-name = "6G_PEWAKE";' in rendered


def test_compile_direct_reuses_reference_i2c_gpio_expander_label(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        devices=[
            Device(
                refdes="U41",
                part_number="TCA9555PWR",
                compatible="nxp,pca9555",
                bus="i2c0",
                address="0x27",
                status="VERIFIED",
                provenance=_prov(),
            )
        ],
    )

    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "};",
                "",
                "&i2c0 {",
                '    status = "okay";',
                "",
                "    gpiocext_wlan: gpio@27 {",
                '        compatible = "nxp,pca9555";',
                "        reg = <0x27>;",
                "    };",
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "gpiocext_wlan: gpio@27 {" in rendered
    assert "u41: gpio@27 {" not in rendered


def test_compile_direct_places_gpioc_before_pcie_ifdef_when_reference_orders_pcie_first(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("CPU_VDD_PS_EN", "POWER_CONTROL", "GPIO_90"),
            _sig("1V88_PS_EN", "POWER_CONTROL", "GPIO_89"),
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
            _sig("2G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_76"),
            _sig("5G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_77"),
            _sig("6G_RF_DISABLE_L", "PCIE_WIFI", "GPIO_78"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
            _sig("5G_PEWAKE", "PCIE_WIFI", "GPIO_79"),
            _sig("6G_PEWAKE", "PCIE_WIFI", "GPIO_80"),
            _sig("GPIO_2GRFIC", "GENERAL_GPIO", "GPIO_24"),
            _sig("GPIO_5GRFIC", "GENERAL_GPIO", "GPIO_54"),
            _sig("GPIO_6GRFIC", "GENERAL_GPIO", "GPIO_25"),
        ],
    )

    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "};",
                "",
                "&ext_pwr_ctrl {",
                '    status = "okay";',
                "};",
                "",
                "&pcie0 {",
                '    status = "okay";',
                "};",
                "",
                "&pcie1 {",
                '    status = "okay";',
                "};",
                "",
                "&pcie2 {",
                '    status = "okay";',
                "};",
                "",
                "&gpioc {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert rendered.index("&ext_pwr_ctrl {") < rendered.index("&gpioc {")
    assert rendered.index("&gpioc {") < rendered.index("#if defined(CONFIG_BCM_PCIE_HCD)")


def test_compile_direct_orders_rendered_blocks_by_reference_sequence(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("RST_BTN", "RESET_BUTTON", "GPIO_48"),
            _sig("UART_TX", "UART", "GPIO_12"),
            _sig("WAN_SFP_RX_LOS", "SFP", "GPIO_03"),
            _sig("WAN_SFP_PRESENT", "SFP", "GPIO_04"),
            _sig("WAN_XCVR_RXEN", "SFP", "GPIO_06"),
            _sig("WAN_SFP_PD_RST", "SFP", "GPIO_52"),
            _sig("WAN_XCVR_TXEN", "SFP", "GPIO_53"),
        ],
        dts_hints=[
            DtsHint(
                target="&switch0/ports/port_wan@xpon_ae",
                property="status",
                value='"okay"',
                reason="Stable topology row wan_10g",
                provenance=_prov(),
            ),
        ],
    )
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "    wan_sfp: wan_sfp {",
                '        status = "okay";',
                "    };",
                "    buttons {",
                '        compatible = "brcm,buttons";',
                "    };",
                "};",
                "",
                "&wdt {",
                '    status = "okay";',
                "};",
                "",
                "&uart0 {",
                '    status = "okay";',
                "};",
                "",
                "&switch0 {",
                '    status = "okay";',
                "};",
                "",
                "&xport {",
                '    status = "okay";',
                "};",
                "",
                "&wan_serdes {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert rendered.index("wan_sfp: wan_sfp {") < rendered.index("buttons {")
    assert rendered.index("&wdt {") < rendered.index("&uart0 {")
    assert rendered.index("&uart0 {") < rendered.index("&switch0 {")
    assert rendered.index("&switch0 {") < rendered.index("&xport {")
    assert rendered.index("&xport {") < rendered.index("&wan_serdes {")


def test_compile_direct_retains_reference_sections_as_comments_in_noninteractive_mode(tmp_path):
    schema = HardwareSchema(project="TEST", chip="BCM68575")
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "    lan_sfp: lan_sfp {",
                '        status = "okay";',
                "    };",
                "};",
                "",
                "&serdes {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")
    parsed = parse_dts_document(output_path)

    assert "no direct evidence confirms that this feature is absent on the target board." in rendered
    assert "/* Retained from public reference" in rendered
    assert "&serdes {" in rendered
    assert "/&serdes" not in parsed.node_index()
    assert "lan_sfp: lan_sfp" not in rendered


def test_compile_direct_interactive_mode_respects_user_choice_for_reference_retention(tmp_path):
    schema = HardwareSchema(project="TEST", chip="BCM68575")
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "};",
                "",
                "&serdes {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    questions: list[str] = []

    def _input_handler(request: dict, context: dict | None = None) -> dict:
        questions.append(str(request.get("question", "")))
        return {"answer": "不保留，視為明顯不存在", "wasFreeform": False}

    asyncio.run(
        _compile_direct(
            schema,
            output_path,
            ref_path,
            interactive=True,
            input_handler=_input_handler,
        )
    )
    rendered = output_path.read_text(encoding="utf-8")

    assert questions
    assert "&serdes {" not in rendered


def test_compile_direct_inserts_missing_property_comment_inside_existing_node(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        dts_hints=[
            DtsHint(
                target="&mdio_bus/xphy1",
                property="enet-phy-lane-swap",
                value="GPHY1: Pair 0 swapped",
                reason="Lane swap detected for GPHY1",
                provenance=_prov(),
            )
        ],
    )
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                '/dts-v1/;',
                '',
                '&ethphytop {',
                '    xphy1-enabled;',
                '    xphy3-enabled;',
                '    status = "okay";',
                '};',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")
    parsed = parse_dts_document(output_path)
    eth_node = parsed.node_index()["/&ethphytop"][0]
    eth_block = rendered[rendered.index("&ethphytop {"):rendered.index("};", rendered.index("&ethphytop {")) + 2]

    assert "&ethphytop {" in rendered
    assert "xphy1-enabled;" in rendered
    assert "    xphy3-enabled;" in rendered
    assert eth_block.index("xphy1-enabled;") < eth_block.index("    xphy3-enabled;")
    assert eth_block.index("    xphy3-enabled;") < eth_block.index('    status = "okay";')
    assert "xphy3-enabled" not in eth_node.properties


def test_compile_direct_deduplicates_child_retention_when_parent_node_is_retained(tmp_path):
    schema = HardwareSchema(project="TEST", chip="BCM68575")
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "&pincontroller {",
                "    pincontroller-functions {",
                "        gpio_18_in_pulldown_pin_18: gpio_18_in_pulldown_pin_18_pinconf {",
                "            pins = <18>;",
                "        };",
                "    };",
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")
    parsed = parse_dts_document(output_path)

    assert rendered.count("no direct evidence confirms that this feature is absent on the target board.") == 1
    assert "&pincontroller {" in rendered
    assert "    pincontroller-functions {" in rendered
    assert "/&pincontroller" not in parsed.node_index()


def test_compile_direct_prunes_lan_sfp_children_from_retained_mdio_bus(tmp_path):
    schema = HardwareSchema(project="TEST", chip="BCM68575")
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "&mdio_bus {",
                "    xphy0 {",
                '        status = "okay";',
                "    };",
                "    serdes0 {",
                "        trx = <&wan_sfp>;",
                '        status = "okay";',
                "    };",
                "    serdes1 {",
                "        trx = <&lan_sfp>;",
                '        status = "okay";',
                "    };",
                "};",
                "",
                "&wdt {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "&wdt {" in rendered
    assert "&mdio_bus {" in rendered
    assert "xphy0 {" in rendered
    assert "serdes0 {" in rendered
    assert "serdes1 {" not in rendered
    assert "&lan_sfp" not in rendered


def test_compile_direct_excludes_retained_i2c1_reference_block(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[
            _sig("RBR", "RESET_BUTTON", "GPIO_48"),
            _sig("2G_PEWAKE", "PCIE_WIFI", "GPIO_58"),
        ],
    )
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "&i2c1 {",
                "    pinctrl-0 = <&b_bsc_m1_sda_pin_58 &b_bsc_m1_scl_pin_48>;",
                '    pinctrl-names = "default";',
                '    status = "okay";',
                "};",
                "",
                "&wdt {",
                '    status = "okay";',
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "&wdt {" in rendered
    assert "&i2c1 {" not in rendered
    assert "b_bsc_m1_sda_pin_58" not in rendered


def test_compile_direct_excludes_unmatched_reference_button_blocks(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[_sig("RST_BTN", "RESET_BUTTON", "GPIO_48")],
    )
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "    buttons {",
                '        compatible = "brcm,buttons";',
                "        reset_button {",
                "            ext_irq-gpio = <&gpioc 2 GPIO_ACTIVE_LOW>;",
                "            press {",
                '                print = "Button Press -- Hold for 5s to do restore to default";',
                "            };",
                "        };",
                "        ses_button {",
                "            ext_irq-gpio = <&gpioc 1 GPIO_ACTIVE_LOW>;",
                "            press {",
                '                print = "Session Button pressed";',
                "            };",
                "        };",
                "    };",
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert 'print = "Button Press -- Hold for 5s to do restore to default";' in rendered
    assert "ses_button {" not in rendered


def test_compile_direct_does_not_retain_linux_code_button_property(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        signals=[_sig("RST_BTN", "RESET_BUTTON", "GPIO_48")],
    )
    output_path = tmp_path / "test.dts"
    ref_path = tmp_path / "ref.dts"
    ref_path.write_text(
        "\n".join(
            [
                "/dts-v1/;",
                "",
                "/ {",
                "    buttons {",
                '        compatible = "brcm,buttons";',
                "        reset_button {",
                "            ext_irq-gpio = <&gpioc 48 GPIO_ACTIVE_LOW>;",
                "            linux,code = <0x198>;",
                "            press {",
                '                print = "Button Press -- Hold for 5s to do restore to default";',
                "            };",
                "        };",
                "    };",
                "};",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "linux,code" not in rendered
    assert 'print = "Button Press -- Hold for 5s to do restore to default";' in rendered
