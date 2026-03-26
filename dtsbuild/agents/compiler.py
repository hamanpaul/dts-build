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
_I2C0_PINCTRL = "<&bsc_m0_scl_pin_28 &bsc_m0_sda_pin_29>"
_RESET_BUTTON_BEHAVIOR_LINES = (
    "press {",
    '    print = "Button Press -- Hold for 5s to do restore to default";',
    "};",
    "hold {",
    "    rst_to_dflt = <5>;",
    "};",
    "release {",
    "    reset = <0>;",
    "};",
)
_SES_BUTTON_BEHAVIOR_LINES = (
    "press {",
    '    print = "Session Button pressed";',
    "};",
    "release {",
    "    ses_short_period = <0>;",
    "    ses_long_period = <3>;",
    "};",
)
_BUILTIN_WDT_CHIPS = frozenset({"BCM68375", "BCM68575"})
_BUILTIN_CPUFREQ_CHIPS = frozenset({"BCM68375", "BCM68575"})
_BUILTIN_XPORT_CHIPS = frozenset({"BCM68375", "BCM68575"})
_BUILTIN_MDIO_CHIPS = frozenset({"BCM68375", "BCM68575"})
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
_PCIE_REGULATOR_SIGNAL_BY_INSTANCE = {
    0: "GPIO_5GRFIC",
    1: "GPIO_6GRFIC",
    2: "GPIO_2GRFIC",
}
_PCIE_REGULATOR_POLARITY = "GPIO_ACTIVE_LOW"


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


def _button_signal_groups(signals: list[Signal]) -> tuple[list[Signal], list[Signal]]:
    """Return reset/ses button groups, falling back to generic BUTTON evidence."""
    reset_signals = _signals_by_role(signals, "RESET_BUTTON")
    ses_signals = _signals_by_role(signals, "SES_BUTTON")
    if reset_signals or ses_signals:
        return reset_signals, ses_signals

    generic = _signals_by_role(signals, "BUTTON")
    if not generic:
        return [], []

    reset_signals = [s for s in generic if "reset" in s.name.lower()]
    ses_signals = [s for s in generic if "ses" in s.name.lower() or "wps" in s.name.lower()]
    if not reset_signals and not ses_signals:
        reset_signals = generic
    return reset_signals, ses_signals


def _button_behavior_lines(template: tuple[str, ...]) -> list[str]:
    return [f"{_INDENT}{_INDENT}{_INDENT}{line}" for line in template]


def _normalized_chip_name(chip: str) -> str:
    return chip.strip().upper()


def _chip_supports_builtin_node(chip: str, supported_chips: frozenset[str]) -> bool:
    return _normalized_chip_name(chip) in supported_chips


def _infer_usb_ports(signals: list[Signal]) -> list[int]:
    ports: set[int] = set()
    for signal in signals:
        match = re.search(r"USB(\d+)", signal.name, re.IGNORECASE)
        if not match:
            continue
        ports.add(int(match.group(1)) + 1)
    return sorted(ports)


def _infer_uart_instances(signals: list[Signal]) -> list[str]:
    uart_signals = _signals_by_role(signals, "UART")
    if not uart_signals:
        return []

    instances: set[str] = set()
    for sig in uart_signals:
        match = re.search(r"uart(\d+)", sig.name, re.IGNORECASE)
        if match:
            instances.add(match.group(1))
        else:
            instances.add("0")
    return sorted(instances)


def _infer_i2c_buses(signals: list[Signal], devices: list[Device]) -> list[str]:
    i2c_signals = _signals_by_role(signals, "I2C")
    i2c_devices = [d for d in devices if d.bus and "i2c" in d.bus.lower()]
    if not i2c_signals and not i2c_devices:
        return []

    buses: set[str] = set()
    for sig in i2c_signals:
        match = re.search(r"i2c(\d+)", sig.name, re.IGNORECASE)
        if match:
            buses.add(f"i2c{match.group(1)}")
        else:
            buses.add("i2c0")
    for dev in i2c_devices:
        buses.add(dev.bus or "i2c0")
    return sorted(buses)


_WAN_SFP_GPIO_FACTS: tuple[tuple[str, str, str], ...] = (
    ("WAN_SFP_RX_LOS", "los-gpio", "GPIO_ACTIVE_HIGH"),
    ("WAN_SFP_PRESENT", "mod-def0-gpio", "GPIO_ACTIVE_LOW"),
    ("WAN_XCVR_TXEN", "tx-power-gpio", "GPIO_ACTIVE_LOW"),
    ("WAN_SFP_PD_RST", "tx-power-down-gpio", "GPIO_ACTIVE_HIGH"),
    ("WAN_XCVR_RXEN", "rx-power-gpio", "GPIO_ACTIVE_LOW"),
)


_WAN_SERDES_MDIO_GPIO_FACTS: tuple[tuple[str, str, str], ...] = (
    ("WAN_XCVR_RXEN", "rx-power", "GPIO_ACTIVE_LOW"),
    ("WAN_XCVR_TXEN", "tx-power", "GPIO_ACTIVE_LOW"),
)


