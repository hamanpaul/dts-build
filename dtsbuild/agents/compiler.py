"""
Agent D: DTS Compiler — 確定性 DTS 產出，只讀 VERIFIED schema record

此 compiler 不看 PDF、不猜測、不問使用者。
每個 DTS node/property 必須可追溯到 schema 中的具體 record。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dtsbuild.pcie_utils import infer_pcie_instances
from dtsbuild.schema import HardwareSchema, Signal, Device, DtsHint
from dtsbuild.schema_io import load_schema
from .refdiff import build_refdiff_report, parse_dts_document

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────

_INDENT = "    "
_ETHPHY_BOOLEAN_HINT_PROPERTIES = frozenset({"enet-phy-lane-swap"})
_RESET_BUTTON_FACTORY_RESET_SECONDS = 5
_RESET_BUTTON_LINUX_CODE = "0x198"
_RESET_BUTTON_PRESS_TEXT = (
    f"Button Press -- Hold for {_RESET_BUTTON_FACTORY_RESET_SECONDS}s "
    "to do restore to default"
)
_RESET_BUTTON_RELEASE_TEXT = "Button Release"
_I2C0_PINCTRL = "<&bsc_m0_scl_pin_28 &bsc_m0_sda_pin_29>"
_REFERENCE_RETENTION_EXCLUDE_PATTERNS = (
    re.compile(r"/lan_sfp(?:/|$)", re.IGNORECASE),
    re.compile(r"/.*voice", re.IGNORECASE),
    re.compile(r"bcm_voice", re.IGNORECASE),
    re.compile(r"slic", re.IGNORECASE),
    re.compile(r"voip", re.IGNORECASE),
)
_REFERENCE_RETENTION_EXCLUDE_SNIPPET_PATTERNS = (
    re.compile(r"\blan_sfp\b", re.IGNORECASE),
    re.compile(r"\bvoice\b", re.IGNORECASE),
    re.compile(r"bcm_voice", re.IGNORECASE),
    re.compile(r"slic", re.IGNORECASE),
    re.compile(r"voip", re.IGNORECASE),
)


def _indent(text: str, level: int = 1) -> str:
    """Indent every non-empty line of *text* by *level* tab stops."""
    prefix = _INDENT * level
    lines = text.split("\n")
    return "\n".join(
        (prefix + line) if line.strip() else line
        for line in lines
    )


def _extract_gpio_num(soc_pin: str) -> str | None:
    """Extract numeric GPIO id from soc_pin like 'GPIO_12' or 'gpio48'."""
    m = re.search(r"(\d+)", soc_pin)
    return m.group(1) if m else None


def _signals_by_role(signals: list[Signal], role: str) -> list[Signal]:
    """Filter verified signals matching a role (case-insensitive contains)."""
    role_upper = role.upper()
    return [
        s for s in signals
        if role_upper in s.role.upper()
    ]


def _find_signal(signals: list[Signal], *names: str) -> Signal | None:
    """Return the first signal whose name matches one of *names* exactly."""
    wanted = {name.upper() for name in names}
    for signal in signals:
        if signal.name.upper() in wanted:
            return signal
    return None


def _extract_ethphy_indices_from_text(text: str | None) -> set[int]:
    """Extract GPHY/XPHY indices mentioned in free-form ethphy evidence."""
    if not text:
        return set()
    return {int(match) for match in re.findall(r"(?:GPHY|XPHY)(\d+)", text.upper())}


def _led_endpoint_signals(signals: list[Signal]) -> list[Signal]:
    """Return LED endpoint signals, excluding LED control bus lines."""
    endpoints: list[Signal] = []
    for sig in signals:
        role_upper = sig.role.upper()
        if role_upper == "LED" or (
            role_upper.startswith("LED_") and role_upper != "LED_CONTROL"
        ):
            endpoints.append(sig)
    return endpoints


# ── Render functions ─────────────────────────────────────────────────

def _render_header(schema: HardwareSchema) -> str:
    """File header comment, /dts-v1/, and #include."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    chip_lower = schema.chip.lower().replace("bcm", "")

    # Map chip to dtsi include path
    dtsi_map = {
        "68575": "inc/68375.dtsi",
        "68375": "inc/68375.dtsi",
        "63146": "inc/63146.dtsi",
        "6756":  "inc/6756.dtsi",
    }
    dtsi = dtsi_map.get(chip_lower, f"inc/{chip_lower}.dtsi")

    lines = [
        f"/dts-v1/;",
        f"",
        f"/*",
        f" * Auto-generated DTS for project \"{schema.project}\"",
        f" * Chip: {schema.chip}",
        f" * Schema version: {schema.version}",
        f" * Generated: {now}",
        f" * Source: dts-build compiler (deterministic, VERIFIED records only)",
        f" */",
        f"",
        f"#include \"{dtsi}\"",
    ]
    return "\n".join(lines)


def _render_root(schema: HardwareSchema) -> str:
    """Root node with model property."""
    lines = [
        "",
        "/ {",
        f'{_INDENT}model = "{schema.project}";',
    ]
    return "\n".join(lines)


