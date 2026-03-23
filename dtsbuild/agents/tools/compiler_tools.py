"""
Compiler tools for Agent D: DTS Compiler.

These tools help produce valid DTS syntax from VERIFIED schema records.
They are deterministic — no guessing, no PDF access, no user interaction.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from copilot.tools import define_tool as _sdk_define_tool
    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    _sdk_define_tool = None


def define_tool(description: str = ""):
    """Decorator that registers a tool with the SDK (if available) while
    keeping the decorated function directly callable."""
    def decorator(func):
        func._tool_description = description
        if _sdk_define_tool is not None:
            tool_obj = _sdk_define_tool(description=description)(func)
            func._tool = tool_obj
        return func
    return decorator


# ── DTS node rendering ───────────────────────────────────────────────

_INDENT = "\t"


def _indent_str(level: int) -> str:
    return _INDENT * level


def _is_boolean_property(value: str | None) -> bool:
    """A DTS boolean property has no value — just ``name;``."""
    return value is None or value == ""


@define_tool(description="Render a single DTS node with proper indentation and syntax.")
def render_dts_node(
    node_name: str,
    properties: list[tuple[str, str | None]],
    children: list[str] | None = None,
    indent: int = 0,
) -> str:
    """Render a single DTS node.

    Parameters
    ----------
    node_name:
        The node label, e.g. ``"buttons"`` or ``"reset_button"``.
    properties:
        List of ``(name, value)`` tuples.
        * Integer-style value: ``("reg", "<0x27>")`` → ``reg = <0x27>;``
        * String-style value: ``("status", '"okay"')`` → ``status = "okay";``
        * Boolean / empty: ``("xhci-enable", None)`` → ``xhci-enable;``
    children:
        Pre-rendered child node strings (already indented internally).
    indent:
        Base indentation level (0 = root).
    """
    pad = _indent_str(indent)
    inner = _indent_str(indent + 1)
    lines: list[str] = []

    lines.append(f"{pad}{node_name} {{")

    for name, value in properties:
        if _is_boolean_property(value):
            lines.append(f"{inner}{name};")
        else:
            lines.append(f"{inner}{name} = {value};")

    if children:
        if properties:
            lines.append("")  # blank separator
        for child in children:
            # Re-indent child block to sit inside this node
            for cline in child.splitlines():
                lines.append(f"{inner}{cline}")
            lines.append("")  # blank line between children

        # Remove trailing blank if present
        if lines and lines[-1] == "":
            lines.pop()

    lines.append(f"{pad}}};")
    return "\n".join(lines)


@define_tool(description="Render a DTS reference override: &ref_name { ... };")
def render_dts_reference(
    ref_name: str,
    properties: list[tuple[str, str | None]],
    children: list[str] | None = None,
) -> str:
    """Render a reference override node (``&ref_name { ... };``).

    Delegates to :func:`render_dts_node` with the ``&`` prefix.
    """
    return render_dts_node(f"&{ref_name}", properties, children=children, indent=0)


# ── GPIO helper ──────────────────────────────────────────────────────

@define_tool(description="Format a GPIO reference: <&gpioc NUM 0>")
def render_gpio_property(gpio_num: int) -> str:
    """Return a GPIO phandle reference string, e.g. ``<&gpioc 48 0>``."""
    return f"<&gpioc {gpio_num} 0>"


# ── Syntax validation ────────────────────────────────────────────────

_PROPERTY_LINE_RE = re.compile(
    r"^\s*[\w#,.:@+-]+\s*=\s*.+$"
)

_BOOL_PROP_LINE_RE = re.compile(
    r"^\s*[\w#,.:@+-]+\s*;\s*$"
)


@define_tool(description="Basic DTS syntax validation (brace balance, header, common errors).")
def validate_dts_syntax(dts_content: str) -> dict[str, Any]:
    """Run lightweight syntax checks on DTS text.

    This is **not** a full ``dtc`` compiler validation — it catches the most
    common authoring mistakes.

    Returns
    -------
    dict with keys ``valid``, ``errors``, ``warnings``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- /dts-v1/; header ---
    stripped = dts_content.lstrip()
    has_header = stripped.startswith("/dts-v1/;")
    # Also accept files that start with #include (they get the header from included file)
    has_include = stripped.startswith("#include")
    if not has_header and not has_include:
        warnings.append("Missing /dts-v1/; header (may be supplied by an #include)")

    # --- balanced braces ---
    open_braces = dts_content.count("{")
    close_braces = dts_content.count("}")
    if open_braces != close_braces:
        errors.append(
            f"Unbalanced braces: {open_braces} opening vs {close_braces} closing"
        )

    # --- unclosed strings ---
    in_string = False
    line_start = 0
    for lineno, line in enumerate(dts_content.splitlines(), start=1):
        # Skip comment lines
        stripped_line = line.strip()
        if stripped_line.startswith("//") or stripped_line.startswith("/*"):
            continue

        quote_count = line.count('"') - line.count('\\"')
        if quote_count % 2 != 0:
            if in_string:
                in_string = False  # closes previous
            else:
                in_string = True
                line_start = lineno

    if in_string:
        errors.append(f"Unclosed string literal starting around line {line_start}")

    # --- property lines missing semicolons ---
    for lineno, line in enumerate(dts_content.splitlines(), start=1):
        stripped_line = line.strip()
        # Skip blanks, comments, preprocessor, braces, labels
        if (
            not stripped_line
            or stripped_line.startswith("//")
            or stripped_line.startswith("/*")
            or stripped_line.startswith("*")
            or stripped_line.startswith("#")
            or stripped_line.startswith("/dts-v1/")
            or stripped_line in ("{", "};", "}", "")
            or stripped_line.endswith("{")
            or stripped_line.endswith("};")
            or stripped_line.endswith(";")
            or stripped_line.endswith("\\")
        ):
            continue

        # Lines that look like property assignments but lack a semicolon
        if _PROPERTY_LINE_RE.match(stripped_line):
            # Could be a multi-line value continued with backslash on next line
            warnings.append(
                f"Line {lineno}: possible missing semicolon: {stripped_line!r}"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── Coverage computation ─────────────────────────────────────────────

@define_tool(
    description="Compare VERIFIED schema records against generated DTS content "
                "to compute coverage."
)
def compute_coverage(schema: Any, dts_content: str) -> dict[str, Any]:
    """Check how many VERIFIED signals/devices appear in the DTS output.

    Parameters
    ----------
    schema:
        A ``HardwareSchema`` instance (or duck-typed object with
        ``.signals`` and ``.devices`` lists).
    dts_content:
        The generated DTS text to search.

    Returns
    -------
    dict with ``total_verified``, ``covered``, ``coverage_pct``,
    ``uncovered``, and ``incomplete_not_in_dts``.
    """
    dts_lower = dts_content.lower()
    covered = 0
    uncovered: list[dict[str, str]] = []
    incomplete_count = 0

    verified_items: list[tuple[str, str]] = []

    for sig in schema.signals:
        if sig.status == "VERIFIED":
            verified_items.append((sig.name, sig.role))
        else:
            incomplete_count += 1

    for dev in schema.devices:
        if dev.status == "VERIFIED":
            label = dev.refdes
            role = dev.compatible or dev.part_number
            verified_items.append((label, role))
        else:
            incomplete_count += 1

    total_verified = len(verified_items)

    for name, role in verified_items:
        # Check if signal name or role appears in DTS (case-insensitive)
        name_lower = name.lower()
        role_lower = role.lower()

        # Build search tokens from role — e.g. "DEBUG_UART_TX" → "uart"
        role_tokens = set(role_lower.replace("_", " ").split())

        found = False
        if role_lower == "led_control" and "&led_ctrl" in dts_lower:
            found = True
        elif role_lower == "spi" and "&hsspi" in dts_lower:
            found = True
        elif name_lower in dts_lower:
            found = True
        elif role_lower in dts_lower:
            found = True
        else:
            # Check individual role tokens (at least a meaningful one)
            for token in role_tokens:
                if len(token) >= 4 and token in dts_lower:
                    found = True
                    break

        if found:
            covered += 1
        else:
            uncovered.append({
                "name": name,
                "role": role,
                "reason": "signal/device name and role not found in DTS output",
            })

    coverage_pct = (covered / total_verified * 100.0) if total_verified > 0 else 0.0

    return {
        "total_verified": total_verified,
        "covered": covered,
        "coverage_pct": round(coverage_pct, 1),
        "uncovered": uncovered,
        "incomplete_not_in_dts": incomplete_count,
    }


# ── Subsystem node templates ────────────────────────────────────────

_TEMPLATES: dict[str, dict[str, Any]] = {
    "uart": {
        "node_name": "&uart0",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
    },
    "i2c": {
        "node_name": "&i2c0",
        "is_reference": True,
        "required_properties": [
            ("status", '"okay"'),
            ("pinctrl-names", '"default"'),
        ],
        "optional_properties": [
            ("pinctrl-0", "<&bsc_m0_scl_pin_XX &bsc_m0_sda_pin_XX>"),
        ],
        "children_template": {
            "gpio@ADDR": {
                "required": ["compatible", "reg", "#gpio-cells", "gpio-controller"],
                "optional": ["polarity"],
            },
        },
    },
    "led_ctrl": {
        "node_name": "&led_ctrl",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
        "children_template": {
            "led*": {
                "required": ["label", "reg"],
                "optional": ["active_low", "brightness"],
            },
        },
    },
    "buttons": {
        "node_name": "buttons",
        "is_reference": False,
        "compatible": "brcm,buttons",
        "required_properties": [("compatible", '"brcm,buttons"')],
        "optional_properties": [],
        "children_template": {
            "button_*": {
                "required": ["ext_irq-gpio", "release", "press"],
                "optional": ["debounce-ms", "hold"],
            },
        },
    },
    "ethphytop": {
        "node_name": "&ethphytop",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [
            ("xphy0-enabled", None),
            ("xphy1-enabled", None),
            ("xphy2-enabled", None),
            ("xphy3-enabled", None),
            ("xphy4-enabled", None),
            ("enet-phy-lane-swap", "<0xXX>"),
            ("wakeup-trigger-pin-gpio", "<&gpioc XX GPIO_ACTIVE_LOW>"),
        ],
    },
    "pcie": {
        "node_name": "&pcie",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [
            ("brcm,dual-lane", None),
        ],
    },
    "usb": {
        "node_name": "&usb_ctrl",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [
            ("pinctrl-names", '"default"'),
            ("pinctrl-0", "<&usb0_pwr_pins &usb1_pwr_pins>"),
            ("xhci-enable", None),
            ("port1-disabled", None),
        ],
    },
    "serdes": {
        "node_name": "&serdes",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
    },
    "wan_serdes": {
        "node_name": "&wan_serdes",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
        "children_template": {
            "serdes*": {
                "required": [],
                "optional": ["pon-led", "alarm-led", "trx"],
            },
        },
    },
    "ext_pwr_ctrl": {
        "node_name": "slicpowerctl",
        "is_reference": False,
        "compatible": "brcm,voice-slic-power",
        "required_properties": [
            ("status", '"okay"'),
            ("compatible", '"brcm,voice-slic-power"'),
            ("pinctrl-names", '"default"'),
        ],
        "optional_properties": [
            ("slicpwrctl-gpio", "<&gpioc XX GPIO_ACTIVE_HIGH>"),
        ],
    },
    "hsspi": {
        "node_name": "&hsspi",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
    },
    "wdt": {
        "node_name": "&wdt",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
    },
    "cpufreq": {
        "node_name": "&cpufreq",
        "is_reference": True,
        "required_properties": [("status", '"okay"')],
        "optional_properties": [],
    },
}


@define_tool(
    description="Return a DTS node template for a common subsystem "
                "(uart, i2c, led_ctrl, buttons, ethphytop, pcie, usb, serdes, "
                "wan_serdes, ext_pwr_ctrl, hsspi, wdt, cpufreq)."
)
def build_node_template(subsystem: str) -> dict[str, Any]:
    """Return a template dict for the requested subsystem.

    Keys: ``node_name``, ``is_reference``, ``required_properties``,
    ``optional_properties``, and optionally ``compatible`` and
    ``children_template``.

    Raises ``KeyError`` if *subsystem* is not recognised.
    """
    key = subsystem.lower()
    if key not in _TEMPLATES:
        available = ", ".join(sorted(_TEMPLATES))
        raise KeyError(
            f"Unknown subsystem {subsystem!r}. Available: {available}"
        )
    return dict(_TEMPLATES[key])  # shallow copy