def _wan_sfp_gpio_properties(signals: list[Signal]) -> list[tuple[str, int, str]]:
    properties: list[tuple[str, int, str]] = []
    for signal_name, property_name, polarity in _WAN_SFP_GPIO_FACTS:
        signal = _find_signal(signals, signal_name)
        if signal is None:
            continue
        gpio = _extract_gpio_num(signal.soc_pin)
        if gpio is None:
            continue
        properties.append((property_name, int(gpio), polarity))
    return properties


def _wan_serdes_mdio_gpio_properties(signals: list[Signal]) -> list[tuple[str, int, str]]:
    properties: list[tuple[str, int, str]] = []
    for signal_name, property_name, polarity in _WAN_SERDES_MDIO_GPIO_FACTS:
        signal = _find_signal(signals, signal_name)
        if signal is None:
            continue
        gpio = _extract_gpio_num(signal.soc_pin)
        if gpio is None:
            continue
        properties.append((property_name, int(gpio), polarity))
    return properties


def _wan_sfp_hint_value(hints: list[DtsHint], property_name: str) -> str | None:
    for hint in hints:
        if hint.target not in {"wan_sfp", "/wan_sfp"}:
            continue
        if hint.property != property_name or not hint.value:
            continue
        return hint.value
    return None


def _boolean_hints_for_target(
    hints: list[DtsHint],
    target: str,
    *,
    allowed: set[str],
) -> list[str]:
    properties: list[str] = []
    for hint in hints:
        if hint.target != target:
            continue
        if not hint.property or hint.value:
            continue
        if hint.property not in allowed:
            continue
        properties.append(hint.property)
    return properties


def _infer_serdes_instances(signals: list[Signal]) -> list[str]:
    if _wan_sfp_gpio_properties(signals):
        return ["0"]
    if any(
        signal.role.upper() == "SERDES"
        and ("WAN" in signal.name.upper() or "SERDES0" in signal.name.upper())
        for signal in signals
    ):
        return ["0"]
    return []


def _extract_ethphy_indices_from_text(text: str | None) -> set[int]:
    """Extract GPHY/XPHY indices mentioned in free-form ethphy evidence."""
    if not text:
        return set()
    return {int(match) for match in re.findall(r"(?:GPHY|XPHY)(\d+)", text.upper())}


def _extract_xphy_index_from_target(target: str | None) -> int | None:
    if not target:
        return None
    match = re.search(r"(?:^|/)xphy(\d+)(?:$|/)", target.lower())
    if match:
        return int(match.group(1))
    match = re.search(r"\bxphy(\d+)\b", target.lower())
    if match:
        return int(match.group(1))
    return None


def _mdio_lane_swap_hints(hints: list[DtsHint]) -> list[DtsHint]:
    return [
        hint
        for hint in hints
        if hint.property == "enet-phy-lane-swap"
        and _extract_xphy_index_from_target(hint.target) is not None
    ]


def _reference_switch0_port_targets(reference_doc: Any | None) -> list[str]:
    if reference_doc is None:
        return []
    targets: list[tuple[int, str]] = []
    seen: set[str] = set()
    for path, nodes in reference_doc.node_index().items():
        if not path.startswith("/&switch0/ports/") or not nodes:
            continue
        if path in seen:
            continue
        seen.add(path)
        targets.append((nodes[0].start_line, path[1:]))
    return [target for _, target in sorted(targets)]


def _switch0_port_targets(hints: list[DtsHint], reference_doc: Any | None = None) -> list[str]:
    return list(_switch0_port_statuses(hints, reference_doc=reference_doc))


def _switch0_port_statuses(
    hints: list[DtsHint],
    reference_doc: Any | None = None,
) -> dict[str, str]:
    statuses: dict[str, str] = {
        target: '"disabled"' for target in _reference_switch0_port_targets(reference_doc)
    }
    for hint in hints:
        if not hint.target.startswith("&switch0/ports/"):
            continue
        if hint.property != "status":
            continue
        value = hint.value or '"okay"'
        current = statuses.get(hint.target)
        if current == '"okay"':
            continue
        if value == '"okay"' or current is None:
            statuses[hint.target] = value
    return statuses


def _has_ethernet_topology_evidence(
    signals: list[Signal],
    hints: list[DtsHint],
) -> bool:
    network_roles = ("ETHERNET_PHY", "SFP", "SERDES")
    if any(_signals_by_role(signals, role) for role in network_roles):
        return True

    network_targets = {
        "ethphytop",
        "&ethphytop",
        "mdio",
        "&mdio",
        "mdio_bus",
        "&mdio_bus",
        "serdes",
        "&serdes",
        "wan_serdes",
        "&wan_serdes",
        "switch0",
        "&switch0",
        "phy_wan_serdes",
        "&phy_wan_serdes",
        "xport",
        "&xport",
    }
    return bool(_mdio_lane_swap_hints(hints)) or bool(_switch0_port_statuses(hints)) or any(
        hint.target in network_targets for hint in hints
    )


