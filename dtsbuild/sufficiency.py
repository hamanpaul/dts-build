from __future__ import annotations

from pathlib import Path
from typing import Any

from .manifest import Manifest
from .schema import ClarificationRequest
from .spec import read_optional_table


# ---------------------------------------------------------------------------
# Domain mapping for ClarificationRequest generation
# ---------------------------------------------------------------------------

_DOMAIN_NAME_MAP: dict[str, str] = {
    "gpio_controls": "gpio_assignment",
    "ddr": "memory_config",
    "network_topology": "network_config",
    "pcie_wifi": "pcie_config",
    "leds_buttons": "led_config",
    "storage_boot": "general",
    "project_identity": "general",
    "block_diagram": "general",
    "tod_timing": "chip_features",
    "output_contract": "general",
}

_DOMAIN_KEYWORDS: list[tuple[list[str], str]] = [
    (["gpio", "pin mapping", "control-pin", "control pin"], "gpio_assignment"),
    (["led", "polarity"], "led_config"),
    (["button", "wps"], "button_config"),
    (["ddr", "memory", "memcfg", "mcb", "meminit"], "memory_config"),
    (["network", "serdes", "wan", "lan", "phy", "sfp", "port mapping"], "network_config"),
    (["wdt", "cpufreq", "hsspi"], "chip_features"),
    (["pcie", "wifi", "wi-fi", "wlan", "rf_disable", "pewake", "perst"], "pcie_config"),
    (["usb"], "usb_config"),
    (["i2c"], "i2c_config"),
    (["uart"], "uart_config"),
    (["power", "regulator"], "power_control"),
]


def build_sufficiency_report(folder: Path, manifest: Manifest, spec: dict[str, Any]) -> dict[str, Any]:
    blockdiag_rows = (spec.get("block_diagram") or {}).get("rows", [])
    ddr_rows = read_optional_table(folder, manifest.artifacts.get("ddr_table"))
    network_rows = (spec.get("network") or {}).get("rows", [])
    gpio_rows = (spec.get("gpio") or {}).get("rows", [])
    memory = spec.get("memory") or {}
    ddr_fields = memory.get("fields") or {}

    blockdiag_text = _rows_text(blockdiag_rows)
    gpio_text = _rows_text(gpio_rows)

    domains = [
        _make_domain(
            name="project_identity",
            blocking=True,
            status="sufficient" if all([manifest.project, manifest.model, manifest.output_dts, manifest.output_dir]) else "missing",
            reasons=[] if all([manifest.project, manifest.model, manifest.output_dts, manifest.output_dir]) else ["manifest project/model/output settings are incomplete"],
            questions=["Please confirm project name, model string, output DTS filename, and output folder name."],
        ),
        _make_domain(
            name="block_diagram",
            blocking=True,
            status="sufficient" if blockdiag_rows else "missing",
            reasons=[] if blockdiag_rows else ["no structured block diagram inventory was found"],
            questions=[
                "Please provide a block diagram table listing interfaces such as DDR, WAN/SFP, xPHY/LAN, PCIe/Wi-Fi, USB, storage, LEDs, buttons, I2C devices, and TOD."
            ],
        ),
        _classify_ddr(ddr_fields, ddr_rows, blockdiag_text),
        _classify_network(network_rows, blockdiag_text),
        _classify_gpio(gpio_rows),
        _classify_pcie_wifi(blockdiag_text, gpio_text),
        _classify_storage(blockdiag_text, manifest),
        _classify_leds_buttons(blockdiag_text, gpio_text),
        _classify_tod(blockdiag_text, gpio_text),
        _make_domain(
            name="output_contract",
            blocking=False,
            status="sufficient",
            reasons=[f"default output directory: {manifest.output_dir}"],
            questions=[],
        ),
    ]

    blocking_gaps = [domain["name"] for domain in domains if domain["blocking"] and domain["status"] != "sufficient"]
    non_blocking_gaps = [domain["name"] for domain in domains if not domain["blocking"] and domain["status"] != "sufficient"]
    questions = [
        question
        for domain in domains
        if domain["status"] != "sufficient"
        for question in domain["questions"]
    ]

    return {
        "project": manifest.project,
        "ready_to_generate": not blocking_gaps,
        "blocking_gaps": blocking_gaps,
        "non_blocking_gaps": non_blocking_gaps,
        "questions": questions,
        "domains": domains,
    }


