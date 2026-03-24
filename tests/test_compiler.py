"""Focused tests for dtsbuild.agents.compiler helpers."""

from __future__ import annotations

import asyncio

from dtsbuild.agents.compiler import (
    _compile_direct,
    _render_buttons,
    _render_ethphy,
    _render_i2c,
    _render_pcie,
    _render_power_ctrl,
    _render_wan_sfp,
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


def test_render_buttons_adds_deterministic_reset_button_semantics():
    rendered = _render_buttons([_sig("RST_BTN", "RESET_BUTTON", "GPIO_48")])

    assert "reset_button {" in rendered
    assert "linux,code = <0x198>;" in rendered
    assert 'print = "Button Press -- Hold for 5s to do restore to default";' in rendered
    assert "rst_to_dflt = <5>;" in rendered
    assert "linux,press = <0>;" in rendered
    assert 'print = "Button Release";' in rendered
    assert "reset = <0>;" in rendered
    assert "linux,release = <0>;" in rendered


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


def test_render_ethphy_uses_hints_without_ethernet_phy_signals():
    rendered = _render_ethphy(
        [],
        [
            DtsHint(
                target="ethphytop",
                property="enet-phy-lane-swap",
                value="GPHY0: Pair 0 swapped",
                reason="Lane swap detected for GPHY0",
                provenance=_prov(),
            ),
            DtsHint(
                target="ethphytop",
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
    assert rendered.count("enet-phy-lane-swap;") == 1
    assert "enet-phy-lane-swap =" not in rendered
    assert 'status = "okay";' in rendered


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
    assert "i2c-bus = <&i2c0>;" in rendered
    assert "los-gpio = <&gpioc 3 GPIO_ACTIVE_HIGH>;" in rendered
    assert "mod-def0-gpio = <&gpioc 4 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-power-gpio = <&gpioc 53 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-power-down-gpio = <&gpioc 52 GPIO_ACTIVE_HIGH>;" in rendered
    assert "rx-power-gpio = <&gpioc 6 GPIO_ACTIVE_LOW>;" in rendered
    assert "tx-disable-gpio = <&gpioc 30 GPIO_ACTIVE_HIGH>;" in rendered


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


def test_compile_direct_renders_ethphy_hints_only_once(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        dts_hints=[
            DtsHint(
                target="ethphytop",
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
    assert "enet-phy-lane-swap;" in rendered


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


def test_compile_direct_retains_reference_sections_as_active_code_in_noninteractive_mode(tmp_path):
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

    assert "no direct evidence confirms that this feature is absent on the target board." in rendered
    assert "&wdt {" in rendered
    assert rendered.index("Retained from public reference") < rendered.index("&wdt {")
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
                "&wdt {",
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
    assert "&wdt {" not in rendered


def test_compile_direct_inserts_missing_property_comment_inside_existing_node(tmp_path):
    schema = HardwareSchema(
        project="TEST",
        chip="BCM68575",
        dts_hints=[
            DtsHint(
                target="ethphytop",
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
                '    enet-phy-lane-swap;',
                '    status = "okay";',
                '};',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    asyncio.run(_compile_direct(schema, output_path, ref_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert "&ethphytop {" in rendered
    assert "xphy1-enabled;" in rendered
    assert "    xphy3-enabled;" in rendered
    assert rendered.index("xphy1-enabled;") < rendered.index("    xphy3-enabled;")
    assert rendered.index("    xphy3-enabled;") < rendered.index("enet-phy-lane-swap;")


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

    assert rendered.count("no direct evidence confirms that this feature is absent on the target board.") == 1
    assert "&pincontroller {" in rendered
    assert "    pincontroller-functions {" in rendered