def _has_mdio_management_evidence(
    signals: list[Signal],
    hints: list[DtsHint],
) -> bool:
    return bool(_mdio_lane_swap_hints(hints)) or any(
        _signals_by_role(signals, role)
        for role in ("ETHERNET_PHY", "SFP", "SERDES")
    )


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
    reset_signals, ses_signals = _button_signal_groups(signals)

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
            *_button_behavior_lines(_RESET_BUTTON_BEHAVIOR_LINES),
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
            *_button_behavior_lines(_SES_BUTTON_BEHAVIOR_LINES),
            f"{_INDENT}{_INDENT}}};",
        ])

    lines.append(f"{_INDENT}}};")
    return "\n".join(lines)


def _render_wan_sfp(signals: list[Signal], hints: list[DtsHint] | None = None) -> str:
    """Render wan_sfp { ... } in the root node from verified SFP GPIO evidence."""
    gpio_properties = _wan_sfp_gpio_properties(signals)
    if not gpio_properties:
        return ""
    hint_list = hints or []

    lines = [
        "",
        f"{_INDENT}wan_sfp: wan_sfp {{",
        f'{_INDENT}{_INDENT}compatible = "brcm,sfp";',
    ]
    i2c_bus = _wan_sfp_hint_value(hint_list, "i2c-bus")
    if i2c_bus:
        lines.append(f"{_INDENT}{_INDENT}i2c-bus = {i2c_bus};")
    for property_name, gpio, polarity in gpio_properties:
        lines.append(f"{_INDENT}{_INDENT}{property_name} = <&gpioc {gpio} {polarity}>;")
    lines.extend([
        f'{_INDENT}{_INDENT}status = "okay";',
        f"{_INDENT}}};",
    ])
    return "\n".join(lines)


def _render_uart(signals: list[Signal]) -> str:
    """Render &uart0 { status = "okay"; }; for UART signals."""
    instances = _infer_uart_instances(signals)
    if not instances:
        return ""

    lines = []
    for inst in instances:
        lines.extend([
            "",
            f"&uart{inst} {{",
            f'{_INDENT}status = "okay";',
            "};",
        ])
    return "\n".join(lines)


