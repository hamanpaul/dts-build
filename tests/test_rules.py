"""Tests for the Public Reference Rule Library.

Each test uses schema objects (Signal, Device, DtsHint) with minimal
Provenance stubs to exercise rule matching and property generation.
"""
from __future__ import annotations

import pytest

from dtsbuild.schema import Signal, Device, DtsHint, Provenance
from dtsbuild.rules.base import SubsystemRule, RuleMatch
from dtsbuild.rules.buttons import ButtonRule
from dtsbuild.rules.uart import UartRule
from dtsbuild.rules.led import LedRule
from dtsbuild.rules.i2c import I2cRule
from dtsbuild.rules.usb import UsbRule
from dtsbuild.rules.pcie import PcieRule
from dtsbuild.rules.serdes import SerdesRule
from dtsbuild.rules.ethernet import EthernetRule
from dtsbuild.rules.power import PowerRule
from dtsbuild.rules.memory import MemoryRule
from dtsbuild.rules.pinctrl import PinctrlRule
from dtsbuild.rules.registry import get_all_rules, get_rule, auto_match


# ── helpers ──────────────────────────────────────────────────────────

def _prov() -> Provenance:
    """Minimal provenance stub."""
    return Provenance(
        pdfs=["test.pdf"],
        pages=[1],
        refs=["U1"],
        method="test",
        confidence=1.0,
    )


def _sig(name: str, role: str, soc_pin: str = "GPIO_10",
         swap_detected: bool | None = None,
         swap_detail: str | None = None) -> Signal:
    return Signal(
        name=name,
        soc_pin=soc_pin,
        traced_path="U1.PinX → J1.Pin1",
        role=role,
        status="VERIFIED",
        swap_detected=swap_detected,
        swap_detail=swap_detail,
        provenance=_prov(),
    )


def _dev(refdes: str, part: str, compatible: str | None = None,
         bus: str | None = None, address: str | None = None) -> Device:
    return Device(
        refdes=refdes,
        part_number=part,
        compatible=compatible,
        bus=bus,
        address=address,
        status="VERIFIED",
        provenance=_prov(),
    )


def _hint(target: str, prop: str | None = None, value: str | None = None,
          reason: str = "test") -> DtsHint:
    return DtsHint(
        target=target,
        property=prop,
        value=value,
        reason=reason,
        provenance=_prov(),
    )


# ── Button tests ─────────────────────────────────────────────────────

class TestButtonRule:

    def test_matches_reset_signal(self):
        rule = ButtonRule()
        sigs = [_sig("RST_BTN", "RESET_BUTTON", "GPIO_2")]
        assert rule.match(sigs, [], [])

    def test_no_match_without_button(self):
        rule = ButtonRule()
        sigs = [_sig("UART0_TX", "DEBUG_UART_TX")]
        assert not rule.match(sigs, [], [])

    def test_generates_gpio_keys(self):
        rule = ButtonRule()
        sigs = [
            _sig("RST_BTN", "RESET_BUTTON", "GPIO_2"),
            _sig("SES_BTN", "SES_BUTTON", "GPIO_1"),
        ]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.subsystem == "buttons"
        assert result.node_name == "buttons"
        assert result.properties["compatible"] == '"brcm,buttons"'
        assert len(result.children) == 2
        # First child is reset_button
        rst = result.children[0]
        assert rst["node_name"] == "reset_button"
        assert "2" in rst["properties"]["ext_irq-gpio"]
        # Reset has press, hold, release
        assert len(rst["children"]) == 3
        assert rst["children"][0]["node_name"] == "press"
        assert rst["children"][1]["node_name"] == "hold"
        assert rst["children"][2]["node_name"] == "release"
        # Second child is ses_button
        ses = result.children[1]
        assert ses["node_name"] == "ses_button"
        assert "1" in ses["properties"]["ext_irq-gpio"]
        # Source attribution
        assert "BCM68575" in result.source

    def test_apply_returns_none_without_signals(self):
        rule = ButtonRule()
        assert rule.apply([], [], []) is None


# ── UART tests ───────────────────────────────────────────────────────