def _render_buttons(signals: list[Signal]) -> str:
    """Render buttons { ... } block for RESET_BUTTON / SES_BUTTON signals."""
    reset_signals = _signals_by_role(signals, "RESET_BUTTON")
    ses_signals = _signals_by_role(signals, "SES_BUTTON")

    if not reset_signals and not ses_signals:
        return ""

    lines = [
        "",
        f"{_INDENT}buttons {{",
        f'{_INDENT}{_INDENT}compatible = "brcm,buttons";',
    ]

    for sig in reset_signals:
        gpio = _extract_gpio_num(sig.soc_pin)
        if gpio is None:
            continue
        lines.extend([
            f"{_INDENT}{_INDENT}reset_button {{",
            f"{_INDENT}{_INDENT}{_INDENT}ext_irq-gpio = <&gpioc {gpio} GPIO_ACTIVE_LOW>;",
            f"{_INDENT}{_INDENT}{_INDENT}interrupt-parent = <&gpioc>;",
            f"{_INDENT}{_INDENT}{_INDENT}interrupts = <{gpio} IRQ_TYPE_EDGE_FALLING>;",
            f"{_INDENT}{_INDENT}{_INDENT}linux,code = <{_RESET_BUTTON_LINUX_CODE}>;",
            f"{_INDENT}{_INDENT}{_INDENT}press {{",
            f'{_INDENT}{_INDENT}{_INDENT}{_INDENT}print = "{_RESET_BUTTON_PRESS_TEXT}";',
            f"{_INDENT}{_INDENT}{_INDENT}}};",
            f"{_INDENT}{_INDENT}{_INDENT}hold {{",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}rst_to_dflt = <{_RESET_BUTTON_FACTORY_RESET_SECONDS}>;",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}linux,press = <0>;",
            f"{_INDENT}{_INDENT}{_INDENT}}};",
            f"{_INDENT}{_INDENT}{_INDENT}release {{",
            f'{_INDENT}{_INDENT}{_INDENT}{_INDENT}print = "{_RESET_BUTTON_RELEASE_TEXT}";',
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}reset = <0>;",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}linux,release = <0>;",
            f"{_INDENT}{_INDENT}{_INDENT}}};",
            f"{_INDENT}{_INDENT}}};",
        ])

    for sig in ses_signals:
        gpio = _extract_gpio_num(sig.soc_pin)
        if gpio is None:
            continue
        lines.extend([
            f"{_INDENT}{_INDENT}ses_button {{",
            f"{_INDENT}{_INDENT}{_INDENT}ext_irq-gpio = <&gpioc {gpio} GPIO_ACTIVE_LOW>;",
            f"{_INDENT}{_INDENT}{_INDENT}interrupt-parent = <&gpioc>;",
            f"{_INDENT}{_INDENT}{_INDENT}interrupts = <{gpio} IRQ_TYPE_EDGE_FALLING>;",
            f"{_INDENT}{_INDENT}{_INDENT}press {{",
            f'{_INDENT}{_INDENT}{_INDENT}{_INDENT}print = "Session Button pressed";',
            f"{_INDENT}{_INDENT}{_INDENT}}};",
            f"{_INDENT}{_INDENT}{_INDENT}release {{",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}ses_short_period = <0>;",
            f"{_INDENT}{_INDENT}{_INDENT}{_INDENT}ses_long_period = <3>;",
            f"{_INDENT}{_INDENT}{_INDENT}}};",
            f"{_INDENT}{_INDENT}}};",
        ])

    lines.append(f"{_INDENT}}};")
    return "\n".join(lines)


def _render_wan_sfp(signals: list[Signal]) -> str:
    """Render wan_sfp { ... } in the root node from verified SFP GPIO evidence."""
    los = _find_signal(signals, "WAN_SFP_RX_LOS")
    present = _find_signal(signals, "WAN_SFP_PRESENT")
    tx_power = _find_signal(signals, "WAN_XCVR_TXEN")
    tx_power_down = _find_signal(signals, "WAN_SFP_PD_RST")
    rx_power = _find_signal(signals, "WAN_XCVR_RXEN")

    required = [los, present, tx_power, tx_power_down, rx_power]
    if any(signal is None for signal in required):
        return ""

    gpio_nums = [
        _extract_gpio_num(signal.soc_pin)
        for signal in required
        if signal is not None
    ]
    if len(gpio_nums) != 5 or any(gpio is None for gpio in gpio_nums):
        return ""

    los_gpio, present_gpio, tx_power_gpio, tx_power_down_gpio, rx_power_gpio = [
        int(gpio)
        for gpio in gpio_nums
        if gpio is not None
    ]

    lines = [
        "",
        f"{_INDENT}wan_sfp: wan_sfp {{",
        f'{_INDENT}{_INDENT}pinctrl-names = "default", "tx-sd", "eth";',
        f"{_INDENT}{_INDENT}pinctrl-0 = <&wan0_lbe_pin_30>;",
        f"{_INDENT}{_INDENT}pinctrl-1 = <&wan0_lbe_pin_30 &rogue_onu_in0_pin_27>;",
        f"{_INDENT}{_INDENT}pinctrl-2 = <>;",
        f'{_INDENT}{_INDENT}compatible = "brcm,sfp";',
        f"{_INDENT}{_INDENT}i2c-bus = <&i2c0>;",
        f"{_INDENT}{_INDENT}los-gpio = <&gpioc {los_gpio} GPIO_ACTIVE_HIGH>;",
        f"{_INDENT}{_INDENT}mod-def0-gpio = <&gpioc {present_gpio} GPIO_ACTIVE_LOW>;",
        f"{_INDENT}{_INDENT}tx-power-gpio = <&gpioc {tx_power_gpio} GPIO_ACTIVE_LOW>;",
        f"{_INDENT}{_INDENT}tx-power-down-gpio = <&gpioc {tx_power_down_gpio} GPIO_ACTIVE_HIGH>;",
        f"{_INDENT}{_INDENT}rx-power-gpio = <&gpioc {rx_power_gpio} GPIO_ACTIVE_LOW>;",
        f"{_INDENT}{_INDENT}tx-disable-gpio = <&gpioc 30 GPIO_ACTIVE_HIGH>;",
        f'{_INDENT}{_INDENT}status = "okay";',
        f"{_INDENT}}};",
    ]
    return "\n".join(lines)