def build_gap_report(report: dict[str, Any]) -> dict[str, Any]:
    domains = report.get("domains") or []
    return {
        "project": report.get("project", ""),
        "ready_to_generate": report.get("ready_to_generate", False),
        "blocking_gaps": report.get("blocking_gaps", []),
        "non_blocking_gaps": report.get("non_blocking_gaps", []),
        "questions": report.get("questions", []),
        "details": [
            {
                "name": domain.get("name", ""),
                "status": domain.get("status", ""),
                "blocking": domain.get("blocking", False),
                "reasons": domain.get("reasons", []),
            }
            for domain in domains
            if domain.get("status") != "sufficient"
        ],
    }


def _classify_ddr(ddr_fields: dict[str, str], ddr_rows: list[dict[str, str]], blockdiag_text: str) -> dict[str, Any]:
    if ddr_fields.get("memcfg_macro"):
        return _make_domain("ddr", True, "sufficient", ["memcfg_macro provided"], [])

    if ddr_fields.get("ddr_type") and (ddr_fields.get("ddr_size") or ddr_fields.get("part_number") or ddr_fields.get("ddr_part")):
        return _make_domain(
            "ddr",
            True,
            "partial",
            ["DDR type and size/part are known, but memcfg_macro is still missing"],
            ["Please provide the final memcfg macro or enough board DDR settings (for example MCB/meminit choice) to derive it."],
        )

    if ddr_rows or any(keyword in blockdiag_text for keyword in ("ddr", "lpddr")):
        return _make_domain(
            "ddr",
            True,
            "partial",
            ["DDR exists in the project, but the structured DTS DDR configuration is incomplete"],
            ["Please provide DDR type, size, width, part number, and if available the final memcfg/MCB setting."],
        )

    return _make_domain(
        "ddr",
        True,
        "missing",
        ["no DDR evidence table was found"],
        ["Please provide a DDR table or equivalent structured DDR evidence before generating the board DTS."],
    )


def _classify_network(network_rows: list[dict[str, str]], blockdiag_text: str) -> dict[str, Any]:
    if network_rows:
        return _make_domain("network_topology", True, "sufficient", [f"{len(network_rows)} structured network rows found"], [])
    if any(keyword in blockdiag_text for keyword in ("wan", "lan", "ethernet", "sfp", "xphy", "serdes")):
        return _make_domain(
            "network_topology",
            True,
            "partial",
            ["network interfaces appear in the block diagram, but no structured network table is available"],
            ["Please provide a structured network/port mapping table that names WAN/LAN roles, PHY handles, and media type."],
        )
    return _make_domain(
        "network_topology",
        True,
        "missing",
        ["no network topology evidence was found"],
        ["Please provide at least a minimal network topology table before generation."],
    )


def _classify_gpio(gpio_rows: list[dict[str, str]]) -> dict[str, Any]:
    structured_rows = [row for row in gpio_rows if isinstance(row, dict) and row.get("pin_or_gpio")]
    if structured_rows:
        return _make_domain("gpio_controls", True, "sufficient", [f"{len(structured_rows)} structured GPIO rows found"], [])
    if gpio_rows:
        return _make_domain(
            "gpio_controls",
            True,
            "partial",
            ["GPIO evidence exists but could not be normalized into structured rows"],
            ["Please provide a structured GPIO/control-pin table or improve the GPIO sheet mapping."],
        )
    return _make_domain(
        "gpio_controls",
        True,
        "missing",
        ["no GPIO/control evidence was found"],
        ["Please provide GPIO/control pin information for reset, SFP, LEDs, Wi-Fi/PCIe control, and similar board signals."],
    )