class TestUartRule:

    def test_matches_uart_signal(self):
        rule = UartRule()
        sigs = [_sig("UART0_TX", "DEBUG_UART_TX")]
        assert rule.match(sigs, [], [])

    def test_generates_uart_node(self):
        rule = UartRule()
        sigs = [_sig("UART0_TX", "DEBUG_UART_TX")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&uart0"
        assert result.properties["status"] == '"okay"'


# ── LED tests ────────────────────────────────────────────────────────

class TestLedRule:

    def test_matches_led_signal(self):
        rule = LedRule()
        sigs = [_sig("SER_LED_DATA", "LED_CONTROL")]
        assert rule.match(sigs, [], [])

    def test_matches_shift_register(self):
        rule = LedRule()
        devs = [_dev("U5", "SN74HC595")]
        assert rule.match([], devs, [])

    def test_serial_led_with_shifters(self):
        rule = LedRule()
        sigs = [_sig("WAN_LED", "LED_WAN")]
        devs = [_dev("U5", "SN74HC595"), _dev("U6", "SN74HC595"), _dev("U7", "SN74HC595")]
        result = rule.apply(sigs, devs, [])
        assert result is not None
        assert result.node_name == "&led_ctrl"
        assert result.properties["serial-shifters-installed"] == "<3>"
        assert len(result.children) == 1
        # WAN label detected
        assert result.children[0]["properties"].get("label") == '"WAN"'

    def test_led_control_only_keeps_parent_without_guessing_children(self):
        rule = LedRule()
        sigs = [_sig("SER_LED_DATA", "LED_CONTROL")]
        devs = [_dev("U5", "SN74HC595")]
        result = rule.apply(sigs, devs, [])
        assert result is not None
        assert result.node_name == "&led_ctrl"
        assert result.properties["serial-shifters-installed"] == "<1>"
        assert result.children == []

    def test_no_match_without_led(self):
        rule = LedRule()
        assert not rule.match([], [], [])


# ── I2C tests ────────────────────────────────────────────────────────

class TestI2cRule:

    def test_matches_i2c_device(self):
        rule = I2cRule()
        devs = [_dev("U8", "PCA9555", compatible="nxp,pca9555", bus="i2c0", address="0x27")]
        assert rule.match([], devs, [])

    def test_generates_i2c_with_device(self):
        rule = I2cRule()
        devs = [_dev("U8", "PCA9555", compatible="nxp,pca9555", bus="i2c0", address="0x27")]
        result = rule.apply([], devs, [])
        assert result is not None
        assert result.node_name == "&i2c0"
        assert result.properties["pinctrl-0"] == "<&bsc_m0_scl_pin_28 &bsc_m0_sda_pin_29>"
        assert result.properties["status"] == '"okay"'
        assert len(result.children) == 1
        child = result.children[0]
        assert child["properties"]["compatible"] == '"nxp,pca9555"'
        assert child["properties"]["reg"] == "<0x27>"
        # GPIO expander extras
        assert child["properties"]["#gpio-cells"] == "<2>"
        assert child["properties"].get("gpio-controller") is None  # boolean
        assert "gpio-controller" in child["properties"]

    def test_generates_i2c1_without_i2c0_pinctrl(self):
        rule = I2cRule()
        devs = [_dev("U9", "PCA9555", compatible="nxp,pca9555", bus="i2c1", address="0x20")]
        result = rule.apply([], devs, [])
        assert result is not None
        assert result.node_name == "&i2c1"
        assert "pinctrl-0" not in result.properties

    def test_no_match_without_i2c(self):
        rule = I2cRule()
        assert not rule.match([], [], [])


# ── Ethernet tests ───────────────────────────────────────────────────

class TestEthernetRule:

    def test_matches_ethernet_phy(self):
        rule = EthernetRule()
        sigs = [_sig("GPHY1_DP0", "ETHERNET_PHY_LANE")]
        assert rule.match(sigs, [], [])

    def test_lane_swap_detected(self):
        rule = EthernetRule()
        sigs = [
            _sig("GPHY1_DP0", "ETHERNET_PHY_LANE", "GPHY1_DP0_P",
                 swap_detected=True, swap_detail="DP0↔DP1 at J3"),
        ]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&ethphytop"
        assert "enet-phy-lane-swap" in result.properties
        assert any("Lane swap" in n for n in result.notes)

    def test_no_swap(self):
        rule = EthernetRule()
        sigs = [_sig("GPHY1_DP0", "ETHERNET_PHY_LANE", "GPHY1_DP0_P")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert "enet-phy-lane-swap" not in result.properties

    def test_xphy_enabled(self):
        rule = EthernetRule()
        sigs = [
            _sig("GPHY0_DP0", "ETHERNET_PHY_LANE", "GPHY0_DP0_P"),
            _sig("GPHY3_DP0", "ETHERNET_PHY_LANE", "GPHY3_DP0_P"),
        ]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert "xphy0-enabled" in result.properties
        assert "xphy3-enabled" in result.properties


# ── PCIe tests ───────────────────────────────────────────────────────

class TestPcieRule:

    def test_matches_pcie_signal(self):
        rule = PcieRule()
        sigs = [_sig("PCIE0_RST", "PCIE_RESET", "GPIO_51")]
        assert rule.match(sigs, [], [])

    def test_generates_pcie_regulator(self):
        rule = PcieRule()
        sigs = [
            _sig("PCIE0_RST", "PCIE_RESET", "GPIO_51"),
            _sig("WIFI_PWR_DIS", "WIFI_POWER_DISABLE", "GPIO_11"),
        ]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&pcie0"
        assert result.properties["status"] == '"okay"'
        assert len(result.children) == 1
        # WiFi regulator with ACTIVE_LOW (DIS in name)
        wifi_reg = result.children[0]
        assert "GPIO_ACTIVE_LOW" in wifi_reg["properties"]["gpio"]

    def test_rejects_grouped_wifi_controls_without_full_instance_evidence(self):
        rule = PcieRule()
        sigs = [
            _sig("PCIE02_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_51"),
            _sig("PCIE13_WiFi_PWR_DIS", "PCIE_WIFI", "GPIO_11"),
        ]
        assert rule.apply(sigs, [], []) is None


# ── Power tests ──────────────────────────────────────────────────────

class TestPowerRule:

    def test_matches_power_signal(self):
        rule = PowerRule()
        sigs = [_sig("PS_EN_3V3", "PS_EN", "GPIO_90")]
        assert rule.match(sigs, [], [])

    def test_generates_power_ctrl_gpio(self):
        rule = PowerRule()
        sigs = [_sig("PS_EN_3V3", "PS_EN", "GPIO_90")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&ext_pwr_ctrl"
        assert any("90" in v for v in result.properties.values() if isinstance(v, str))

    def test_phy_power(self):
        rule = PowerRule()
        sigs = [_sig("PHY_PWR_EN", "PWR_CTRL_PHY", "GPIO_89")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert "phy-pwr-ctrl-gpios" in result.properties
        assert "89" in result.properties["phy-pwr-ctrl-gpios"]


# ── USB tests ────────────────────────────────────────────────────────

class TestUsbRule:

    def test_matches_usb_signal(self):
        rule = UsbRule()
        sigs = [_sig("USB0_PWR", "USB_POWER")]
        assert rule.match(sigs, [], [])

    def test_generates_usb_ctrl(self):
        rule = UsbRule()
        sigs = [_sig("USB0_PWR", "USB_POWER")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&usb_ctrl"
        assert "xhci-enable" in result.properties


# ── SerDes tests ─────────────────────────────────────────────────────

class TestSerdesRule:

    def test_matches_wan_signal(self):
        rule = SerdesRule()
        sigs = [_sig("WAN_SFP_TX", "WAN_SFP")]
        assert rule.match(sigs, [], [])

    def test_generates_wan_serdes(self):
        rule = SerdesRule()
        sigs = [_sig("WAN_SFP_TX", "WAN_SFP")]
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert result.node_name == "&wan_serdes"


# ── Memory tests ─────────────────────────────────────────────────────

class TestMemoryRule:

    def test_matches_mem_hint(self):
        rule = MemoryRule()
        hints = [_hint("memory_controller", "memcfg", "<(BP1_DDR_TYPE_LPDDR4)>")]
        assert rule.match([], [], hints)

    def test_no_match_without_hints(self):
        rule = MemoryRule()
        assert not rule.match([], [], [])


# ── Pinctrl tests ────────────────────────────────────────────────────

class TestPinctrlRule:

    def test_matches_pinmux_signal(self):
        rule = PinctrlRule()
        sigs = [_sig("GPIO_18_PULLDOWN", "PINMUX", "GPIO_18")]
        assert rule.match(sigs, [], [])

    def test_matches_pinctrl_hint(self):
        rule = PinctrlRule()
        hints = [_hint("pincontroller", "some_pin", "<18>")]
        assert rule.match([], [], hints)


# ── Registry tests ───────────────────────────────────────────────────

class TestRegistry:

    def test_get_all_rules_returns_list(self):
        rules = get_all_rules()
        assert len(rules) >= 11
        assert all(isinstance(r, SubsystemRule) for r in rules)

    def test_get_rule_by_name(self):
        r = get_rule("buttons")
        assert r is not None
        assert r.subsystem_name == "buttons"

    def test_get_rule_unknown(self):
        assert get_rule("nonexistent") is None

    def test_auto_match_filters(self):
        sigs = [
            _sig("RST_BTN", "RESET_BUTTON", "GPIO_2"),
            _sig("UART0_TX", "DEBUG_UART_TX"),
        ]
        matched = auto_match(sigs, [], [])
        names = {r.subsystem_name for r in matched}
        assert "buttons" in names
        assert "uart" in names
        assert "led" not in names  # no LED signals

    def test_rule_required_evidence(self):
        """Every concrete rule should declare required_evidence."""
        for rule in get_all_rules():
            assert isinstance(rule.required_evidence, list)
            assert len(rule.required_evidence) > 0, (
                f"{rule.subsystem_name} has empty required_evidence"
            )

    def test_rule_source_attribution(self):
        """Every rule apply() result should carry source attribution."""
        sigs = [_sig("RST_BTN", "RESET_BUTTON", "GPIO_2")]
        rule = ButtonRule()
        result = rule.apply(sigs, [], [])
        assert result is not None
        assert "BCM68575" in result.source

    def test_all_rules_have_description(self):
        for rule in get_all_rules():
            assert rule.description, f"{rule.subsystem_name} missing description"