def _render_uart(signals: list[Signal]) -> str:
    """Render &uart0 { status = "okay"; }; for UART signals."""
    uart_signals = _signals_by_role(signals, "UART")
    if not uart_signals:
        return ""

    # Determine which UART instances are used
    instances: set[str] = set()
    for sig in uart_signals:
        m = re.search(r"uart(\d+)", sig.name, re.IGNORECASE)
        if m:
            instances.add(m.group(1))
        else:
            instances.add("0")

    lines = []
    for inst in sorted(instances):
        lines.extend([
            "",
            f"&uart{inst} {{",
            f'{_INDENT}status = "okay";',
            "};",
        ])
    return "\n".join(lines)


def _render_wdt(signals: list[Signal]) -> str:
    """Render &wdt { status = "okay"; }; for WATCHDOG signals."""
    wdt_signals = _signals_by_role(signals, "WATCHDOG")
    if not wdt_signals:
        return ""
    return "\n".join([
        "",
        "&wdt {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_hsspi(signals: list[Signal]) -> str:
    """Render &hsspi { status = "okay"; }; for verified SPI bus signals."""
    hsspi_signals = [
        sig for sig in signals
        if sig.role.upper() == "SPI" or sig.name.upper().startswith("SPIS_")
    ]
    if not hsspi_signals:
        return ""
    return "\n".join([
        "",
        "&hsspi {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_led_ctrl(signals: list[Signal], devices: list[Device]) -> str:
    """Render &led_ctrl { ... } from verified LED bus evidence."""
    led_signals = _led_endpoint_signals(signals)
    shift_regs = [d for d in devices if "74HC595" in d.part_number.upper() or "595" in d.part_number]
    led_control_signals = _signals_by_role(signals, "LED_CONTROL")

    if not led_signals and not shift_regs and not led_control_signals:
        return ""

    lines = [
        "",
        "&led_ctrl {",
        f'{_INDENT}pinctrl-names = "default";',
        f'{_INDENT}pinctrl-0 = <&c_ser_led_data_pin_55 &c_ser_led_clk_pin_56 &c_ser_led_mask_pin_57>;',
    ]

    if shift_regs:
        num_shifters = len(shift_regs)
        lines.append(f"{_INDENT}serial-shifters-installed = <{num_shifters}>;")

    # Emit individual LED nodes from LED signals
    for i, sig in enumerate(led_signals):
        led_name = sig.name.lower().replace(" ", "_")
        lines.extend([
            "",
            f"{_INDENT}led{i}: serial-{led_name} {{",
            f"{_INDENT}{_INDENT}active_low;",
            f"{_INDENT}{_INDENT}crossbar-output = <{i}>;",
            f'{_INDENT}{_INDENT}status = "okay";',
            f"{_INDENT}}};",
        ])

    lines.extend([
        "",
        f'{_INDENT}status = "okay";',
        "};",
    ])
    return "\n".join(lines)


def _render_ethphy(signals: list[Signal], hints: list[DtsHint]) -> str:
    """Render &ethphytop { ... } for ETHERNET_PHY signals."""
    eth_signals = _signals_by_role(signals, "ETHERNET_PHY")
    ethphy_hints = [h for h in hints if h.target in ("ethphytop", "&ethphytop")]
    if not eth_signals and not ethphy_hints:
        return ""

    lines = [
        "",
        "&ethphytop {",
    ]

    # Enable xphy ports based on signals
    enabled_phys: set[int] = set()
    for sig in eth_signals:
        m = re.search(r"(\d+)", sig.name)
        if m:
            enabled_phys.add(int(m.group(1)))
    for hint in ethphy_hints:
        enabled_phys.update(_extract_ethphy_indices_from_text(hint.value))
        enabled_phys.update(_extract_ethphy_indices_from_text(hint.reason))

    for phy_idx in sorted(enabled_phys):
        lines.append(f"{_INDENT}xphy{phy_idx}-enabled;")

    lane_swap_from_hints = False
    for hint in ethphy_hints:
        if not hint.property:
            continue
        if hint.property in _ETHPHY_BOOLEAN_HINT_PROPERTIES:
            lane_swap_from_hints = True
            continue
        if hint.value:
            lines.append(f"{_INDENT}{hint.property} = {hint.value};")
        else:
            lines.append(f"{_INDENT}{hint.property};")

    # Check signals for swap_detected
    lane_swap_comment = None
    for sig in eth_signals:
        if sig.swap_detected:
            lane_swap_comment = sig.swap_detail or sig.name
            break
    if lane_swap_comment is None and lane_swap_from_hints:
        hinted_phys = sorted({
            idx
            for hint in ethphy_hints
            for idx in (
                _extract_ethphy_indices_from_text(hint.value)
                | _extract_ethphy_indices_from_text(hint.reason)
            )
        })
        if hinted_phys:
            lane_swap_comment = "Lane swap traced for " + ", ".join(
                f"GPHY{idx}" for idx in hinted_phys
            )
    if lane_swap_comment or lane_swap_from_hints:
        comment = f"  /* {lane_swap_comment} */" if lane_swap_comment else ""
        lines.append(f"{_INDENT}enet-phy-lane-swap;{comment}")

    lines.extend([
        f'{_INDENT}status = "okay";',
        "};",
    ])
    return "\n".join(lines)


def _render_i2c(signals: list[Signal], devices: list[Device]) -> str:
    """Render &i2c0/i2c1 { ... } with device sub-nodes."""
    i2c_signals = _signals_by_role(signals, "I2C")
    i2c_devices = [d for d in devices if d.bus and "i2c" in d.bus.lower()]

    if not i2c_signals and not i2c_devices:
        return ""

    # Group devices by bus
    bus_devices: dict[str, list[Device]] = {}
    for dev in i2c_devices:
        bus = dev.bus or "i2c0"
        bus_devices.setdefault(bus, []).append(dev)

    # Determine which I2C buses are used
    buses: set[str] = set()
    for sig in i2c_signals:
        m = re.search(r"i2c(\d+)", sig.name, re.IGNORECASE)
        if m:
            buses.add(f"i2c{m.group(1)}")
        else:
            buses.add("i2c0")
    for bus in bus_devices:
        buses.add(bus)

    output_parts = []

    for bus in sorted(buses):
        lines = [
            "",
            f"&{bus} {{",
            f'{_INDENT}pinctrl-names = "default";',
        ]
        if bus == "i2c0":
            lines.append(f"{_INDENT}pinctrl-0 = {_I2C0_PINCTRL};")
        lines.append(f'{_INDENT}status = "okay";')

        for dev in bus_devices.get(bus, []):
            if dev.dnp:
                continue
            compat = dev.compatible or f"unknown,{dev.part_number.lower()}"
            addr = dev.address or "0x00"
            addr_hex = addr.replace("0x", "")
            label = dev.refdes.lower()

            lines.extend([
                "",
                f"{_INDENT}{label}: gpio@{addr_hex} {{",
                f'{_INDENT}{_INDENT}compatible = "{compat}";',
                f"{_INDENT}{_INDENT}reg = <{addr}>;",
                f"{_INDENT}{_INDENT}#gpio-cells = <2>;",
                f"{_INDENT}{_INDENT}gpio-controller;",
                f"{_INDENT}{_INDENT}polarity = <0x00>;",
                f"{_INDENT}}};",
            ])

        lines.append("};")
        output_parts.append("\n".join(lines))

    return "\n".join(output_parts)


def _render_usb(signals: list[Signal]) -> str:
    """Render &usb_ctrl { ... } for USB signals."""
    usb_signals = _signals_by_role(signals, "USB")
    if not usb_signals:
        return ""

    lines = [
        "",
        "&usb_ctrl {",
        f'{_INDENT}pinctrl-names = "default";',
        f"{_INDENT}pinctrl-0 = <&usb0_pwr_pins &usb1_pwr_pins>;",
        f"{_INDENT}xhci-enable;",
        f'{_INDENT}status = "okay";',
        "};",
        "",
        "&usb0_xhci {",
        f'{_INDENT}status = "okay";',
        "",
        f"{_INDENT}usb_port1: port1 {{",
        f"{_INDENT}{_INDENT}reg = <1>;",
        f"{_INDENT}{_INDENT}#trigger-source-cells = <0>;",
        f"{_INDENT}}};",
        "",
        f"{_INDENT}usb_port2: port2 {{",
        f"{_INDENT}{_INDENT}reg = <2>;",
        f"{_INDENT}{_INDENT}#trigger-source-cells = <0>;",
        f"{_INDENT}}};",
        "};",
    ]
    return "\n".join(lines)


def _render_pcie(signals: list[Signal]) -> str:
    """Render &pcie0/1/2 { ... } for evidence-backed PCIe/Wi-Fi signals."""
    instances = infer_pcie_instances(sig.name for sig in signals)
    if not instances:
        return ""

    lines = []
    for inst in sorted(instances):
        lines.extend([
            "",
            f"&pcie{inst} {{",
            f'{_INDENT}status = "okay";',
            "};",
        ])
    return "\n".join(lines)


def _render_serdes(signals: list[Signal]) -> str:
    """Render &wan_serdes { ... } for SFP / SERDES signals."""
    sfp_signals = _signals_by_role(signals, "SFP")
    serdes_signals = _signals_by_role(signals, "SERDES")
    all_signals = sfp_signals + serdes_signals

    if not all_signals:
        return ""

    lines = [
        "",
        "&wan_serdes {",
        f'{_INDENT}status = "okay";',
    ]

    # Group SFP signals by serdes instance
    serdes_instances: dict[str, list[Signal]] = {}
    for sig in all_signals:
        m = re.search(r"(\d+)", sig.name)
        inst = m.group(1) if m else "0"
        serdes_instances.setdefault(inst, []).append(sig)

    for inst in sorted(serdes_instances):
        lines.extend([
            "",
            f"{_INDENT}serdes{inst} {{",
            f"{_INDENT}{_INDENT}trx = <&wan_sfp>;",
            f"{_INDENT}}};",
        ])

    lines.append("};")
    return "\n".join(lines)


def _render_power_ctrl(signals: list[Signal]) -> str:
    """Render power control nodes."""
    pwr_signals = _signals_by_role(signals, "POWER")
    if not pwr_signals:
        return ""

    primary = _pick_primary_power_signal(pwr_signals)
    remaining = [sig for sig in pwr_signals if sig is not primary]
    phy = _pick_phy_power_signal(remaining)
    extras = [sig for sig in remaining if sig is not phy]

    properties: list[str] = []

    if primary is not None:
        primary_gpio = _extract_gpio_num(primary.soc_pin)
        if primary_gpio is not None:
            properties.append(
                f"{_INDENT}pwr-ctrl-0-gpios = <&gpioc {primary_gpio} GPIO_ACTIVE_HIGH>;"
            )

    if phy is not None:
        phy_gpio = _extract_gpio_num(phy.soc_pin)
        if phy_gpio is not None:
            properties.append(
                f"{_INDENT}phy-pwr-ctrl-gpios = <&gpioc {phy_gpio} GPIO_ACTIVE_HIGH>;"
            )

    for idx, sig in enumerate(extras, start=1):
        gpio = _extract_gpio_num(sig.soc_pin)
        if gpio is None:
            continue
        properties.append(
            f"{_INDENT}pwr-ctrl-{idx}-gpios = <&gpioc {gpio} GPIO_ACTIVE_HIGH>;"
        )

    if not properties:
        return ""

    lines = [
        "",
        "&ext_pwr_ctrl {",
        *properties,
        f'{_INDENT}status = "okay";',
        "};",
    ]
    return "\n".join(lines)


def _pick_primary_power_signal(signals: list[Signal]) -> Signal | None:
    """Pick the most likely primary board power-enable signal."""
    if not signals:
        return None

    def priority(sig: Signal) -> tuple[int, str]:
        label = f"{sig.name} {sig.role}".upper()
        score = 0
        if "CPU" in label:
            score += 100
        if "VDD" in label or "CORE" in label:
            score += 50
        if "PHY" in label:
            score -= 25
        return (score, sig.name)

    return max(signals, key=priority)


def _pick_phy_power_signal(signals: list[Signal]) -> Signal | None:
    """Pick the most likely PHY/peripheral power-enable signal."""
    if not signals:
        return None

    explicit = [
        sig for sig in signals
        if "PHY" in f"{sig.name} {sig.role}".upper()
    ]
    if explicit:
        return explicit[0]

    if len(signals) == 1:
        return signals[0]
    return None


def _render_dts_hints(hints: list[DtsHint], already_rendered: set[str]) -> str:
    """Render DTS hints that haven't been covered by other render functions."""
    remaining = [h for h in hints if h.target not in already_rendered]
    if not remaining:
        return ""

    # Group by target
    by_target: dict[str, list[DtsHint]] = {}
    for h in remaining:
        by_target.setdefault(h.target, []).append(h)

    lines = []
    for target, target_hints in sorted(by_target.items()):
        ref = target if target.startswith("&") else f"&{target}"
        lines.extend([
            "",
            f"{ref} {{",
        ])
        for h in target_hints:
            if h.property and h.value:
                lines.append(f"{_INDENT}{h.property} = {h.value};  /* {h.reason} */")
            elif h.property:
                lines.append(f"{_INDENT}{h.property};  /* {h.reason} */")
            else:
                lines.append(f"{_INDENT}/* hint: {h.reason} */")
        lines.extend([
            f'{_INDENT}status = "okay";',
            "};",
        ])
    return "\n".join(lines)


def _render_incomplete_comments(schema: HardwareSchema) -> str:
    """Add TODO comments for unresolved items."""
    incomplete_sigs = [s for s in schema.signals if s.status in ("INCOMPLETE", "AMBIGUOUS")]
    incomplete_devs = [d for d in schema.devices if d.status in ("INCOMPLETE", "AMBIGUOUS")]
    pending_crs = schema.pending_clarifications()

    if not incomplete_sigs and not incomplete_devs and not pending_crs:
        return ""

    lines = [
        "",
        "/*",
        " * ============================================================",
        " * TODO: Unresolved items — requires manual review",
        " * ============================================================",
    ]

    if incomplete_sigs:
        lines.append(" *")
        lines.append(f" * Unresolved signals ({len(incomplete_sigs)}):")
        for sig in incomplete_sigs:
            lines.append(
                f" *   - [{sig.status}] {sig.name} (role={sig.role}, "
                f"pin={sig.soc_pin}, confidence={sig.provenance.confidence})"
            )

    if incomplete_devs:
        lines.append(" *")
        lines.append(f" * Unresolved devices ({len(incomplete_devs)}):")
        for dev in incomplete_devs:
            lines.append(
                f" *   - [{dev.status}] {dev.refdes} ({dev.part_number}, "
                f"bus={dev.bus or 'N/A'}, addr={dev.address or 'N/A'})"
            )

    if pending_crs:
        lines.append(" *")
        lines.append(f" * Pending clarifications ({len(pending_crs)}):")
        for cr in pending_crs:
            blocking_str = "BLOCKING" if cr.blocking else "non-blocking"
            lines.append(f" *   - [{blocking_str}] {cr.id}: {cr.question}")

    lines.append(" */")
    return "\n".join(lines)


def _should_exclude_reference_target(target: str) -> bool:
    """Return True for reference-only targets that are clearly absent on this board."""
    return any(pattern.search(target) for pattern in _REFERENCE_RETENTION_EXCLUDE_PATTERNS)


def _retain_reference_candidate(
    target: str,
    interactive: bool,
    input_handler: Callable | None,
) -> bool:
    """Decide whether a reference-only section should be kept as a comment."""
    if not interactive or input_handler is None:
        return True

    response = input_handler(
        {
            "question": (
                f"公版 DTS 的區段 '{target}' 目前沒有本地證據證明不存在；"
                "互動模式下是否保留為註解區段？"
            ),
            "choices": [
                "保留為註解",
                "不保留，視為明顯不存在",
                "先跳過，不保留",
            ],
            "allowFreeform": True,
        }
    )
    answer = str((response or {}).get("answer", "")).strip().lower()
    if any(token in answer for token in ("不保留", "skip", "跳過", "no")):
        return False
    return any(token in answer for token in ("保留", "keep", "retain", "yes", "是"))


def _extract_reference_snippet(
    reference_lines: list[str],
    reference_doc: Any,
    target: str,
) -> list[str]:
    """Extract a stable node/property snippet from the reference DTS."""
    node_index = reference_doc.node_index()

    if ":" in target:
        node_path, prop_name = target.rsplit(":", 1)
        ref_nodes = node_index.get(node_path, [])
        if not ref_nodes:
            return []
        prop = ref_nodes[0].properties.get(prop_name)
        if prop is None:
            return []
        return [reference_lines[prop.line - 1]]

    ref_nodes = node_index.get(target, [])
    if not ref_nodes:
        return []
    node = ref_nodes[0]
    if node.end_line is None:
        return []
    return reference_lines[node.start_line - 1:node.end_line]


def _target_node_path(target: str) -> str:
    if ":" in target:
        return target.rsplit(":", 1)[0]
    return target


def _should_exclude_reference_snippet(snippet: list[str]) -> bool:
    snippet_text = "\n".join(snippet)
    return any(pattern.search(snippet_text) for pattern in _REFERENCE_RETENTION_EXCLUDE_SNIPPET_PATTERNS)


def _reference_target_line(reference_doc: Any, target: str) -> int | None:
    node_index = reference_doc.node_index()
    if ":" in target:
        node_path, prop_name = target.rsplit(":", 1)
        ref_nodes = node_index.get(node_path, [])
        if not ref_nodes:
            return None
        prop = ref_nodes[0].properties.get(prop_name)
        return prop.line if prop is not None else None

    ref_nodes = node_index.get(target, [])
    if not ref_nodes:
        return None
    return ref_nodes[0].start_line


def _parent_node_path(path: str) -> str | None:
    if path == "/":
        return None
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def _is_descendant_path(path: str, ancestor: str) -> bool:
    if path == ancestor:
        return True
    if ancestor == "/":
        return path.startswith("/") and path != "/"
    return path.startswith(f"{ancestor}/")


def _select_reference_retention_candidates(
    report: Any,
    reference_doc: Any,
    interactive: bool,
    input_handler: Callable | None,
) -> list[Any]:
    candidates = [
        candidate
        for candidate in report.candidates
        if candidate.candidate_type in {"missing_node", "unsupported_surface", "missing_property"}
        and candidate.route_hint in {"renderer", "capability"}
        and not _should_exclude_reference_target(candidate.target)
    ]
    if not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            _reference_target_line(reference_doc, candidate.target) or 10**9,
            candidate.target,
        ),
    )
    retained: list[Any] = []
    retained_node_paths: list[str] = []

    for candidate in ordered:
        target_path = _target_node_path(candidate.target)
        if any(_is_descendant_path(target_path, ancestor) for ancestor in retained_node_paths):
            continue
        if not _retain_reference_candidate(candidate.target, interactive, input_handler):
            continue
        retained.append(candidate)
        if candidate.candidate_type in {"missing_node", "unsupported_surface"}:
            retained_node_paths.append(target_path)

    return retained


def _build_inline_retention_block(candidate: Any, snippet: list[str]) -> list[str]:
    source = candidate.reference_locator or "public reference"
    lines = [
        "",
        (
            f"// Retained from public reference ({source}): "
            "no direct evidence confirms that this feature is absent on the target board."
        ),
    ]
    for snippet_line in snippet:
        lines.append(f"// {snippet_line}" if snippet_line else "//")
    return lines


def _find_property_insertion_line(
    generated_doc: Any,
    reference_doc: Any,
    target: str,
) -> int | None:
    if ":" not in target:
        return None

    node_path, prop_name = target.rsplit(":", 1)
    generated_nodes = generated_doc.node_index().get(node_path, [])
    reference_nodes = reference_doc.node_index().get(node_path, [])
    if not generated_nodes or not reference_nodes:
        return None

    generated_node = generated_nodes[0]
    reference_node = reference_nodes[0]
    reference_prop = reference_node.properties.get(prop_name)
    if reference_prop is None:
        return None

    next_generated_prop_line: int | None = None
    for sibling_name, sibling_prop in sorted(
        reference_node.properties.items(),
        key=lambda item: item[1].line,
    ):
        if sibling_prop.line <= reference_prop.line:
            continue
        generated_prop = generated_node.properties.get(sibling_name)
        if generated_prop is None:
            continue
        next_generated_prop_line = generated_prop.line
        break

    if next_generated_prop_line is not None:
        return next_generated_prop_line
    return generated_node.end_line


def _find_node_insertion_line(
    generated_doc: Any,
    reference_doc: Any,
    target: str,
    total_lines: int,
) -> int:
    target_path = _target_node_path(target)
    parent_path = _parent_node_path(target_path)
    if parent_path is None:
        return total_lines + 1

    reference_index = reference_doc.node_index()
    generated_index = generated_doc.node_index()
    reference_nodes = reference_index.get(target_path, [])
    if not reference_nodes:
        return total_lines + 1
    reference_node = reference_nodes[0]

    sibling_nodes = sorted(
        [
            node
            for nodes in reference_index.values()
            for node in nodes
            if _parent_node_path(node.path) == parent_path
        ],
        key=lambda node: node.start_line,
    )

    for sibling in sibling_nodes:
        if sibling.path == target_path or sibling.start_line <= reference_node.start_line:
            continue
        generated_nodes = generated_index.get(sibling.path, [])
        if generated_nodes:
            return generated_nodes[0].start_line

    for sibling in reversed(sibling_nodes):
        if sibling.path == target_path or sibling.start_line >= reference_node.start_line:
            continue
        generated_nodes = generated_index.get(sibling.path, [])
        if generated_nodes:
            prev_node = generated_nodes[0]
            return (prev_node.end_line or prev_node.start_line) + 1

    parent_nodes = generated_index.get(parent_path, [])
    if parent_nodes:
        return parent_nodes[0].end_line or (total_lines + 1)
    return total_lines + 1


def _apply_inline_reference_retention(
    generated_dts_path: Path,
    ref_dts_path: Path | None,
    interactive: bool,
    input_handler: Callable | None,
) -> str:
    """Insert retained public-reference snippets as non-executing review context."""
    if ref_dts_path is None or not ref_dts_path.exists():
        return generated_dts_path.read_text(encoding="utf-8")

    report = build_refdiff_report(
        project=generated_dts_path.stem,
        generated_dts_path=generated_dts_path,
        reference_dts_path=ref_dts_path,
    )
    reference_doc = parse_dts_document(ref_dts_path)
    candidates = _select_reference_retention_candidates(
        report=report,
        reference_doc=reference_doc,
        interactive=interactive,
        input_handler=input_handler,
    )
    if not candidates:
        return generated_dts_path.read_text(encoding="utf-8")

    generated_content = generated_dts_path.read_text(encoding="utf-8")
    generated_lines = generated_content.splitlines()
    generated_doc = parse_dts_document(generated_dts_path)
    reference_lines = ref_dts_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    insertions: list[tuple[int, int, list[str]]] = []

    for candidate in candidates:
        snippet = _extract_reference_snippet(reference_lines, reference_doc, candidate.target)
        if not snippet:
            continue
        if _should_exclude_reference_snippet(snippet):
            continue

        if candidate.candidate_type == "missing_property":
            insertion_line = _find_property_insertion_line(
                generated_doc=generated_doc,
                reference_doc=reference_doc,
                target=candidate.target,
            )
        else:
            insertion_line = _find_node_insertion_line(
                generated_doc=generated_doc,
                reference_doc=reference_doc,
                target=candidate.target,
                total_lines=len(generated_lines),
            )
        if insertion_line is None:
            continue

        insertions.append(
            (
                insertion_line,
                _reference_target_line(reference_doc, candidate.target) or 10**9,
                _build_inline_retention_block(candidate, snippet),
            )
        )

    if not insertions:
        return generated_content

    for insertion_line, reference_line, block in sorted(
        insertions,
        key=lambda item: (item[0], item[1]),
        reverse=True,
    ):
        insert_at = max(0, min(insertion_line - 1, len(generated_lines)))
        generated_lines[insert_at:insert_at] = block

    return "\n".join(generated_lines) + "\n"


# ── Main compile logic ───────────────────────────────────────────────

async def _compile_direct(
    schema: HardwareSchema,
    output_path: Path,
    ref_dts_path: Path | None = None,
    *,
    interactive: bool = False,
    input_handler: Callable | None = None,
) -> Path:
    """Build the DTS file from VERIFIED schema records."""
    verified_sigs = schema.verified_signals()
    verified_devs = schema.verified_devices()
    all_hints = schema.dts_hints

    logger.info(
        "Compiling DTS: %d verified signals, %d verified devices, %d hints",
        len(verified_sigs), len(verified_devs), len(all_hints),
    )

    parts: list[str] = []

    # 1. Header
    parts.append(_render_header(schema))

    # 2. Root node (open)
    parts.append(_render_root(schema))

    # 3. Buttons inside root node
    buttons_block = _render_buttons(verified_sigs)
    if buttons_block:
        parts.append(buttons_block)

    wan_sfp_block = _render_wan_sfp(verified_sigs)
    if wan_sfp_block:
        parts.append(wan_sfp_block)

    # Close root node
    parts.append("};")

    # Track which hint targets are already rendered by specific renderers
    rendered_targets: set[str] = set()

    # 4. Subsystem overlay nodes (outside root)
    uart_block = _render_uart(verified_sigs)
    if uart_block:
        parts.append(uart_block)

    wdt_block = _render_wdt(verified_sigs)
    if wdt_block:
        parts.append(wdt_block)

    hsspi_block = _render_hsspi(verified_sigs)
    if hsspi_block:
        parts.append(hsspi_block)

    led_block = _render_led_ctrl(verified_sigs, verified_devs)
    if led_block:
        parts.append(led_block)

    ethphy_block = _render_ethphy(verified_sigs, all_hints)
    if ethphy_block:
        parts.append(ethphy_block)
        rendered_targets.update({"ethphytop", "&ethphytop"})

    i2c_block = _render_i2c(verified_sigs, verified_devs)
    if i2c_block:
        parts.append(i2c_block)

    usb_block = _render_usb(verified_sigs)
    if usb_block:
        parts.append(usb_block)

    pcie_block = _render_pcie(verified_sigs)
    if pcie_block:
        parts.append(pcie_block)

    serdes_block = _render_serdes(verified_sigs)
    if serdes_block:
        parts.append(serdes_block)

    pwr_block = _render_power_ctrl(verified_sigs)
    if pwr_block:
        parts.append(pwr_block)

    # 5. Remaining DTS hints
    hints_block = _render_dts_hints(all_hints, rendered_targets)
    if hints_block:
        parts.append(hints_block)

    # 6. TODO comments for incomplete items
    todo_block = _render_incomplete_comments(schema)
    if todo_block:
        parts.append(todo_block)

    # Assemble and write the evidence-only base DTS first.
    dts_content = "\n".join(parts) + "\n"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(dts_content, encoding="utf-8")

    dts_content = _apply_inline_reference_retention(
        generated_dts_path=output_path,
        ref_dts_path=ref_dts_path,
        interactive=interactive,
        input_handler=input_handler,
    )
    output_path.write_text(dts_content, encoding="utf-8")

    logger.info("DTS written to %s (%d bytes)", output_path, len(dts_content))
    return output_path


# ── Public entry point ───────────────────────────────────────────────

async def run_compiler(
    schema_path: Path,
    output_path: Path,
    ref_dts_path: Path | None = None,
    mode: str = "direct",
    *,
    interactive: bool = False,
    input_handler: Callable | None = None,
) -> Path:
    """
    從 VERIFIED schema record 產出 DTS 檔案。

    Args:
        schema_path: Hardware schema YAML 路徑（只讀）
        output_path: DTS 輸出路徑
        ref_dts_path: Public reference DTS（用於結構參考，非 answer key）
        mode: "direct" (default)

    Returns:
        產出的 DTS 檔案路徑
    """
    schema_path = Path(schema_path)
    output_path = Path(output_path)

    schema = load_schema(schema_path)

    verified_count = len(schema.verified_signals()) + len(schema.verified_devices())
    if verified_count == 0:
        logger.warning(
            "No VERIFIED records in schema — generating minimal DTS with TODO comments only."
        )

    if mode == "direct":
        return await _compile_direct(
            schema,
            output_path,
            ref_dts_path,
            interactive=interactive,
            input_handler=input_handler,
        )
    else:
        raise ValueError(f"Unsupported compiler mode: {mode!r}")