def _classify_pcie_wifi(blockdiag_text: str, gpio_text: str) -> dict[str, Any]:
    if not any(keyword in blockdiag_text for keyword in ("pcie", "wifi", "wlan", "radio")):
        return _make_domain("pcie_wifi", False, "sufficient", ["no PCIe/Wi-Fi interfaces were declared in the block diagram"], [])
    if any(keyword in gpio_text for keyword in ("pcie", "wifi", "wlan", "rf_disable", "pewake")):
        return _make_domain("pcie_wifi", False, "sufficient", ["PCIe/Wi-Fi control evidence found"], [])
    return _make_domain(
        "pcie_wifi",
        False,
        "partial",
        ["PCIe/Wi-Fi appears in the block diagram but control-pin evidence is incomplete"],
        ["Please provide PCIe/Wi-Fi control pins such as power enable, PERST#, RF_DISABLE, PEWAKE, and any shared regulator information."],
    )


def _classify_storage(blockdiag_text: str, manifest: Manifest) -> dict[str, Any]:
    has_storage_domain = any(keyword in blockdiag_text for keyword in ("emmc", "nand", "nor", "flash", "storage", "mmc"))
    has_storage_artifact = any("storage" in name for name in manifest.artifacts)
    if not has_storage_domain and not has_storage_artifact:
        return _make_domain("storage_boot", False, "partial", ["storage media has not been structured yet"], ["Please confirm whether the board uses eMMC, NAND, NOR, or another boot/storage path."])
    if has_storage_artifact:
        return _make_domain("storage_boot", False, "sufficient", ["structured storage evidence found"], [])
    return _make_domain(
        "storage_boot",
        False,
        "partial",
        ["storage appears in the block diagram but no structured storage table exists"],
        ["Please provide the storage/boot-media choice and any required control interfaces."],
    )


def _classify_leds_buttons(blockdiag_text: str, gpio_text: str) -> dict[str, Any]:
    block_has_leds = any(keyword in blockdiag_text for keyword in ("led", "button", "reset", "wps"))
    gpio_has_leds = any(keyword in gpio_text for keyword in ("led", "button", "reset", "wps"))
    if block_has_leds and gpio_has_leds:
        return _make_domain("leds_buttons", False, "sufficient", ["LED/button evidence found"], [])
    if block_has_leds or gpio_has_leds:
        return _make_domain(
            "leds_buttons",
            False,
            "partial",
            ["LED/button evidence exists but may be incomplete"],
            ["Please confirm LED channels/polarity and any reset/WPS button wiring that must appear in DTS."],
        )
    return _make_domain("leds_buttons", False, "partial", ["LED/button inventory has not been structured yet"], ["Please provide LED/button mapping if the board uses them."])


def _classify_tod(blockdiag_text: str, gpio_text: str) -> dict[str, Any]:
    if any(keyword in blockdiag_text for keyword in ("tod", "1pps", "8k")) or any(keyword in gpio_text for keyword in ("tod", "1pps", "8k")):
        return _make_domain("tod_timing", False, "partial", ["timing/TOD signals may exist but are not yet structured"], ["Please confirm whether TOD/1PPS/8k timing signals need DTS nodes and provide their pin mapping if so."])
    return _make_domain("tod_timing", False, "sufficient", ["no TOD/timing evidence detected"], [])


def _make_domain(name: str, blocking: bool, status: str, reasons: list[str], questions: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "blocking": blocking,
        "status": status,
        "reasons": reasons,
        "questions": questions,
    }