def _render_wdt(schema: HardwareSchema, signals: list[Signal]) -> str:
    """Render &wdt for chips with built-in watchdog capability or explicit evidence."""
    wdt_signals = _signals_by_role(signals, "WATCHDOG")
    if not wdt_signals and not _chip_supports_builtin_node(schema.chip, _BUILTIN_WDT_CHIPS):
        return ""
    return "\n".join([
        "",
        "&wdt {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_cpufreq(schema: HardwareSchema) -> str:
    """Render cpufreq policy for chips whose ref policy is approved for reuse."""
    if not _chip_supports_builtin_node(schema.chip, _BUILTIN_CPUFREQ_CHIPS):
        return ""
    return "\n".join([
        "",
        "&cpufreq {",
        f'{_INDENT}op-mode = "dvfs";',
        "};",
    ])


def _render_xport(
    schema: HardwareSchema,
    signals: list[Signal],
    hints: list[DtsHint],
) -> str:
    """Render &xport when the SoC supports it and board topology proves Ethernet use."""
    if not _chip_supports_builtin_node(schema.chip, _BUILTIN_XPORT_CHIPS):
        return ""
    if not _has_ethernet_topology_evidence(signals, hints):
        return ""
    return "\n".join([
        "",
        "&xport {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_switch0(hints: list[DtsHint], reference_doc: Any | None = None) -> str:
    """Render &switch0 internal port inventory plus proven board-topology statuses."""
    port_statuses = _switch0_port_statuses(hints, reference_doc=reference_doc)
    if not port_statuses:
        return ""

    lines = [
        "",
        "&switch0 {",
        f"{_INDENT}ports {{",
    ]
    for target, status in port_statuses.items():
        port_name = target.split("/ports/", 1)[1]
        lines.extend([
            f"{_INDENT}{_INDENT}{port_name} {{",
            f"{_INDENT}{_INDENT}{_INDENT}status = {status};",
            f"{_INDENT}{_INDENT}}};",
        ])
    lines.extend([
        f"{_INDENT}}};",
        "};",
    ])
    return "\n".join(lines)


def _render_mdio(
    schema: HardwareSchema,
    signals: list[Signal],
    hints: list[DtsHint],
) -> str:
    """Render &mdio when the SoC supports it and board management paths are proven."""
    if not _chip_supports_builtin_node(schema.chip, _BUILTIN_MDIO_CHIPS):
        return ""
    if not _has_mdio_management_evidence(signals, hints):
        return ""
    return "\n".join([
        "",
        "&mdio {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _mdio_xphy_status_indices(hints: list[DtsHint]) -> set[int]:
    indices: set[int] = set()
    for hint in hints:
        if hint.property != "status":
            continue
        if not hint.target.lower().startswith("&mdio_bus/xphy"):
            continue
        idx = _extract_xphy_index_from_target(hint.target)
        if idx is not None:
            indices.add(idx)
    return indices


def _render_mdio_bus(signals: list[Signal], hints: list[DtsHint]) -> str:
    """Render MDIO control-plane children from proven xphy inventory and lane-swap evidence."""
    lane_swap_indices = {
        idx
        for idx in (
            _extract_xphy_index_from_target(hint.target)
            for hint in _mdio_lane_swap_hints(hints)
        )
        if idx is not None
    }
    xphy_indices = sorted(_mdio_xphy_status_indices(hints) | lane_swap_indices)
    serdes_instances = [inst for inst in _infer_serdes_instances(signals) if inst == "0"]
    if not xphy_indices and not serdes_instances:
        return ""

    lines = [
        "",
        "&mdio_bus {",
    ]
    for idx in xphy_indices:
        lines.extend([
            f"{_INDENT}xphy{idx} {{",
            f'{_INDENT}{_INDENT}status = "okay";',
        ])
        if idx in lane_swap_indices:
            lines.append(f"{_INDENT}{_INDENT}enet-phy-lane-swap;  /* Lane swap traced for GPHY{idx} */")
        lines.append(f"{_INDENT}}};")

    serdes_gpio_properties = _wan_serdes_mdio_gpio_properties(signals)
    for inst in serdes_instances:
        lines.extend([
            f"{_INDENT}serdes{inst} {{",
        ])
        for property_name, gpio, polarity in serdes_gpio_properties:
            lines.append(f"{_INDENT}{_INDENT}{property_name} = <&gpioc {gpio} {polarity}>;")
        lines.extend([
            f"{_INDENT}{_INDENT}trx = <&wan_sfp>;",
            f'{_INDENT}{_INDENT}status = "okay";',
            f"{_INDENT}}};",
        ])
    lines.append("};")
    return "\n".join(lines)


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

    # Child LED nodes need explicit circuit/controller mapping; do not guess
    # crossbar-output or per-LED children from endpoint signal ordering alone.
    del led_signals

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
    mdio_lane_swap_hints = _mdio_lane_swap_hints(hints)
    if not eth_signals and not ethphy_hints and not mdio_lane_swap_hints:
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
        if hint.property:
            enabled_match = re.fullmatch(r"xphy(\d+)-enabled", hint.property)
            if enabled_match:
                enabled_phys.add(int(enabled_match.group(1)))
        enabled_phys.update(_extract_ethphy_indices_from_text(hint.value))
        enabled_phys.update(_extract_ethphy_indices_from_text(hint.reason))
    for hint in mdio_lane_swap_hints:
        idx = _extract_xphy_index_from_target(hint.target)
        if idx is not None:
            enabled_phys.add(idx)

    for phy_idx in sorted(enabled_phys):
        lines.append(f"{_INDENT}xphy{phy_idx}-enabled;")

    rendered_boolean_props = {f"xphy{phy_idx}-enabled" for phy_idx in enabled_phys}
    for hint in ethphy_hints:
        if not hint.property:
            continue
        if hint.property in rendered_boolean_props and not hint.value:
            continue
        if hint.value:
            lines.append(f"{_INDENT}{hint.property} = {hint.value};")
        else:
            lines.append(f"{_INDENT}{hint.property};")

    lines.extend([
        f'{_INDENT}status = "okay";',
        "};",
    ])
    return "\n".join(lines)


def _reference_i2c_device_label(
    reference_doc: Any | None,
    *,
    bus: str,
    addr_hex: str,
    compatible: str,
) -> str | None:
    if reference_doc is None:
        return None
    ref_nodes = reference_doc.node_index().get(f"/&{bus}/gpio@{addr_hex}", [])
    for node in ref_nodes:
        if node.label is None:
            continue
        compatible_prop = node.properties.get("compatible")
        if compatible_prop is not None and compatible_prop.value == f'"{compatible}"':
            return node.label
    return None


def _render_i2c(
    signals: list[Signal],
    devices: list[Device],
    *,
    reference_doc: Any | None = None,
) -> str:
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
    buses = _infer_i2c_buses(signals, devices)

    output_parts = []

    for bus in buses:
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
            label = _reference_i2c_device_label(
                reference_doc,
                bus=bus,
                addr_hex=addr_hex,
                compatible=compat,
            ) or dev.refdes.lower()

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


def _render_usb(signals: list[Signal], hints: list[DtsHint] | None = None) -> str:
    """Render &usb_ctrl { ... } for USB signals."""
    usb_signals = _signals_by_role(signals, "USB")
    if not usb_signals:
        return ""

    usb_ports = _infer_usb_ports(usb_signals)
    usb_ctrl_props = _boolean_hints_for_target(
        hints or [],
        "&usb_ctrl",
        allowed={"port1-disabled"},
    )

    lines = [
        "",
        "&usb_ctrl {",
        f'{_INDENT}pinctrl-names = "default";',
        f"{_INDENT}pinctrl-0 = <&usb0_pwr_pins &usb1_pwr_pins>;",
        f"{_INDENT}xhci-enable;",
        f'{_INDENT}status = "okay";',
    ]
    for prop in usb_ctrl_props:
        lines.append(f"{_INDENT}{prop};")
    lines.extend([
        "};",
        "",
        "&usb0_xhci {",
        f'{_INDENT}status = "okay";',
        "};",
    ])
    if usb_ports:
        lines.pop()
        for port in usb_ports:
            lines.extend([
                "",
                f"{_INDENT}usb_port{port}: port{port} {{",
                f"{_INDENT}{_INDENT}reg = <{port}>;",
                f"{_INDENT}{_INDENT}#trigger-source-cells = <0>;",
                f"{_INDENT}}};",
            ])
        lines.append("};")
    return "\n".join(lines)


def _render_pcie(signals: list[Signal]) -> str:
    """Render &pcie0/1/2 { ... } for evidence-backed PCIe/Wi-Fi signals."""
    instances = infer_pcie_instances(sig.name for sig in signals)
    if not instances:
        return ""

    regulator_gpio_by_instance: dict[int, str] = {}
    for inst, signal_name in _PCIE_REGULATOR_SIGNAL_BY_INSTANCE.items():
        signal = _find_signal(signals, signal_name)
        if signal is None:
            regulator_gpio_by_instance = {}
            break
        gpio = _extract_gpio_num(signal.soc_pin)
        if gpio is None:
            regulator_gpio_by_instance = {}
            break
        regulator_gpio_by_instance[inst] = gpio

    render_regulator_block = set(regulator_gpio_by_instance) == set(instances)

    lines = []
    if render_regulator_block:
        lines.extend([
            "#if defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)",
            "/**********************************************************************/",
            "/* GPIO: Add one define per PCIE (individual or shared) regulator     */",
            "/*       - Skip if no GPIO regulators in use                          */",
            "/**********************************************************************/",
            "#define PCIE_REG_GPIOC     gpioc           /* Internal GPIO Controller */",
        ])
        for inst in sorted(instances):
            gpio = regulator_gpio_by_instance[inst]
            signal_name = _PCIE_REGULATOR_SIGNAL_BY_INSTANCE[inst]
            lines.extend([
                f"#define PCIE{inst}_REG_GPIO    {gpio}   /* {signal_name} board rail control */",
                f"#define PCIE{inst}_REG_POLARITY  {_PCIE_REGULATOR_POLARITY}   /* board control net is active low */",
            ])
        lines.extend([
            "",
            '#include "../bcm_pcie_regulator.dtsi"',
            "",
            "/**********************************************************************/",
            "/* PCIe: Add status = \"okay\" for each PCIe slots of this board        */",
            "/**********************************************************************/",
        ])

    for inst in sorted(instances):
        lines.extend([
            "",
            f"&pcie{inst} {{",
            f'{_INDENT}status = "okay";',
            "};",
        ])
    if render_regulator_block:
        lines.append("#endif // defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)")
    return "\n".join(lines)


def _render_serdes(signals: list[Signal]) -> str:
    """Render &wan_serdes { ... } for SFP / SERDES signals."""
    serdes_instances = _infer_serdes_instances(signals)
    if not serdes_instances:
        return ""

    lines = [
        "",
        "&wan_serdes {",
        f'{_INDENT}status = "okay";',
    ]

    for inst in serdes_instances:
        lines.extend([
            "",
            f"{_INDENT}serdes{inst} {{",
            f"{_INDENT}{_INDENT}trx = <&wan_sfp>;",
            f"{_INDENT}}};",
        ])

    lines.append("};")
    return "\n".join(lines)


def _render_serdes_core(signals: list[Signal]) -> str:
    """Render &serdes when board evidence proves SerDes path usage."""
    if not _infer_serdes_instances(signals):
        return ""
    return "\n".join([
        "",
        "&serdes {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_phy_wan_serdes(signals: list[Signal]) -> str:
    """Render &phy_wan_serdes when wan_sfp or serdes0 path is proven."""
    if "0" not in _infer_serdes_instances(signals):
        return ""
    return "\n".join([
        "",
        "&phy_wan_serdes {",
        f'{_INDENT}status = "okay";',
        "};",
    ])


def _render_power_ctrl(signals: list[Signal]) -> str:
    """Render power control nodes."""
    pwr_signals = _signals_by_role(signals, "POWER_CONTROL")
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


def _render_gpioc_wifi_hogs(signals: list[Signal]) -> str:
    """Render Wi-Fi RF_DISABLE/PEWAKE gpio-hogs under &gpioc."""
    wifi_signals = _signals_by_role(signals, "PCIE_WIFI")
    if not wifi_signals:
        return ""

    def _signal_sort_key(sig: Signal) -> tuple[int, str]:
        match = re.search(r"(\d+)G", sig.name.upper())
        band = int(match.group(1)) if match else 999
        return (band, sig.name)

    rf_disable = sorted(
        [sig for sig in wifi_signals if "RF_DISABLE" in sig.name.upper()],
        key=_signal_sort_key,
    )
    pewake = sorted(
        [sig for sig in wifi_signals if "PEWAKE" in sig.name.upper()],
        key=_signal_sort_key,
    )
    if not rf_disable and not pewake:
        return ""

    lines = [
        "",
        "&gpioc {",
    ]

    def _append_hog(sig: Signal, *, polarity: str, output_state: str) -> None:
        gpio = _extract_gpio_num(sig.soc_pin)
        if gpio is None:
            return
        lines.extend([
            f"{_INDENT}pin{gpio} {{",
            f"{_INDENT}{_INDENT}gpio-hog;",
            f"{_INDENT}{_INDENT}gpios = <{gpio} {polarity}>;",
            f"{_INDENT}{_INDENT}{output_state};",
            f'{_INDENT}{_INDENT}line-name = "{sig.name}";',
            f"{_INDENT}}};",
        ])

    for sig in rf_disable:
        _append_hog(sig, polarity="GPIO_ACTIVE_LOW", output_state="output-low")
    if rf_disable and pewake:
        lines.append("")
    for sig in pewake:
        _append_hog(sig, polarity="GPIO_ACTIVE_HIGH", output_state="output-high")

    lines.append("};")
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


def _render_dts_hints(
    hints: list[DtsHint],
    already_rendered: set[str],
    reference_doc: Any | None = None,
) -> str:
    """Render DTS hints that haven't been covered by other render functions."""
    remaining = [h for h in hints if h.target not in already_rendered]
    if not remaining:
        return ""

    # Group by target
    by_target: dict[str, list[DtsHint]] = {}
    for h in remaining:
        by_target.setdefault(h.target, []).append(h)

    lines = []
    for target, target_hints in sorted(
        by_target.items(),
        key=lambda item: (
            _reference_sort_line(reference_doc, (item[0],)) or 10**9,
            item[0],
        ),
    ):
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


def _should_exclude_reference_i2c1_snippet(
    snippet_text: str,
    signals: list[Signal],
) -> bool:
    if "&i2c1" not in snippet_text:
        return False

    gpio_pins = {
        pin
        for pin in re.findall(r"b_bsc_m1_(?:sda|scl)_pin_(\d+)", snippet_text, re.IGNORECASE)
    }
    if not gpio_pins:
        return False

    signal_names_by_gpio: dict[str, list[Signal]] = {pin: [] for pin in gpio_pins}
    for signal in signals:
        gpio = _extract_gpio_num(signal.soc_pin)
        if gpio in signal_names_by_gpio:
            signal_names_by_gpio[gpio].append(signal)

    if any(not matches for matches in signal_names_by_gpio.values()):
        return False

    def _looks_i2c_signal(signal: Signal) -> bool:
        upper_name = signal.name.upper()
        upper_role = signal.role.upper()
        return "I2C" in upper_role or "SCL" in upper_name or "SDA" in upper_name

    return all(
        not any(_looks_i2c_signal(signal) for signal in matches)
        for matches in signal_names_by_gpio.values()
    )


def _should_exclude_reference_button_snippet(
    snippet_text: str,
    signals: list[Signal],
) -> bool:
    reset_signals, ses_signals = _button_signal_groups(signals)
    if "ses_button" in snippet_text and not ses_signals:
        return True
    if "reset_button" in snippet_text and not reset_signals:
        return True
    return False


def _normalize_i2c_address(address: str) -> str:
    normalized = address.strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("0")
    return normalized or "0"


def _extract_reference_i2c_child_signature(
    target: str,
    snippet_text: str,
) -> tuple[str, str, str | None] | None:
    match = re.search(r"/&(?P<bus>i2c\d+)/gpio@(?P<addr>[0-9a-f]+)$", target, re.IGNORECASE)
    if not match:
        return None

    compatible_match = re.search(r'compatible\s*=\s*"([^"]+)"', snippet_text, re.IGNORECASE)
    compatible = compatible_match.group(1).strip().lower() if compatible_match else None
    return (
        match.group("bus").lower(),
        _normalize_i2c_address(match.group("addr")),
        compatible,
    )


def _has_matching_local_i2c_device(
    devices: list[Device],
    *,
    bus: str,
    addr_hex: str,
    compatible: str | None,
) -> bool:
    for device in devices:
        if device.dnp or not device.bus or not device.address:
            continue
        if device.bus.lower() != bus:
            continue
        if _normalize_i2c_address(device.address) != addr_hex:
            continue
        if compatible and (device.compatible or "").lower() != compatible:
            continue
        return True
    return False


def _should_exclude_reference_i2c_child_snippet(
    target: str,
    snippet_text: str,
    devices: list[Device],
) -> bool:
    signature = _extract_reference_i2c_child_signature(target, snippet_text)
    if signature is None:
        return False

    bus, addr_hex, compatible = signature
    return not _has_matching_local_i2c_device(
        devices,
        bus=bus,
        addr_hex=addr_hex,
        compatible=compatible,
    )


def _should_exclude_reference_switch_port_snippet(
    target: str,
    _hints: list[DtsHint],
) -> bool:
    if not target.startswith("/&switch0/ports/"):
        return False

    return True


def _sanitize_reference_mdio_bus_snippet(snippet: list[str]) -> list[str]:
    if "&mdio_bus" not in "\n".join(snippet):
        return snippet

    sanitized: list[str] = []
    idx = 0
    while idx < len(snippet):
        line = snippet[idx]
        if re.match(r"\s*serdes\d+\s*\{", line):
            block = [line]
            depth = line.count("{") - line.count("}")
            idx += 1
            while idx < len(snippet):
                block_line = snippet[idx]
                block.append(block_line)
                depth += block_line.count("{") - block_line.count("}")
                idx += 1
                if depth <= 0:
                    break
            if any("lan_sfp" in block_line for block_line in block):
                continue
            sanitized.extend(block)
            continue
        sanitized.append(line)
        idx += 1
    return sanitized


def _sanitize_reference_snippet(snippet: list[str], signals: list[Signal]) -> list[str]:
    del signals
    sanitized = _sanitize_reference_mdio_bus_snippet(snippet)
    return [
        line
        for line in sanitized
        if not re.search(r"\blinux,(?:code|press|release)\b", line, flags=re.IGNORECASE)
    ]


def _should_exclude_reference_snippet(
    target: str,
    snippet: list[str],
    signals: list[Signal],
    devices: list[Device],
    hints: list[DtsHint],
) -> bool:
    snippet_text = "\n".join(snippet)
    return (
        any(pattern.search(snippet_text) for pattern in _REFERENCE_RETENTION_EXCLUDE_SNIPPET_PATTERNS)
        or _should_exclude_reference_i2c1_snippet(snippet_text, signals)
        or _should_exclude_reference_button_snippet(snippet_text, signals)
        or _should_exclude_reference_i2c_child_snippet(target, snippet_text, devices)
        or _should_exclude_reference_switch_port_snippet(target, hints)
    )


def _escape_block_comment_text(line: str) -> str:
    return line.replace("/*", "/ *").replace("*/", "* /")


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


def _normalize_reference_sort_target(target: str) -> str:
    if ":" in target:
        node_path, prop_name = target.rsplit(":", 1)
        return f"{_normalize_reference_sort_target(node_path)}:{prop_name}"
    if target.startswith("/"):
        return target
    if target.startswith("&"):
        return f"/{target}"
    return f"/&{target}"


def _reference_sort_line(reference_doc: Any | None, targets: tuple[str, ...]) -> int | None:
    if reference_doc is None:
        return None
    lines = [
        line
        for target in targets
        if (line := _reference_target_line(reference_doc, _normalize_reference_sort_target(target))) is not None
    ]
    return min(lines) if lines else None


def _ordered_block_texts(
    blocks: list[tuple[int, str, tuple[str, ...]]],
    reference_doc: Any | None,
) -> list[str]:
    ordered = sorted(
        [
            (fallback_order, text, reference_targets)
            for fallback_order, text, reference_targets in blocks
            if text
        ],
        key=lambda item: (
            _reference_sort_line(reference_doc, item[2]) or 10**9,
            item[0],
        ),
    )
    return [text for _, text, _ in ordered]


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
            f"/* Retained from public reference ({source}): "
            "no direct evidence confirms that this feature is absent on the target board."
        ),
    ]
    for snippet_line in snippet:
        lines.append(_escape_block_comment_text(snippet_line))
    lines.append("*/")
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
    signals: list[Signal],
    devices: list[Device],
    hints: list[DtsHint],
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
        snippet = _sanitize_reference_snippet(snippet, signals)
        if not snippet:
            continue
        if _should_exclude_reference_snippet(candidate.target, snippet, signals, devices, hints):
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
    reference_doc = parse_dts_document(ref_dts_path) if ref_dts_path and ref_dts_path.exists() else None

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
    wan_sfp_block = _render_wan_sfp(verified_sigs, all_hints)
    parts.extend(
        _ordered_block_texts(
            [
                (0, buttons_block, ("/buttons",)),
                (1, wan_sfp_block, ("/wan_sfp",)),
            ],
            reference_doc,
        )
    )

    # Close root node
    parts.append("};")

    # Track which hint targets are already rendered by specific renderers
    rendered_targets: set[str] = set()
    overlay_blocks: list[tuple[int, str, tuple[str, ...]]] = []
    if buttons_block:
        rendered_targets.update({"buttons", "/buttons"})
    if wan_sfp_block:
        rendered_targets.update({"wan_sfp", "/wan_sfp"})

    # 4. Subsystem overlay nodes (outside root)
    uart_block = _render_uart(verified_sigs)
    if uart_block:
        overlay_blocks.append(
            (0, uart_block, tuple(f"/&uart{inst}" for inst in _infer_uart_instances(verified_sigs)))
        )

    wdt_block = _render_wdt(schema, verified_sigs)
    if wdt_block:
        overlay_blocks.append((1, wdt_block, ("/&wdt",)))
        rendered_targets.update({"wdt", "&wdt"})

    cpufreq_block = _render_cpufreq(schema)
    if cpufreq_block:
        overlay_blocks.append((2, cpufreq_block, ("/&cpufreq",)))
        rendered_targets.update({"cpufreq", "&cpufreq"})

    xport_block = _render_xport(schema, verified_sigs, all_hints)
    if xport_block:
        overlay_blocks.append((3, xport_block, ("/&xport",)))
        rendered_targets.update({"xport", "&xport"})

    switch0_block = _render_switch0(all_hints, reference_doc=reference_doc)
    if switch0_block:
        overlay_blocks.append((4, switch0_block, ("/&switch0",)))
        rendered_targets.update({"switch0", "&switch0"})
        rendered_targets.update(_switch0_port_targets(all_hints, reference_doc=reference_doc))

    hsspi_block = _render_hsspi(verified_sigs)
    if hsspi_block:
        overlay_blocks.append((5, hsspi_block, ("/&hsspi",)))

    led_block = _render_led_ctrl(verified_sigs, verified_devs)
    if led_block:
        overlay_blocks.append((6, led_block, ("/&led_ctrl",)))

    ethphy_block = _render_ethphy(verified_sigs, all_hints)
    if ethphy_block:
        overlay_blocks.append((7, ethphy_block, ("/&ethphytop",)))
        rendered_targets.update({"ethphytop", "&ethphytop"})

    mdio_block = _render_mdio(schema, verified_sigs, all_hints)
    if mdio_block:
        overlay_blocks.append((8, mdio_block, ("/&mdio",)))
        rendered_targets.update({"mdio", "&mdio"})

    mdio_bus_block = _render_mdio_bus(verified_sigs, all_hints)
    if mdio_bus_block:
        overlay_blocks.append((9, mdio_bus_block, ("/&mdio_bus",)))
        rendered_targets.update({"mdio_bus", "&mdio_bus"})
        rendered_targets.update(
            {f"&mdio_bus/xphy{idx}" for idx in _mdio_xphy_status_indices(all_hints)}
        )
        rendered_targets.update(hint.target for hint in _mdio_lane_swap_hints(all_hints))

    i2c_block = _render_i2c(verified_sigs, verified_devs, reference_doc=reference_doc)
    if i2c_block:
        overlay_blocks.append(
            (10, i2c_block, tuple(f"/&{bus}" for bus in _infer_i2c_buses(verified_sigs, verified_devs)))
        )

    usb_block = _render_usb(verified_sigs, all_hints)
    if usb_block:
        overlay_blocks.append((11, usb_block, ("/&usb_ctrl", "/&usb0_xhci")))
        rendered_targets.update({"usb_ctrl", "&usb_ctrl", "usb0_xhci", "&usb0_xhci"})

    pcie_block = _render_pcie(verified_sigs)
    if pcie_block:
        overlay_blocks.append(
            (
                16,
                pcie_block,
                tuple(f"/&pcie{inst}" for inst in infer_pcie_instances(sig.name for sig in verified_sigs)),
            )
        )

    serdes_block = _render_serdes(verified_sigs)
    if serdes_block:
        overlay_blocks.append((13, serdes_block, ("/&wan_serdes",)))
        rendered_targets.update({"wan_serdes", "&wan_serdes"})

    serdes_core_block = _render_serdes_core(verified_sigs)
    if serdes_core_block:
        overlay_blocks.append((13, serdes_core_block, ("/&serdes",)))
        rendered_targets.update({"serdes", "&serdes"})

    phy_wan_serdes_block = _render_phy_wan_serdes(verified_sigs)
    if phy_wan_serdes_block:
        overlay_blocks.append((13, phy_wan_serdes_block, ("/&phy_wan_serdes",)))
        rendered_targets.update({"phy_wan_serdes", "&phy_wan_serdes"})

    gpioc_block = _render_gpioc_wifi_hogs(verified_sigs)
    if gpioc_block:
        overlay_blocks.append((15, gpioc_block, ("/&ext_pwr_ctrl", "/&gpioc")))

    pwr_block = _render_power_ctrl(verified_sigs)
    if pwr_block:
        overlay_blocks.append((14, pwr_block, ("/&ext_pwr_ctrl",)))

    parts.extend(_ordered_block_texts(overlay_blocks, reference_doc))

    # 5. Remaining DTS hints
    hints_block = _render_dts_hints(all_hints, rendered_targets, reference_doc=reference_doc)
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
        signals=verified_sigs,
        devices=verified_devs,
        hints=all_hints,
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
