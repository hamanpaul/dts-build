"""Focused tests for dtsbuild.agents.compiler helpers."""

from __future__ import annotations

import asyncio

from dtsbuild.agents.compiler import _compile_direct, _render_ethphy, _render_i2c, _render_power_ctrl
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
    assert "u41: gpio@27 {" in rendered
    assert 'compatible = "nxp,pca9555";' in rendered
    assert "reg = <0x27>;" in rendered


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