def _rows_text(rows: list[dict[str, Any]]) -> str:
    return " ".join(
        " ".join("" if value is None else str(value).lower() for value in row.values())
        for row in rows
        if isinstance(row, dict)
    )


# ---------------------------------------------------------------------------
# Structured ClarificationRequest generation
# ---------------------------------------------------------------------------


def _classify_cr_domain(domain_name: str, question: str) -> str:
    """Map a gap domain name + question text to a ClarificationRequest domain."""
    q = question.lower()
    for keywords, domain in _DOMAIN_KEYWORDS:
        if any(kw in q for kw in keywords):
            return domain
    return _DOMAIN_NAME_MAP.get(domain_name, "general")


def _infer_choices(domain: str, question: str) -> list[str]:
    """Infer predefined answer choices from domain and question content."""
    q = question.lower()
    if "confirm whether" in q or "confirm if" in q:
        return ["yes", "no"]
    if domain == "chip_features":
        return ["enable", "disable"]
    if domain == "memory_config" and "ddr type" in q:
        return ["DDR3", "DDR4", "LPDDR4", "LPDDR4X"]
    if "emmc" in q and "nand" in q:
        return ["eMMC", "NAND", "NOR"]
    return []


def _build_evidence_context(domain_name: str, reasons: list[str], spec: dict[str, Any]) -> str:
    """Summarise available evidence for a gap."""
    parts: list[str] = []
    if reasons:
        parts.append("; ".join(reasons))

    if domain_name == "ddr":
        memory = spec.get("memory") or {}
        if memory.get("type"):
            parts.append(f"DDR type: {memory['type']}")
        fields = memory.get("fields") or {}
        if fields.get("ddr_size"):
            parts.append(f"DDR size: {fields['ddr_size']}")
    elif domain_name == "network_topology":
        network = spec.get("network") or {}
        if network.get("rows"):
            parts.append(f"{len(network['rows'])} network rows available")
        interfaces = (spec.get("block_diagram") or {}).get("interfaces", {})
        if interfaces:
            parts.append(f"interfaces: {interfaces}")
    elif domain_name == "gpio_controls":
        gpio_rows = (spec.get("gpio") or {}).get("rows", [])
        if gpio_rows:
            parts.append(f"{len(gpio_rows)} GPIO rows available")

    return "; ".join(parts) if parts else "no evidence available"


_MISSING_EVIDENCE_MAP: dict[str, str] = {
    "ddr": "DDR configuration parameters (type, size, width, part number, memcfg macro)",
    "network_topology": "Structured network topology table with port roles and PHY mapping",
    "gpio_controls": "GPIO/control pin table with pin assignments and functions",
    "pcie_wifi": "PCIe/Wi-Fi control pin assignments and regulator information",
    "leds_buttons": "LED channel mapping with polarity and button wiring details",
    "storage_boot": "Boot/storage media selection and control interfaces",
    "tod_timing": "TOD/1PPS/8kHz timing signal pin mapping",
    "project_identity": "Project identity and output configuration",
    "block_diagram": "Structured block diagram inventory",
}


def _build_missing_evidence(domain_name: str, question: str) -> str:
    """Describe what evidence is still needed."""
    return _MISSING_EVIDENCE_MAP.get(domain_name, question)


def gaps_to_clarification_requests(
    gaps: list[dict[str, Any]],
    spec: dict[str, Any],
) -> list[ClarificationRequest]:
    """Convert sufficiency gap items into structured ClarificationRequest objects.

    *gaps* is the ``domains`` list produced by :func:`build_sufficiency_report`
    (or ``_build_spec_gaps``).  Only domains whose status is **not** ``"sufficient"``
    produce requests.
    """
    requests: list[ClarificationRequest] = []
    domain_counters: dict[str, int] = {}

    for gap in gaps:
        if gap.get("status") == "sufficient":
            continue

        domain_name = gap.get("name", "")
        blocking = gap.get("blocking", False)
        questions = gap.get("questions", [])
        reasons = gap.get("reasons", [])

        for question in questions:
            cr_domain = _classify_cr_domain(domain_name, question)
            idx = domain_counters.get(cr_domain, 0)
            domain_counters[cr_domain] = idx + 1

            requests.append(ClarificationRequest(
                id=f"cr-{cr_domain}-{idx:03d}",
                blocking=blocking,
                domain=cr_domain,
                question=question,
                choices=_infer_choices(cr_domain, question),
                evidence_context=_build_evidence_context(domain_name, reasons, spec),
                missing_evidence=_build_missing_evidence(domain_name, question),
                status="pending",
            ))

    return requests


