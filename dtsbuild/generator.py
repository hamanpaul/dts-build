from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from .manifest import load_manifest
from .spec import extract_board_spec
from .sufficiency import build_gap_report, build_sufficiency_report

logger = logging.getLogger(__name__)


def generate_dts(
    folder: Path,
    output_path: Path | None = None,
    *,
    backend: str = "auto",
    model: str = "gpt-4.1",
    cli_url: str | None = None,
    schema_path: Path | None = None,
) -> Path:
    manifest = load_manifest(folder)
    output_root = manifest.resolve_output_dir(folder)
    output = output_path or output_root / manifest.output_dts
    output.parent.mkdir(parents=True, exist_ok=True)

    # --- Phase 2: schema-driven compilation ---------------------------
    if schema_path and Path(schema_path).exists():
        logger.info("Using schema-driven compilation (Phase 2)")
        from .agents.compiler import run_compiler

        return asyncio.run(run_compiler(schema_path, output))

    # --- Phase 1: legacy spec-based generation ------------------------
    logger.info("Using legacy spec-based generation (Phase 1)")

    spec = extract_board_spec(folder, manifest, backend=backend, model=model, cli_url=cli_url)
    sufficiency = build_sufficiency_report(folder, manifest, spec)
    gap_report = build_gap_report(sufficiency)
    spec_path = output.parent / f"{output.stem}.spec.json"
    sufficiency_path = output.parent / f"{output.stem}.sufficiency.json"
    gaps_path = output.parent / f"{output.stem}.gaps.json"
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sufficiency_path.write_text(json.dumps(sufficiency, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    gaps_path.write_text(json.dumps(gap_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    content = _render_dts(manifest, spec, sufficiency)
    output.write_text(content, encoding="utf-8")
    return output


def _render_dts(manifest, spec: dict, sufficiency: dict) -> str:
    public_reference = spec.get("public_reference") or {}
    rendered = _render_public_reference_board(manifest, spec, public_reference)
    if rendered:
        return rendered
    return _render_comment_draft(manifest, spec, sufficiency)


def _render_public_reference_board(manifest, spec: dict, public_reference: dict[str, Any]) -> str:
    if not public_reference.get("exists"):
        return ""

    patterns = set(public_reference.get("patterns") or [])
    network_rows = [
        row
        for row in (spec.get("network") or {}).get("rows", [])
        if isinstance(row, dict) and _is_present(row.get("present"))
    ]
    blockdiag_rows = [
        row
        for row in (spec.get("block_diagram") or {}).get("rows", [])
        if isinstance(row, dict) and _is_present(row.get("present"))
    ]
    gpio_rows = [row for row in (spec.get("gpio") or {}).get("rows", []) if isinstance(row, dict)]

    lan_rows = [row for row in network_rows if str(row.get("role", "")).strip().upper() == "LAN"]
    wan_rows = [row for row in network_rows if str(row.get("role", "")).strip().upper() == "WAN"]
    usb_rows = [row for row in blockdiag_rows if str(row.get("domain", "")).strip() == "usb"]
    wifi_rows = [row for row in blockdiag_rows if str(row.get("domain", "")).strip() == "pcie_wifi"]
    if len(lan_rows) < 4 or not wan_rows:
        return ""
    if not {"wan_sfp", "wan_serdes", "switch0", "mdio", "i2c", "pcie"}.issubset(patterns):
        return ""

    gpio_index = _build_gpio_index(gpio_rows)
    reset_irq_row = _find_gpio(gpio_index, "FACTORY RESET", "NORMAL RESET", "SCL_M2", "RBR")
    los_row = _find_gpio(gpio_index, "WAN_SFP_RX_LOS")
    present_row = _find_gpio(gpio_index, "WAN_SFP_PRESENT")
    tx_power_row = _find_gpio(gpio_index, "WAN_XCVR_TXEN")
    rx_power_row = _find_gpio(gpio_index, "WAN_XCVR_RXEN")
    tx_power_down_row = _find_gpio(gpio_index, "WAN_SFP_PD_RST")
    pwr_ctrl_row = _find_gpio(gpio_index, "CPU_VDD_PS_EN")
    phy_pwr_row = _find_gpio(gpio_index, "1V88_PS_EN")
    rf_disable_rows = [
        _find_gpio(gpio_index, "2G_RF_DISABLE_L"),
        _find_gpio(gpio_index, "5G_RF_DISABLE_L"),
        _find_gpio(gpio_index, "6G_RF_DISABLE_L"),
    ]
    pewake_rows = [
        _find_gpio(gpio_index, "2G_PEWAKE"),
        _find_gpio(gpio_index, "5G_PEWAKE"),
        _find_gpio(gpio_index, "6G_PEWAKE"),
    ]
    pcie0_row = _find_gpio(gpio_index, "PCIE02_WIFI_PWR_DIS")
    pcie12_row = _find_gpio(gpio_index, "PCIE13_WIFI_PWR_DIS")

    tx_disable_pin = 30
    model = manifest.model or manifest.project
    include_value = manifest.base_include or "inc/68375.dtsi"
    memcfg_macro = str((spec.get("memory") or {}).get("memcfg_macro", "")).strip()
    has_leds = any(str(row.get("domain", "")).strip() == "led_button" for row in blockdiag_rows)

    root_lines = ["/ {", f'    model = "{model}";']
    _append_block(
        root_lines,
        [
            "    memory_controller {",
            *_render_memcfg_lines(memcfg_macro or str(public_reference.get("memcfg_macro", "")).strip()),
            "    };",
        ],
    )
    _append_block(root_lines, _render_reset_evidence_comment(reset_irq_row))
    _append_block(
        root_lines,
        _render_wan_sfp_block(
            los_pin=_gpio_pin(los_row, 3),
            los_flag=_gpio_flag_for_loss(los_row),
            present_pin=_gpio_pin(present_row, 4),
            present_flag=_gpio_flag(present_row, "GPIO_ACTIVE_LOW"),
            tx_power_pin=_gpio_pin(tx_power_row, 53),
            tx_power_flag=_gpio_flag(tx_power_row, "GPIO_ACTIVE_LOW"),
            tx_power_down_pin=_gpio_pin(tx_power_down_row, 52),
            tx_power_down_flag=_gpio_flag(tx_power_down_row, "GPIO_ACTIVE_HIGH"),
            rx_power_pin=_gpio_pin(rx_power_row, 6),
            rx_power_flag=_gpio_flag(rx_power_row, "GPIO_ACTIVE_LOW"),
            tx_disable_pin=tx_disable_pin,
        ),
    )
    if has_leds:
        _append_block(root_lines, _render_led_evidence_comment(gpio_rows))
    if wifi_rows:
        _append_block(root_lines, _render_pcie_wifi_evidence_comment(wifi_rows))
    root_lines.append("};")

    lines = [f'#include "{include_value}"']
    _append_block(lines, root_lines)
    _append_block(lines, _render_wan_serdes_block())
    _append_block(lines, _render_i2c0_block())
    if usb_rows:
        _append_block(lines, _render_usb_ctrl_block())
    _append_block(lines, ["&xport {", '    status = "okay";', "};"])
    _append_block(lines, _render_ethphytop_block())
    _append_block(lines, ["&serdes {", '    status = "okay";', "};"])
    _append_block(lines, ["&mdio {", '    status = "okay";', "};"])
    _append_block(
        lines,
        _render_mdio_bus_block(
            rx_power_pin=_gpio_pin(rx_power_row, 6),
            rx_power_flag=_gpio_flag(rx_power_row, "GPIO_ACTIVE_LOW"),
            tx_power_pin=_gpio_pin(tx_power_row, 53),
            tx_power_flag=_gpio_flag(tx_power_row, "GPIO_ACTIVE_LOW"),
            tx_disable_pin=tx_disable_pin,
        ),
    )
    _append_block(lines, ["&phy_wan_serdes {", '    status = "okay";', "};"])
    _append_block(lines, _render_switch0_block())
    _append_block(
        lines,
        _render_ext_power_block(
            pwr_ctrl_pin=_gpio_pin(pwr_ctrl_row, 90),
            pwr_ctrl_flag=_gpio_flag(pwr_ctrl_row, "GPIO_ACTIVE_HIGH"),
            phy_pwr_pin=_gpio_pin(phy_pwr_row, 89),
            phy_pwr_flag=_gpio_flag(phy_pwr_row, "GPIO_ACTIVE_HIGH"),
        ),
    )
    gpioc_block = _render_gpioc_block(rf_disable_rows, pewake_rows)
    if gpioc_block:
        _append_block(lines, gpioc_block)
    if wifi_rows:
        _append_block(lines, _render_pcie_block(pcie0_row, pcie12_row))
    return "\n".join(lines).rstrip() + "\n"


def _render_comment_draft(manifest, spec: dict, sufficiency: dict) -> str:
    memory = spec.get("memory", {})
    public_reference = spec.get("public_reference") or {}
    blockdiag_rows = (spec.get("block_diagram") or {}).get("rows", [])
    network_rows = (spec.get("network") or {}).get("rows", [])
    gpio_rows = (spec.get("gpio") or {}).get("rows", [])
    meta = spec.get("meta") or {}
    backend = meta.get("backend", "unknown")
    lines: list[str] = [
        "/dts-v1/;",
        "",
        f'/* Generated by dts-build for project "{manifest.project}" */',
        f"/* profile: {manifest.profile or 'unknown'} */",
        f"/* refboard: {manifest.refboard or 'unknown'} */",
        f"/* output dir: {manifest.output_dir} */",
        f"/* spec backend: {backend} */",
        "",
    ]

    include_value = manifest.base_include or "inc/68375.dtsi"
    lines.append(f'#include "{include_value}"')
    lines.append("")
    lines.append("/ {")
    lines.append(f'    model = "{manifest.model}";')
    if manifest.compatible:
        lines.append(f'    compatible = "{manifest.compatible}";')
    lines.append("};")

    memcfg_macro = memory.get("memcfg_macro", "")
    if memcfg_macro:
        lines.extend(
            [
                "",
                "/ {",
                "    memory_controller {",
                f"        memcfg = <({memcfg_macro})>;",
                "    };",
                "};",
            ]
        )

    if public_reference.get("exists"):
        lines.extend(
            _render_comment_block(
                "Public reference patterns",
                [
                    f"path={public_reference.get('path', '')}",
                    f"model={public_reference.get('model', '')}",
                    f"memcfg_macro={public_reference.get('memcfg_macro', '') or '<none>'}",
                ]
                + [f"pattern={pattern}" for pattern in public_reference.get("patterns", [])]
                + [f"compatible={item}" for item in public_reference.get("compatibles", [])[:8]],
            )
        )

    lines.extend(
        _render_comment_block(
            "Block diagram interfaces",
            [
                f"{row.get('domain', '')}:{row.get('interface', '')} present={row.get('present', '')}, "
                f"controller={row.get('controller', '')}, endpoint={row.get('endpoint', '')}, "
                f"page_ref={row.get('page_ref', '')}"
                for row in blockdiag_rows
                if isinstance(row, dict) and any(value for value in row.values())
            ]
            or ["No structured block diagram rows were provided."],
        )
    )

    lines.extend(
        _render_comment_block(
            "TODO: network topology input rows",
            [
                f"{row.get('name', '')}: role={row.get('role', '')}, source={row.get('source', '')}, "
                f"phy_handle={row.get('phy_handle', '')}, phy_mode={row.get('phy_mode', '')}, "
                f"present={row.get('present', '')}"
                for row in network_rows
                if isinstance(row, dict) and any(value for value in row.values())
            ]
            or ["No structured network rows were provided."],
        )
    )

    lines.extend(
        _render_comment_block(
            "TODO: GPIO / LED input rows",
            [
                f"{row.get('category', '')}:{row.get('name', '')} signal={row.get('signal', '')}, "
                f"pin_or_gpio={row.get('pin_or_gpio', '')}, polarity={row.get('polarity', '')}, "
                f"io={row.get('io', '')}, notes={row.get('notes', '')}"
                for row in gpio_rows[:20]
                if isinstance(row, dict) and any(value for value in row.values())
            ]
            + ([f"... {len(gpio_rows) - 20} more rows omitted"] if len(gpio_rows) > 20 else [])
            or ["No structured GPIO/LED rows were provided."],
        )
    )

    missing_fields = spec.get("missing_fields") or []
    assumptions = spec.get("assumptions") or []
    if missing_fields:
        lines.extend(_render_comment_block("Missing fields", [str(item) for item in missing_fields]))
    if assumptions:
        lines.extend(_render_comment_block("Agent assumptions", [str(item) for item in assumptions]))
    lines.extend(
        _render_comment_block(
            "Sufficiency summary",
            [f"ready_to_generate={sufficiency.get('ready_to_generate', False)}"]
            + [f"blocking gap: {name}" for name in sufficiency.get("blocking_gaps", [])]
            + [f"non-blocking gap: {name}" for name in sufficiency.get("non_blocking_gaps", [])]
            + (sufficiency.get("questions", [])[:5] if sufficiency.get("questions") else ["No follow-up questions."]),
        )
    )

    lines.extend(
        _render_comment_block(
            "Next editing targets",
            [
                "Start from block diagram completeness, then resolve missing evidence before finalizing nodes.",
                "Translate network rows into &switch0, &mdio_bus, &wan_serdes, and related nodes.",
                "Translate GPIO/LED rows into buttons, LED mappings, and pinctrl/GPIO bindings.",
                "Use sufficiency/gap reports to drive ask-me follow-ups before removing TODO blocks.",
            ],
        )
    )

    return "\n".join(lines) + "\n"


def _render_memcfg_lines(memcfg_macro: str) -> list[str]:
    macro = memcfg_macro.strip()
    if not macro:
        return ["        /* TODO: fill memcfg from DDR evidence */"]
    tokens = [token.strip() for token in macro.split("|") if token.strip()]
    if len(tokens) <= 1:
        return [f"        memcfg = <({macro})>;"]
    width = max(len(token) for token in tokens)
    lines = [f"        memcfg = <({tokens[0].ljust(width)} | \\"]
    for token in tokens[1:-1]:
        lines.append(f"        {token.ljust(width)} | \\")
    lines.append(f"        {tokens[-1].ljust(width)} )>;")
    return lines


def _render_reset_evidence_comment(reset_irq_row: dict[str, Any] | None) -> list[str]:
    if not reset_irq_row:
        return []
    pin = _gpio_pin(reset_irq_row)
    notes = str(reset_irq_row.get("notes", "")).replace("\n", " / ")
    items = [
        f"reset input evidence: pin={pin}, signal={reset_irq_row.get('signal', '')}, polarity={reset_irq_row.get('polarity', '')}, io={reset_irq_row.get('io', '')}",
    ]
    if notes:
        items.append(f"notes={notes}")
    items.append("No full buttons node emitted yet; current evidence does not uniquely determine the DTS binding shape.")
    return _render_comment_block("Reset/button evidence", items)


def _render_wan_sfp_block(
    *,
    los_pin: int,
    los_flag: str,
    present_pin: int,
    present_flag: str,
    tx_power_pin: int,
    tx_power_flag: str,
    tx_power_down_pin: int,
    tx_power_down_flag: str,
    rx_power_pin: int,
    rx_power_flag: str,
    tx_disable_pin: int,
) -> list[str]:
    return [
        "    wan_sfp: wan_sfp {",
        '        pinctrl-names = "default", "tx-sd", "eth";',
        "        pinctrl-0 = <&wan0_lbe_pin_30>;",
        "        pinctrl-1 = <&wan0_lbe_pin_30 &rogue_onu_in0_pin_27>;",
        "        pinctrl-2 = <>;",
        '        compatible = "brcm,sfp";',
        "        i2c-bus = <&i2c0>;",
        f"        los-gpio = <&gpioc {los_pin} {los_flag}>;",
        f"        mod-def0-gpio = <&gpioc {present_pin} {present_flag}>;",
        f"        tx-power-gpio = <&gpioc {tx_power_pin} {tx_power_flag}>;",
        f"        tx-power-down-gpio = <&gpioc {tx_power_down_pin} {tx_power_down_flag}>;",
        f"        rx-power-gpio = <&gpioc {rx_power_pin} {rx_power_flag}>;",
        f"        tx-disable-gpio = <&gpioc {tx_disable_pin} GPIO_ACTIVE_HIGH>;",
        '        status = "okay";',
        "    };",
    ]


def _render_wan_serdes_block() -> list[str]:
    lines = [
        "&wan_serdes {",
        '    status = "okay";',
        "",
        "    serdes0 {",
    ]
    lines.extend(
        [
            "        trx = <&wan_sfp>;",
            "    };",
            "};",
        ]
    )
    return lines


def _render_i2c0_block() -> list[str]:
    return [
        "&i2c0 {",
        '    status = "okay";',
        "};",
    ]


def _render_usb_ctrl_block() -> list[str]:
    return [
        "&usb_ctrl {",
        '    status = "okay";',
        "};",
    ]

def _render_ethphytop_block() -> list[str]:
    return [
        "&ethphytop {",
        "    xphy0-enabled;",
        "    xphy1-enabled;",
        "    xphy2-enabled;",
        "    xphy3-enabled;",
        "    xphy4-enabled;",
        '    status = "okay";',
        "};",
    ]


def _render_mdio_bus_block(*, rx_power_pin: int, rx_power_flag: str, tx_power_pin: int, tx_power_flag: str, tx_disable_pin: int) -> list[str]:
    return [
        "&mdio_bus {",
        "    xphy0 {",
        '        status = "okay";',
        "    };",
        "    xphy1 {",
        '        status = "okay";',
        "    };",
        "    xphy2 {",
        '        status = "okay";',
        "    };",
        "    xphy3 {",
        '        status = "okay";',
        "    };",
        "    xphy4 {",
        '        status = "okay";',
        "    };",
        "    serdes0 {",
        '        pinctrl-names = "default";',
        "        pinctrl-0 = <&a_signal_detect0_pin_3 &a_mod_abs0_pin_4>;",
        f"        rx-power = <&gpioc {rx_power_pin} {rx_power_flag}>;",
        f"        tx-power = <&gpioc {tx_power_pin} {tx_power_flag}>;",
        f"        tx-disable = <&gpioc {tx_disable_pin} GPIO_ACTIVE_HIGH>;",
        "        10000-Base-R;",
        "        5000-Base-R;",
        "        2500-Base-X;",
        "        1000-Base-X;",
        "        trx = <&wan_sfp>;",
        '        status = "okay";',
        "    };",
        "};",
    ]


def _render_switch0_block() -> list[str]:
    return [
        "&switch0 {",
        "    ports {",
        "        port_xgphy0 {",
        '            status = "okay";',
        "        };",
        "        port_xgphy1 {",
        '            status = "okay";',
        "        };",
        "        port_xgphy2 {",
        '            status = "okay";',
        "        };",
        "        port_xgphy3 {",
        '            status = "okay";',
        "        };",
        "        port_xgphy4 {",
        '            status = "okay";',
        "        };",
        "        port_slan0@xpon_ae {",
        '            status = "okay";',
        "        };",
        "        port_wan@xpon_ae {",
        '            status = "okay";',
        "        };",
        "        port_wan@slan_sd {",
        '            status = "okay";',
        "        };",
        "    };",
        "};",
    ]


def _render_led_evidence_comment(gpio_rows: list[dict[str, Any]]) -> list[str]:
    serial_led_rows = []
    for row in gpio_rows:
        signal = str(row.get("signal", "")).strip()
        if signal in {"SER_LED_DATA", "SER_LED_CLK", "SER_LED_MASK"}:
            serial_led_rows.append(row)
    if not serial_led_rows:
        return []
    items = [
        f"{row.get('signal', '')}: pin={row.get('pin_or_gpio', '')}, io={row.get('io', '')}, notes={str(row.get('notes', '')).replace(chr(10), ' / ')}"
        for row in serial_led_rows
    ]
    items.append("LED child nodes are not emitted yet; channel/crossbar mapping is not determined by current evidence.")
    return _render_comment_block("LED evidence", items)


def _render_pcie_wifi_evidence_comment(rows: list[dict[str, Any]]) -> list[str]:
    items = [
        f"{row.get('controller', '')} -> {row.get('endpoint', '')} ({row.get('notes', '')})"
        for row in rows
    ]
    items.append("Wi-Fi endpoint/device nodes are not emitted yet; current renderer only keeps generic PCIe enable/power control scaffolding.")
    return _render_comment_block("PCIe/Wi-Fi evidence", items)


def _render_ext_power_block(*, pwr_ctrl_pin: int, pwr_ctrl_flag: str, phy_pwr_pin: int, phy_pwr_flag: str) -> list[str]:
    return [
        "&ext_pwr_ctrl {",
        f"    pwr-ctrl-0-gpios = <&gpioc {pwr_ctrl_pin} {pwr_ctrl_flag}>;",
        f"    phy-pwr-ctrl-gpios = <&gpioc {phy_pwr_pin} {phy_pwr_flag}>;",
        "};",
    ]


def _render_gpioc_block(rf_disable_rows: list[dict[str, Any] | None], pewake_rows: list[dict[str, Any] | None]) -> list[str]:
    hogs: list[str] = ["&gpioc {"]
    for row in rf_disable_rows:
        if not row:
            continue
        pin = _gpio_pin(row)
        if pin is None:
            continue
        hogs.extend(
            [
                f"    pin{pin} {{",
                "        gpio-hog;",
                f"        gpios = <{pin} {_gpio_flag(row, 'GPIO_ACTIVE_LOW')}>;",
                "        output-low;",
                f'        line-name = "{_line_name(row)}";',
                "    };",
            ]
        )
    if any(row for row in pewake_rows):
        hogs.append("")
    for row in pewake_rows:
        if not row:
            continue
        pin = _gpio_pin(row)
        if pin is None:
            continue
        hogs.extend(
            [
                f"    pin{pin} {{",
                "        gpio-hog;",
                f"        gpios = <{pin} {_gpio_flag(row, 'GPIO_ACTIVE_HIGH')}>;",
                "        output-high;",
                f'        line-name = "{_line_name(row)}";',
                "    };",
            ]
        )
    hogs.append("};")
    return hogs if len(hogs) > 2 else []


def _render_pcie_block(pcie0_row: dict[str, Any] | None, pcie12_row: dict[str, Any] | None) -> list[str]:
    pcie0_pin = _gpio_pin(pcie0_row, 51)
    pcie0_flag = _gpio_flag(pcie0_row, "GPIO_ACTIVE_LOW")
    pcie12_pin = _gpio_pin(pcie12_row, 11)
    pcie12_flag = _gpio_flag(pcie12_row, "GPIO_ACTIVE_LOW")
    return [
        "#if defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)",
        "/**********************************************************************/",
        "/* GPIO: Add one define per PCIE (individual or shared) regulator     */",
        "/*       - Skip if no GPIO regulators in use                          */",
        "/**********************************************************************/",
        "#define PCIE_REG_GPIOC     gpioc           /* Internal GPIO Controller */",
        f"#define PCIE0_REG_GPIO     {pcie0_pin:<2}              /* PCIE0_WiFi_PWR_DIS (PCIE0) */",
        f"#define PCIE0_REG_POLARITY  {pcie0_flag} /* enable is {'active low' if pcie0_flag == 'GPIO_ACTIVE_LOW' else 'active high'} */",
        f"#define PCIE1_REG_GPIO     {pcie12_pin:<2}              /* PCIE12_WiFi_PWR_DIS (PCIE1,2) */",
        f"#define PCIE1_REG_POLARITY  {pcie12_flag} /* enable is {'active low' if pcie12_flag == 'GPIO_ACTIVE_LOW' else 'active high'} */",
        f"#define PCIE2_REG_GPIO     {pcie12_pin:<2}              /* PCIE12_WiFi_PWR_DIS (PCIE1,2) */",
        f"#define PCIE2_REG_POLARITY  {pcie12_flag} /* enable is {'active low' if pcie12_flag == 'GPIO_ACTIVE_LOW' else 'active high'} */",
        "",
        '#include "../bcm_pcie_regulator.dtsi"',
        "",
        "/**********************************************************************/",
        "/* PCIe: Add status = \"okay\" for each PCIe slots of this board        */",
        "/**********************************************************************/",
        "&pcie0 {",
        '      status = "okay";',
        "};",
        "",
        "&pcie1 {",
        '      status = "okay";',
        "};",
        "",
        "&pcie2 {",
        '      status = "okay";',
        "};",
        "#endif // defined(CONFIG_BCM_PCIE_HCD) || defined(CONFIG_BCM_PCIE_HCD_MODULE)",
    ]


def _build_gpio_index(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(str(row.get(field, "")) for field in ("category", "name", "signal", "notes"))
        indexed.append(
            {
                "haystack": _normalize_lookup(haystack),
                "keys": {
                    _normalize_lookup(str(row.get("name", ""))),
                    _normalize_lookup(str(row.get("signal", ""))),
                }
                - {""},
                "row": row,
            }
        )
    return indexed


def _find_gpio(indexed_rows: list[dict[str, Any]], *needles: str) -> dict[str, Any] | None:
    normalized_needles = [_normalize_lookup(needle) for needle in needles if needle]
    for needle in normalized_needles:
        for entry in indexed_rows:
            if needle and needle in entry["keys"]:
                return entry["row"]
    for needle in normalized_needles:
        for entry in indexed_rows:
            if needle and needle in entry["haystack"]:
                return entry["row"]
    return None


def _normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _gpio_pin(row: dict[str, Any] | None, default: int | None = None) -> int | None:
    if not row:
        return default
    match = re.search(r"(\d+)", str(row.get("pin_or_gpio", "")))
    if not match:
        return default
    return int(match.group(1))


def _gpio_flag(row: dict[str, Any] | None, default: str) -> str:
    polarity = str((row or {}).get("polarity", "")).strip().lower()
    if polarity == "active_low":
        return "GPIO_ACTIVE_LOW"
    if polarity == "active_high":
        return "GPIO_ACTIVE_HIGH"
    return default


def _gpio_flag_for_loss(row: dict[str, Any] | None) -> str:
    notes = str((row or {}).get("notes", "")).lower()
    if "loss of signal" in notes and "high" in notes:
        return "GPIO_ACTIVE_HIGH"
    return _gpio_flag(row, "GPIO_ACTIVE_HIGH")


def _line_name(row: dict[str, Any]) -> str:
    signal = str(row.get("signal", "")).strip()
    name = str(row.get("name", "")).strip()
    return signal or name or "gpio-line"


def _is_present(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "present", "okay"}


def _append_block(lines: list[str], block: list[str]) -> None:
    if not block:
        return
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(block)


def _render_comment_block(title: str, items: list[str]) -> list[str]:
    lines = ["", "/*", f" * {title}"]
    for item in items:
        lines.append(f" * - {item}")
    lines.append(" */")
    return lines