def build_clarification_report(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a structured clarification report with ClarificationRequest objects.

    This is a standalone entry-point that only requires *spec* (no folder or
    manifest).  It performs its own gap analysis and converts every gap into a
    :class:`ClarificationRequest`.

    Returns::

        {
            "readiness": {"ready": bool, "score": float},
            "gaps": [...],
            "clarification_requests": [ClarificationRequest, ...],
            "summary": {
                "total_questions": int,
                "blocking": int,
                "non_blocking": int,
                "by_domain": {"chip_features": 3, "gpio_assignment": 2, ...}
            }
        }
    """
    all_gaps = _build_spec_gaps(spec)

    sufficient_count = sum(1 for g in all_gaps if g["status"] == "sufficient")
    total_count = len(all_gaps) or 1
    blocking_insufficient = any(
        g["blocking"] and g["status"] != "sufficient" for g in all_gaps
    )

    crs = gaps_to_clarification_requests(all_gaps, spec)

    blocking_crs = sum(1 for cr in crs if cr.blocking)
    non_blocking_crs = sum(1 for cr in crs if not cr.blocking)
    by_domain: dict[str, int] = {}
    for cr in crs:
        by_domain[cr.domain] = by_domain.get(cr.domain, 0) + 1

    return {
        "readiness": {
            "ready": not blocking_insufficient,
            "score": round(sufficient_count / total_count, 2),
        },
        "gaps": [g for g in all_gaps if g["status"] != "sufficient"],
        "clarification_requests": crs,
        "summary": {
            "total_questions": len(crs),
            "blocking": blocking_crs,
            "non_blocking": non_blocking_crs,
            "by_domain": by_domain,
        },
    }


def _build_spec_gaps(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Lightweight gap analysis that works from *spec* alone (no folder/manifest)."""
    blockdiag = spec.get("block_diagram") or {}
    blockdiag_rows = blockdiag.get("rows", [])
    blockdiag_interfaces = blockdiag.get("interfaces", {})

    memory = spec.get("memory") or {}
    network = spec.get("network") or {}
    network_rows = network.get("rows", [])
    gpio = spec.get("gpio") or {}
    gpio_rows = gpio.get("rows", [])

    blockdiag_text = _rows_text(blockdiag_rows)
    if blockdiag_interfaces:
        blockdiag_text += " " + " ".join(str(v).lower() for v in blockdiag_interfaces.values())
    gpio_text = _rows_text(gpio_rows)

    gaps: list[dict[str, Any]] = []

    # -- block diagram --
    if blockdiag_rows or blockdiag_interfaces:
        gaps.append(_make_domain("block_diagram", True, "sufficient",
            ["block diagram evidence found"], []))
    else:
        gaps.append(_make_domain("block_diagram", True, "missing",
            ["no structured block diagram inventory was found"],
            ["Please provide a block diagram table listing interfaces such as DDR, "
             "WAN/SFP, xPHY/LAN, PCIe/Wi-Fi, USB, storage, LEDs, buttons, I2C devices, and TOD."]))

    # -- DDR / memory --
    ddr_fields = memory.get("fields") or {}
    if ddr_fields.get("memcfg_macro"):
        gaps.append(_make_domain("ddr", True, "sufficient", ["memcfg_macro provided"], []))
    elif memory.get("type") or ddr_fields.get("ddr_type"):
        gaps.append(_make_domain("ddr", True, "partial",
            ["DDR type is known but full configuration is incomplete"],
            ["Please provide the DDR memcfg macro or enough board DDR settings "
             "(size, part number, MCB/meminit) to derive it."]))
    elif any(kw in blockdiag_text for kw in ("ddr", "lpddr")):
        gaps.append(_make_domain("ddr", True, "partial",
            ["DDR mentioned in evidence but not configured"],
            ["Please provide DDR type, size, width, part number, and memcfg/MCB setting."]))
    else:
        gaps.append(_make_domain("ddr", True, "missing",
            ["no DDR evidence found"],
            ["Please provide DDR configuration details."]))

    # -- network --
    if network_rows:
        gaps.append(_make_domain("network_topology", True, "sufficient",
            [f"{len(network_rows)} network rows found"], []))
    elif any(kw in blockdiag_text for kw in ("wan", "lan", "ethernet", "sfp", "xphy", "serdes", "gphy")):
        gaps.append(_make_domain("network_topology", True, "partial",
            ["network interfaces appear in evidence but structured table is missing"],
            ["Please provide a structured network/port mapping table with "
             "WAN/LAN roles, PHY handles, and media type."]))
    else:
        gaps.append(_make_domain("network_topology", True, "missing",
            ["no network topology evidence found"],
            ["Please provide network topology information."]))

    # -- GPIO --
    structured_gpio = [r for r in gpio_rows if isinstance(r, dict) and r.get("pin_or_gpio")]
    if structured_gpio:
        gaps.append(_make_domain("gpio_controls", True, "sufficient",
            [f"{len(structured_gpio)} GPIO rows found"], []))
    elif gpio_rows:
        gaps.append(_make_domain("gpio_controls", True, "partial",
            ["GPIO evidence exists but not fully structured"],
            ["Please provide a structured GPIO/control-pin table."]))
    else:
        gaps.append(_make_domain("gpio_controls", True, "missing",
            ["no GPIO/control evidence found"],
            ["Please provide GPIO/control pin information for reset, SFP, LEDs, "
             "Wi-Fi/PCIe control, and similar board signals."]))

    # -- PCIe / Wi-Fi --
    if any(kw in blockdiag_text for kw in ("pcie", "wifi", "wlan", "radio")):
        if any(kw in gpio_text for kw in ("pcie", "wifi", "wlan", "rf_disable", "pewake")):
            gaps.append(_make_domain("pcie_wifi", False, "sufficient",
                ["PCIe/Wi-Fi control evidence found"], []))
        else:
            gaps.append(_make_domain("pcie_wifi", False, "partial",
                ["PCIe/Wi-Fi in block diagram but control pins incomplete"],
                ["Please provide PCIe/Wi-Fi control pins "
                 "(power enable, PERST#, RF_DISABLE, PEWAKE)."]))
    else:
        gaps.append(_make_domain("pcie_wifi", False, "sufficient",
            ["no PCIe/Wi-Fi interfaces declared"], []))

    # -- LEDs / buttons --
    block_has = any(kw in blockdiag_text for kw in ("led", "button", "reset", "wps"))
    gpio_has = any(kw in gpio_text for kw in ("led", "button", "reset", "wps"))
    if block_has and gpio_has:
        gaps.append(_make_domain("leds_buttons", False, "sufficient",
            ["LED/button evidence found"], []))
    elif block_has or gpio_has:
        gaps.append(_make_domain("leds_buttons", False, "partial",
            ["LED/button evidence incomplete"],
            ["Please confirm LED channels/polarity and button wiring."]))
    else:
        gaps.append(_make_domain("leds_buttons", False, "partial",
            ["LED/button inventory not structured"],
            ["Please provide LED/button mapping if the board uses them."]))

    return gaps
