"""
Agent B: Connectivity Auditor — 跟線追蹤，跨頁/跨 PDF，lane swap 偵測

直接模式：系統化地追蹤 GPIO 表中的每個信號
Agent 模式：透過 Copilot SDK agent 智慧追蹤（未來擴充）
"""

from __future__ import annotations

import asyncio
import csv
import logging
import re
from pathlib import Path
from typing import Any

from dtsbuild.pcie_utils import (
    gpio_row_signal_name,
    is_grouped_pcie_wifi_power_signal,
    is_pcie_wifi_signal_name,
)
from dtsbuild.schema import HardwareSchema, Provenance
from dtsbuild.schema_io import save_schema, load_schema
from dtsbuild.tables import read_table_rows
from .tools.tracing import (
    trace_net, trace_cross_pdf,
    detect_lane_swap, lookup_refdes,
    _normalize_trace_token, _is_endpoint_pin_hint,
)
from .tools.schema_ops import write_signal, write_device, write_dts_hint

logger = logging.getLogger(__name__)

# ── Signal role classification patterns ─────────────────────────────

_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^UART", re.IGNORECASE), "UART"),
    (re.compile(r"^SER_LED|^CPU_Service", re.IGNORECASE), "LED_CONTROL"),
    (re.compile(r"LED", re.IGNORECASE), "LED"),
    (re.compile(r"^GPHY|^SGMII", re.IGNORECASE), "ETHERNET_PHY"),
    (re.compile(r"^RBR\b", re.IGNORECASE), "RESET_BUTTON"),
    (re.compile(r"^PCIE|^RF_|GRFIC", re.IGNORECASE), "PCIE_WIFI"),
    (re.compile(r"^I2C|^SCL|^SDA", re.IGNORECASE), "I2C"),
    (re.compile(r"^USB", re.IGNORECASE), "USB"),
    (re.compile(r"PS_EN$|PWR", re.IGNORECASE), "POWER_CONTROL"),
    (re.compile(r"^SFP|^WAN_SFP|^WAN_XCVR", re.IGNORECASE), "SFP"),
    (re.compile(r"^NAND", re.IGNORECASE), "NAND"),
    (re.compile(r"^EMMC", re.IGNORECASE), "EMMC"),
    (re.compile(r"^SPIS", re.IGNORECASE), "SPI"),
    (re.compile(r"^STRAP|^SW_DDR|^SW_STRAP|^SW Boot", re.IGNORECASE), "STRAP"),
    (re.compile(r"RESET", re.IGNORECASE), "RESET"),
]

# Components worth recording as Device entries
_NOTABLE_PREFIXES = {"U", "J", "T"}

_TESTPOINT_RE = re.compile(r"^TP\d+[A-Z]?$")
_SPLIT_UNIT_RE = re.compile(r"^U\d+[A-Z]$")
_UNKNOWN_PARTS = {"UNKNOWN", "?", "N/A", "TBD"}
_CONNECTOR_KEEP_HINTS = (
    "MODULE",
    "SOCKET",
    "EXPANDER",
    "EEPROM",
    "FLASH",
    "PHY",
    "SFP",
    "QSFP",
    "MINIPCI",
    "MINI PCI",
    "M.2",
)
_TRANSFORMER_KEEP_HINTS = (
    "RJ45",
    "MAG",
    "XFMR",
    "TRANSFORM",
    "CENTER TAP",
    "MDI",
    "LAN",
)
_PINMAP_CONTEXT_HINTS = (
    "VDD",
    "VSS",
    "AVDD",
    "DVDD",
    "RFU",
    "NC_",
    "LAYOUT NOTES",
    "10DEGREE",
    " MIL",
    "BGA",
    "BALL",
)
_NON_DTS_PART_PATTERNS = (
    re.compile(r"^SN74LVC1G(?:08|11)$"),
    re.compile(r"^TPS\d+"),
    re.compile(r"^FP\d+"),
)
_POWER_HELPER_HINTS = (
    "P_GOOD",
    "PGOOD",
    "POWER SEQUENCER",
    "PSEQ",
    "VSEQ",
    "FLAG",
    "WIFI POWER ENABLES",
    "MAIN SUPPLY",
)
_REGULATOR_HINTS = (
    "VIN",
    "VOUT",
    "LX",
    "BOOT",
    "PG",
    "PGOOD",
    "P_GOOD",
    "FB",
    "MAIN SUPPLY",
    "POWER MODULE",
    "VDD_PG",
)


def _has_known_part_number(part_number: str | None) -> bool:
    """True when *part_number* carries useful device identity."""
    if not part_number:
        return False
    return part_number.strip().upper() not in _UNKNOWN_PARTS


def _context_looks_like_pinmap_noise(context: str) -> bool:
    """Best-effort check for BGA ball-table / layout-note OCR noise."""
    upper = context.upper()
    if any(marker in upper for marker in ("LAYOUT NOTES", "10DEGREE", " MIL")):
        return True

    power_hits = sum(upper.count(marker) for marker in _PINMAP_CONTEXT_HINTS)
    net_like_hits = sum(
        1
        for token in upper.split()
        if "_" in token or "/" in token
    )
    return power_hits >= 2 and net_like_hits >= 2


def _should_keep_connector_candidate(
    part_number: str | None,
    compatible: str | None,
    context: str,
) -> bool:
    """Keep only connector refs that look like runtime-relevant attachments."""
    if compatible:
        return True
    if not _has_known_part_number(part_number):
        return False

    upper = context.upper()
    return any(hint in upper for hint in _CONNECTOR_KEEP_HINTS)


def _pick_audit_entry(entries: Any) -> dict[str, Any] | None:
    """Pick the most useful refdes entry for audit-time device classification."""
    if isinstance(entries, dict):
        return entries
    if not isinstance(entries, list):
        return None

    candidates = [entry for entry in entries if isinstance(entry, dict)]
    if not candidates:
        return None

    def _entry_key(entry: dict[str, Any]) -> tuple[int, int]:
        part_known = 1 if _has_known_part_number(entry.get("part_number")) else 0
        looks_clean = 0 if _context_looks_like_pinmap_noise(entry.get("context", "")) else 1
        return (part_known, looks_clean)

    return max(candidates, key=_entry_key)


def _device_lookup_context(info: dict[str, Any], fallback: str) -> str:
    """Return the richest available lookup context for device classification."""
    lookup_context = (info.get("lookup_context") or "").strip()
    if lookup_context:
        return lookup_context
    return fallback


def _is_non_dts_helper_device(
    *,
    part_number: str | None,
    normalized_part_number: str | None,
    compatible: str | None,
    context: str,
) -> bool:
    """Return True for power/helper ICs that should not become DTS unresolved noise."""
    if compatible:
        return False

    upper_context = context.upper()
    normalized = (normalized_part_number or part_number or "").upper()

    if normalized and any(pattern.match(normalized) for pattern in _NON_DTS_PART_PATTERNS):
        if any(hint in upper_context for hint in (*_POWER_HELPER_HINTS, *_REGULATOR_HINTS)):
            return True

    regulator_hits = sum(1 for hint in _REGULATOR_HINTS if hint in upper_context)
    if regulator_hits >= 4:
        return True

    if not _has_known_part_number(part_number):
        required = ("VIN", "PG")
        if all(token in upper_context for token in required) and any(
            hint in upper_context for hint in ("BOOT", "LX", "VDD_PG", "PGOOD", "P_GOOD")
        ):
            return True

    return False


def _determine_device_status(
    *,
    part_number: str,
    compatible: str | None,
    bus: str | None,
    address: str | None,
) -> str:
    """Return whether a device is fully resolved enough to be VERIFIED."""
    if not _has_known_part_number(part_number):
        return "INCOMPLETE"

    part_upper = part_number.upper()
    compatible_upper = (compatible or "").upper()
    needs_bus_and_address = (
        "PCA9555" in compatible_upper
        or "TCA9555" in part_upper
        or "PCA9555" in part_upper
    )
    if needs_bus_and_address and (not bus or not address):
        return "INCOMPLETE"

    return "VERIFIED"


def _classify_signal_role(signal_name: str) -> str:
    """Classify a GPIO signal name into a semantic role."""
    if is_pcie_wifi_signal_name(signal_name):
        return "PCIE_WIFI"
    for pattern, role in _ROLE_PATTERNS:
        if pattern.search(signal_name):
            return role
    return "GENERAL_GPIO"


# ── GPIO CSV reading ────────────────────────────────────────────────

def _read_gpio_table(gpio_table: Path) -> list[dict[str, str]]:
    """Read the GPIO table CSV and return rows with a usable signal name.

    Keeps BGW720 PCIe/Wi-Fi rows whose effective label may live in ``name``
    instead of ``signal``.
    """
    rows: list[dict[str, str]] = []
    with open(gpio_table, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sig = gpio_row_signal_name(row)
            name = (row.get("name") or "").strip()
            if not sig:
                continue
            if name.lower() == "not used" and not is_pcie_wifi_signal_name(sig):
                continue
            rows.append(row)
    return rows


def _read_blockdiag_table(blockdiag_table: Path) -> list[dict[str, str]]:
    """Read optional block diagram CSV rows."""
    if not blockdiag_table.exists():
        return []
    with open(blockdiag_table, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _read_optional_table(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    return read_table_rows(path)


def _has_present_truth(row: dict[str, str]) -> bool:
    return (row.get("present") or "").strip().lower() in {"true", "yes", "1", "y"}


def _proven_network_gphy_prefixes(network_rows: list[dict[str, str]]) -> set[str]:
    prefixes: set[str] = set()
    for row in network_rows:
        if not _has_present_truth(row):
            continue
        phy_handle = (row.get("phy_handle") or "").strip().lower()
        match = re.fullmatch(r"gphy(\d+)", phy_handle)
        if match:
            prefixes.add(f"GPHY{match.group(1)}")
    return prefixes


def _lane_swap_candidate_gphy_prefixes(network_rows: list[dict[str, str]]) -> set[str]:
    prefixes: set[str] = set()
    for row in network_rows:
        present = (row.get("present") or "").strip().lower()
        if present not in {"true", "yes", "1", "y", "inferred"}:
            continue
        phy_handle = (row.get("phy_handle") or "").strip().lower()
        match = re.fullmatch(r"gphy(\d+)", phy_handle)
        if match:
            prefixes.add(f"GPHY{match.group(1)}")
    return prefixes


def _network_row_provenance(row: dict[str, str]) -> dict[str, Any]:
    source = (row.get("source") or "").strip()
    pdf_id = source.split(":", 1)[0] if source else "network_table"
    page_match = re.search(r"pages?\s+([0-9,\s]+)", source, re.IGNORECASE)
    if page_match:
        pages = [int(match) for match in re.findall(r"\d+", page_match.group(1))] or [0]
    else:
        pages = [0]
    refs = [
        value
        for value in (
            row.get("name", "").strip(),
            row.get("phy_handle", "").strip(),
            row.get("switch_port", "").strip(),
        )
        if value
    ]
    return {
        "pdfs": [pdf_id],
        "pages": pages,
        "refs": refs,
        "method": "network_table",
        "confidence": 0.8,
    }


def _audit_network_topology(network_rows: list[dict[str, str]], schema_path: str) -> None:
    wrote_xport = False

    for row in network_rows:
        if not _has_present_truth(row):
            continue

        name = (row.get("name") or "").strip()
        role = (row.get("role") or "").strip()
        phy_handle = (row.get("phy_handle") or "").strip().lower()
        phy_group = (row.get("phy_group") or "").strip()
        switch_port = (row.get("switch_port") or "").strip()
        provenance = _network_row_provenance(row)

        if switch_port:
            write_dts_hint(
                schema_path=schema_path,
                target=f"&switch0/ports/{switch_port}",
                property="status",
                value='"okay"',
                reason=(
                    f"Stable topology row {name or switch_port}: role={role or 'UNKNOWN'}, "
                    f"phy_handle={phy_handle or 'UNKNOWN'}, phy_group={phy_group or 'UNKNOWN'}"
                ),
                provenance=provenance,
            )

        gphy_match = re.fullmatch(r"gphy(\d+)", phy_handle)
        if gphy_match:
            write_dts_hint(
                schema_path=schema_path,
                target="&ethphytop",
                property=f"xphy{int(gphy_match.group(1))}-enabled",
                reason=(
                    f"Stable topology row {name or phy_handle}: switch_port={switch_port or 'UNKNOWN'}, "
                    f"phy_group={phy_group or 'UNKNOWN'}"
                ),
                provenance=provenance,
            )

        if not wrote_xport:
            write_dts_hint(
                schema_path=schema_path,
                target="&xport",
                property="status",
                value='"okay"',
                reason="Stable network topology confirms Ethernet transport is used on this board.",
                provenance=provenance,
            )
            wrote_xport = True


def _write_gpio_table_signal(
    signal_name: str,
    gpio_pin: str,
    role: str,
    schema_path: str,
) -> None:
    """Record a signal that is directly evidenced by the GPIO table itself."""
    write_signal(
        schema_path=schema_path,
        name=signal_name,
        soc_pin=gpio_pin,
        traced_path=f"{signal_name} ↔ {gpio_pin} (GPIO table)",
        role=role,
        status="VERIFIED",
        provenance={
            "pdfs": ["gpio_table"],
            "pages": [0],
            "refs": [],
            "method": "gpio_table",
            "confidence": 0.8,
        },
    )
    logger.debug("Recorded %s from GPIO table evidence (%s)", signal_name, role)


def _infer_soc_pin_from_line(line: str, token: str) -> str | None:
    """Best-effort extraction of the SoC pad token nearest to *token*."""
    token_match = re.search(re.escape(token), line, re.IGNORECASE)
    if token_match is None:
        return None

    pad_matches = list(re.finditer(r"\b[A-Z]{1,2}\d{1,2}\b", line))
    if not pad_matches:
        return None

    after = [m.group(0) for m in pad_matches if m.start() >= token_match.end()]
    if after:
        return after[0]

    before = [m.group(0) for m in pad_matches if m.end() <= token_match.start()]
    if before:
        return before[-1]
    return None


def _find_token_occurrence(
    indices: dict[str, Any],
    token: str,
) -> tuple[str, int, str] | None:
    """Return the first ``(pdf_id, page_num, line)`` containing *token*."""
    needle = token.upper()
    for pdf_id in _all_pdf_ids(indices):
        for page_num, content in _pages_for(indices, pdf_id).items():
            for line in content.splitlines():
                if needle in line.upper():
                    return pdf_id, int(page_num), line
    return None


def _find_page_with_tokens(
    indices: dict[str, Any],
    pdf_id: str,
    required_tokens: tuple[str, ...],
) -> tuple[int, str] | None:
    """Return the first page whose content contains all *required_tokens*."""
    for page_num, content in _pages_for(indices, pdf_id).items():
        haystack = content.upper()
        if all(token.upper() in haystack for token in required_tokens):
            return int(page_num), content
    return None


def _audit_usb_presence(
    indices: dict[str, Any],
    blockdiag_table: Path | None,
    schema_path: str,
) -> None:
    """Record minimal USB host evidence from blockdiag + schematic page text."""
    if blockdiag_table is None:
        return

    usb_rows = [
        row for row in _read_blockdiag_table(blockdiag_table)
        if str(row.get("domain", "")).strip().lower() == "usb"
        and str(row.get("present", "")).strip().lower() in {"true", "1", "yes"}
    ]
    if not usb_rows:
        return

    required_tokens = (
        ("USB0_PWRON_N", "USB_POWER"),
        ("USB1_PWRON", "USB_POWER"),
        ("USB0_SSRXN", "USB_SUPERSPEED"),
    )
    evidence: list[tuple[str, str, str, int, str, str]] = []
    for token, role in required_tokens:
        hit = _find_token_occurrence(indices, token)
        if hit is None:
            logger.info("USB audit skipped: missing schematic token %s", token)
            return
        pdf_id, page_num, line = hit
        soc_pin = _infer_soc_pin_from_line(line, token)
        if soc_pin is None:
            logger.info("USB audit skipped: unable to infer SoC pin for %s", token)
            return
        evidence.append((token, role, pdf_id, page_num, line, soc_pin))

    for token, role, pdf_id, page_num, _line, soc_pin in evidence:
        write_signal(
            schema_path=schema_path,
            name=token,
            soc_pin=soc_pin,
            traced_path=f"{token} ↔ {soc_pin} (USB schematic page {page_num})",
            role=role,
            status="VERIFIED",
            provenance={
                "pdfs": [pdf_id, "blockdiag_table"],
                "pages": [page_num, 0],
                "refs": [token],
                "method": "blockdiag+page_scan",
                "confidence": 0.76,
            },
        )
    logger.info("Recorded %d USB signals from blockdiag + schematic evidence", len(evidence))


def _audit_usb_port_policy(
    indices: dict[str, Any],
    schema_path: str,
) -> None:
    """Record usb_ctrl port policy when only the USB0 external path is populated."""
    hit = _find_page_with_tokens(
        indices,
        "mainboard",
        (
            "USB0_VBUS",
            "USB0_DP",
            "USB0_DM",
            "USB0_SSRXN",
            "USB1_DP",
            "USB1_DM",
            "USB1_ID",
            "USB1_PWRON",
        ),
    )
    if hit is None:
        return

    page_num, content = hit
    haystack = content.upper()
    if any(token in haystack for token in ("USB1_VBUS", "USB1_SSRXN", "USB1_SSTXN", "USB1_SSTXP")):
        return

    write_dts_hint(
        schema_path=schema_path,
        target="&usb_ctrl",
        property="port1-disabled",
        reason=(
            "Mainboard USB page shows a populated USB0 connector/power path, "
            "while the USB1 lane has no matching VBUS or superspeed path."
        ),
        provenance={
            "pdfs": ["mainboard"],
            "pages": [page_num],
            "refs": [
                "USB0_VBUS",
                "USB0_DP",
                "USB0_DM",
                "USB0_SSRXN",
                "USB1_DP",
                "USB1_DM",
                "USB1_ID",
                "USB1_PWRON",
            ],
            "method": "page_scan",
            "confidence": 0.74,
        },
    )
    logger.info("Recorded usb_ctrl port1-disabled hint from mainboard page %d", page_num)


def _audit_uart_presence(
    indices: dict[str, Any],
    schema_path: str,
) -> None:
    """Record uart0 evidence only when TX/RX and 4-pin header context co-occur."""
    hit = _find_page_with_tokens(
        indices,
        "mainboard",
        ("UART0_SOUT", "UART0_SIN", "GPIO_14", "GPIO_15", "P301V-04-SMT-G1-RT"),
    )
    if hit is None:
        return

    page_num, _content = hit
    for signal_name, soc_pin in (
        ("UART0_SOUT", "GPIO_14"),
        ("UART0_SIN", "GPIO_15"),
    ):
        write_signal(
            schema_path=schema_path,
            name=signal_name,
            soc_pin=soc_pin,
            traced_path=(
                f"{signal_name} ↔ {soc_pin} (mainboard page {page_num}, "
                "4-pin header context)"
            ),
            role="UART",
            status="VERIFIED",
            provenance={
                "pdfs": ["mainboard"],
                "pages": [page_num],
                "refs": ["P301V-04-SMT-G1-RT"],
                "method": "page_scan",
                "confidence": 0.72,
            },
        )
    logger.info("Recorded uart0 TX/RX from mainboard page %d header context", page_num)


def _audit_wan_sfp_i2c_bus(
    indices: dict[str, Any],
    schema_path: str,
) -> None:
    """Record wan_sfp i2c-bus when SFP cage I2C wiring is explicit in schematic OCR."""
    hit = _find_page_with_tokens(
        indices,
        "mainboard",
        ("U6", "I2C ADDRESS", "0XA0/A2", "SFP_SCL", "SFP_SDA"),
    )
    if hit is None:
        return

    page_num, content = hit
    haystack = content.upper()
    bus_num: str | None = None
    for pattern in (r"\bSDA[_\s-]?(\d+)\b", r"\bSCL[_\s-]?(\d+)\b"):
        match = re.search(pattern, haystack)
        if match:
            bus_num = match.group(1)
            break
    if bus_num is None:
        return

    write_dts_hint(
        schema_path=schema_path,
        target="wan_sfp",
        property="i2c-bus",
        value=f"<&i2c{bus_num}>",
        reason=(
            "SFP cage U6 exposes EEPROM I2C address 0xA0/A2 and the schematic "
            f"routes SFP_SCL/SFP_SDA to i2c{bus_num}."
        ),
        provenance={
            "pdfs": ["mainboard"],
            "pages": [page_num],
            "refs": ["U6", "SFP_SCL", "SFP_SDA", f"SDA_{bus_num}", "0xA0/A2"],
            "method": "page_scan",
            "confidence": 0.78,
        },
    )
    logger.info("Recorded wan_sfp i2c-bus hint from mainboard page %d", page_num)


# ── Schema bootstrap ────────────────────────────────────────────────

def _ensure_schema(schema_path: Path) -> None:
    """Create an empty schema file if one does not exist."""
    if schema_path.exists():
        return
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema = HardwareSchema(project="BGW720", chip="BCM68575")
    save_schema(schema, schema_path)
    logger.info("Created new schema at %s", schema_path)


def _reset_schema(schema_path: Path) -> None:
    """Start a fresh audit schema so reruns do not append stale records."""
    project = "BGW720"
    chip = "BCM68575"

    if schema_path.exists():
        try:
            existing = load_schema(schema_path)
            project = existing.project
            chip = existing.chip
        except Exception:
            logger.warning("Failed to reuse schema header at %s", schema_path)

    schema_path.parent.mkdir(parents=True, exist_ok=True)
    save_schema(HardwareSchema(project=project, chip=chip), schema_path)
    logger.info("Reset schema at %s", schema_path)


# ── Index helpers ────────────────────────────────────────────────────

def _all_pdf_ids(indices: dict[str, Any]) -> list[str]:
    """Return sorted list of PDF identifiers from page_indices."""
    return sorted(indices.get("page_indices", {}).keys())


def _tag_index_for(indices: dict[str, Any], pdf_id: str) -> dict:
    """Return the tag_index sub-dict for *pdf_id*.

    The unified tag_index maps tag → [{pdf_id, page, …}].
    Restructure into {tag → [entries]} for the given PDF.
    """
    per_pdf: dict[str, list] = {}
    for tag, entries in indices.get("tag_index", {}).items():
        matching = [e for e in entries if isinstance(e, dict) and e.get("pdf_id") == pdf_id]
        if matching:
            per_pdf[tag] = matching
    return per_pdf


def _pages_for(indices: dict[str, Any], pdf_id: str) -> dict[int, str]:
    """Return {page_num: text} for *pdf_id*."""
    return indices.get("page_indices", {}).get(pdf_id, {})


def _refdes_index_for(indices: dict[str, Any], pdf_id: str) -> dict:
    """Return refdes_index entries relevant to *pdf_id*."""
    per_pdf: dict[str, Any] = {}
    for ref, entries in indices.get("refdes_index", {}).items():
        if isinstance(entries, list):
            matching = [e for e in entries if isinstance(e, dict) and e.get("pdf_id") == pdf_id]
            if matching:
                per_pdf[ref] = matching[0] if len(matching) == 1 else matching
        elif isinstance(entries, dict):
            if entries.get("pdf_id") == pdf_id:
                per_pdf[ref] = entries
    return per_pdf


# ── Signal tracing ──────────────────────────────────────────────────

def _trace_signal(
    signal_name: str,
    gpio_pin: str,
    role: str,
    indices: dict[str, Any],
    schema_path: str,
    aliases: list[str] | None = None,
) -> None:
    """Trace a single signal across all PDFs and write to schema."""
    pdf_ids = _all_pdf_ids(indices)
    all_pages_found: list[int] = []
    all_components: list[str] = []
    signal_segments: list[str] = []
    page_hop_segments: list[str] = []
    endpoint_hints: list[str] = []
    component_segments: list[str] = []
    source_pdfs: list[str] = []
    crosses_pdf = False
    connector_hit: str | None = None
    used_deep_trace = False
    primary_has_components = False

    connector_index = indices.get("connector_index", {})
    all_tag_indices = {pid: _tag_index_for(indices, pid) for pid in pdf_ids}
    global_tag_index = indices.get("tag_index", {})
    seed_signals = list(dict.fromkeys([signal_name, *(aliases or [])]))

    def _append_unique(target: list[str], value: str | None) -> None:
        if value and value not in target:
            target.append(value)

    def _format_component_segment(segment: dict[str, Any], *, include_value: bool) -> str:
        comp = segment.get("component", "?")
        pin = segment.get("pin")
        label = f"{comp}.Pin{pin}" if pin else comp
        value = segment.get("value")
        if include_value and value and segment.get("passive_role"):
            label = f"{label}({value})"
        return label

    def _rank_follow_on_signal(candidate: str) -> int:
        upper = candidate.upper()
        score = 0
        if upper in global_tag_index:
            score += 5 + len(global_tag_index.get(upper, []))
        if any(marker in upper for marker in ("FAULT", "RESET", "SFP", "WAN", "NAND")):
            score += 3
        if upper.startswith(("WAN_", "POR_", "SYS_")):
            score += 2
        if role == "SFP" and any(marker in upper for marker in ("SFP", "WAN", "FAULT", "TX", "RX")):
            score += 4
        if role == "RESET" and "RESET" in upper:
            score += 4
        if role == "NAND" and any(marker in upper for marker in ("NAND", "DAT", "ALE", "CLE", "CE", "RB")):
            score += 4
        if _is_endpoint_pin_hint(candidate):
            score += 2
        if upper.startswith("GPIO_"):
            score -= 5
        if upper in {"SCL", "SDA", "BSCL", "BSDA", "TEST", "JTAG"}:
            score -= 3
        return score

    def _record_trace_result(
        current_signal: str,
        result: dict[str, Any],
        *,
        pdf_id: str,
        depth: int,
    ) -> None:
        nonlocal connector_hit, primary_has_components

        all_pages_found.extend(result["pages_found"])
        all_components.extend(result["connected_components"])
        _append_unique(source_pdfs, pdf_id)

        local_has_components = bool(result["path_segments"])
        if depth == 0 and local_has_components:
            primary_has_components = True

        if depth > 0 or not primary_has_components:
            _append_unique(signal_segments, current_signal)
            for hop in result.get("page_hops", []):
                hop_label = f"[{pdf_id}:{hop['from']}→{hop['to']}]"
                _append_unique(page_hop_segments, hop_label)

        for related in result.get("related_signals", []):
            if _is_endpoint_pin_hint(related):
                _append_unique(endpoint_hints, related)

        include_value = depth > 0 or not primary_has_components
        for segment in result["path_segments"]:
            comp = segment.get("component", "?")
            if comp.startswith("J") and comp in connector_index:
                connector_hit = comp
            _append_unique(
                component_segments,
                _format_component_segment(segment, include_value=include_value),
            )

    pending_signals: list[tuple[str, int]] = [(signal_name, 0)]
    visited_signals: set[str] = set()

    while pending_signals:
        current_signal, depth = pending_signals.pop(0)
        normalized = _normalize_trace_token(current_signal)
        if normalized in visited_signals:
            continue
        visited_signals.add(normalized)

        extra_aliases = seed_signals if depth == 0 else None
        related_candidates: list[str] = []
        found_for_current = False

        for pdf_id in pdf_ids:
            tag_idx = all_tag_indices[pdf_id]
            pages = _pages_for(indices, pdf_id)
            if not pages:
                continue

            result = trace_net(
                current_signal,
                pdf_id,
                tag_idx,
                pages,
                extra_aliases=extra_aliases,
            )

            if not result["pages_found"]:
                continue

            found_for_current = True
            _record_trace_result(current_signal, result, pdf_id=pdf_id, depth=depth)
            related_candidates.extend(result.get("related_signals", []))

        if depth == 0 and not primary_has_components and found_for_current:
            ranked_related = sorted(
                [
                    (idx, candidate)
                    for idx, candidate in enumerate(related_candidates)
                    if _normalize_trace_token(candidate) not in visited_signals
                ],
                key=lambda item: (-_rank_follow_on_signal(item[1]), item[0]),
            )
            for _, candidate in ranked_related[:1]:
                if _rank_follow_on_signal(candidate) < 6:
                    continue
                pending_signals.append((candidate, depth + 1))
                used_deep_trace = True

    # Attempt cross-PDF tracing if a connector was hit
    cross_pdf_dest: str | None = None
    if connector_hit and len(pdf_ids) > 1:
        src_pdf = source_pdfs[0] if source_pdfs else pdf_ids[0]
        xpdf = trace_cross_pdf(
            signal_name, src_pdf, connector_hit,
            connector_index, all_tag_indices,
        )
        if xpdf.get("destination_pdf"):
            crosses_pdf = True
            cross_pdf_dest = xpdf["destination_pdf"]
            cont = xpdf.get("continued_as") or signal_name
            pin_number = xpdf.get("pin_number")
            destination_connector = xpdf.get("destination_connector")
            source_pdfs.append(cross_pdf_dest)
            used_deep_trace = True
            _append_unique(signal_segments, signal_name)
            source_pin = f"{connector_hit}.Pin{pin_number}" if pin_number else connector_hit
            if destination_connector and pin_number:
                continuation = (
                    f"{source_pin} → [{cross_pdf_dest}] "
                    f"{destination_connector}.Pin{pin_number} ({cont})"
                )
            elif destination_connector:
                continuation = f"{source_pin} → [{cross_pdf_dest}] {destination_connector} ({cont})"
            else:
                continuation = f"{source_pin} → [{cross_pdf_dest}] {cont}"
            _append_unique(signal_segments, continuation)

            # Trace the continued signal in the destination PDF
            dst_tag_idx = all_tag_indices.get(cross_pdf_dest, {})
            dst_pages = _pages_for(indices, cross_pdf_dest)
            if dst_pages:
                dst_result = trace_net(cont, cross_pdf_dest, dst_tag_idx, dst_pages)
                _record_trace_result(cont, dst_result, pdf_id=cross_pdf_dest, depth=1)

    # De-duplicate
    all_components = list(dict.fromkeys(all_components))
    signal_segments = list(dict.fromkeys(signal_segments))
    page_hop_segments = list(dict.fromkeys(page_hop_segments))
    endpoint_hints = list(dict.fromkeys(endpoint_hints))
    component_segments = list(dict.fromkeys(component_segments))

    # Build human-readable traced path
    if primary_has_components and not used_deep_trace:
        traced_path = " → ".join(component_segments)
    else:
        combined_segments: list[str] = []
        for segment in signal_segments:
            _append_unique(combined_segments, segment)
        for segment in page_hop_segments:
            _append_unique(combined_segments, segment)
        for segment in endpoint_hints[:2]:
            _append_unique(combined_segments, segment)
        for segment in component_segments:
            _append_unique(combined_segments, segment)

        if combined_segments:
            traced_path = " → ".join(combined_segments)
        elif all_pages_found:
            page_list = ",".join(str(page) for page in sorted(set(all_pages_found)))
            traced_path = f"{signal_name} @ pages {page_list}"
        else:
            traced_path = f"(no trace found for {signal_name})"

    # Determine status
    status: str
    if all_pages_found and (
        component_segments
        or (page_hop_segments and len(endpoint_hints) == 1)
    ):
        status = "VERIFIED"
    elif all_pages_found:
        status = "INCOMPLETE"
    else:
        status = "INCOMPLETE"

    if status == "VERIFIED" and component_segments:
        confidence = 0.82
    elif status == "VERIFIED":
        confidence = 0.68
    elif all_pages_found and (page_hop_segments or endpoint_hints):
        confidence = 0.55
    else:
        confidence = 0.4

    method_parts = ["net_trace"]
    if page_hop_segments or used_deep_trace:
        method_parts.append("cross_page")
    if crosses_pdf:
        method_parts.append("cross_pdf")

    prov = {
        "pdfs": source_pdfs or ["unknown"],
        "pages": sorted(set(all_pages_found)) or [0],
        "refs": all_components[:10],
        "method": "+".join(method_parts),
        "confidence": confidence,
    }

    write_signal(
        schema_path=schema_path,
        name=signal_name,
        soc_pin=gpio_pin,
        traced_path=traced_path,
        role=role,
        status=status,
        provenance=prov,
    )
    logger.debug("Traced %s → %s (%s)", signal_name, status, role)


# ── GPHY lane swap audit ───────────────────────────────────────────

def _audit_gphy_lane_swap(
    indices: dict[str, Any],
    schema_path: str,
    *,
    allowed_prefixes: set[str] | None = None,
) -> None:
    """Detect lane swaps for all GPHY groups and write hints."""
    tag_index = indices.get("tag_index", {})
    pdf_ids = _all_pdf_ids(indices)

    # Discover GPHY prefixes from tag_index
    gphy_prefixes: set[str] = set()
    for tag in tag_index:
        m = re.match(r"(GPHY\d+)", tag)
        if m:
            gphy_prefixes.add(m.group(1))

    if not gphy_prefixes:
        logger.info("No GPHY signals found in tag index — skipping lane swap detection")
        return

    if allowed_prefixes is not None:
        gphy_prefixes &= allowed_prefixes
        if not gphy_prefixes:
            logger.info("No GPHY lane-swap candidates are available from network rows — skipping lane swap detection")
            return

    for prefix in sorted(gphy_prefixes):
        # Run lane swap detection in each PDF that has these signals
        for pdf_id in pdf_ids:
            per_pdf_tag = _tag_index_for(indices, pdf_id)
            pages = _pages_for(indices, pdf_id)
            refdes_idx = _refdes_index_for(indices, pdf_id)

            if not any(prefix in t for t in per_pdf_tag):
                continue

            result = detect_lane_swap(
                prefix, "RJ45", per_pdf_tag, pages, refdes_idx,
            )

            if result.get("swap_detected"):
                prov = {
                    "pdfs": [pdf_id],
                    "pages": [0],
                    "refs": [],
                    "method": "lane_swap_detection",
                    "confidence": 0.7,
                }
                # Collect page numbers from trace paths
                for tp in result.get("trace_paths", []):
                    for comp in tp.get("components", []):
                        prov["refs"].append(comp)

                write_dts_hint(
                    schema_path=schema_path,
                    target=f"&mdio_bus/xphy{prefix.removeprefix('GPHY')}",
                    property="enet-phy-lane-swap",
                    value=f"{prefix}: {result['swap_detail']}",
                    reason=f"Lane swap detected for {prefix}: {result['swap_detail']}",
                    provenance=prov,
                )
                logger.info("Lane swap detected for %s: %s", prefix, result["swap_detail"])

                # Also update signals for this prefix with swap info
                schema = load_schema(Path(schema_path))
                for sig in schema.signals:
                    if sig.name.startswith(prefix):
                        sig.swap_detected = True
                        sig.swap_detail = result["swap_detail"]
                save_schema(schema, Path(schema_path))
            else:
                logger.debug("No lane swap for %s in %s", prefix, pdf_id)


# ── Device audit ────────────────────────────────────────────────────

def _audit_devices(
    indices: dict[str, Any],
    schema_path: str,
) -> None:
    """Write Device records for notable components found in the indices."""
    refdes_index = indices.get("refdes_index", {})
    pdf_ids = _all_pdf_ids(indices)

    seen_refdes: set[str] = set()

    for ref, entries in refdes_index.items():
        ref = (ref or "").upper()
        # Only record ICs, connectors, and magnetics
        if not ref or ref[0] not in _NOTABLE_PREFIXES:
            continue
        if _TESTPOINT_RE.match(ref) or _SPLIT_UNIT_RE.match(ref):
            continue
        if ref in seen_refdes:
            continue
        seen_refdes.add(ref)

        entry = _pick_audit_entry(entries)
        if not entry:
            continue

        context = entry.get("context") or ""
        if ref.startswith("T") and _context_looks_like_pinmap_noise(context):
            continue

        part_number = entry.get("part_number") or ""
        pdf_id = entry.get("pdf_id") or (pdf_ids[0] if pdf_ids else "unknown")
        page = entry.get("page", 0)

        # Use lookup_refdes for richer info
        pages = _pages_for(indices, pdf_id)
        per_pdf_refdes = _refdes_index_for(indices, pdf_id)
        info = lookup_refdes(ref, per_pdf_refdes, pages)

        pn = info.get("part_number") or part_number or "UNKNOWN"
        normalized_pn = info.get("normalized_part_number") or pn
        compatible = info.get("compatible")
        address = info.get("address")
        bus = info.get("bus")
        lookup_context = _device_lookup_context(info, context)

        if ref.startswith("J") and not _should_keep_connector_candidate(pn, compatible, context):
            continue
        if ref.startswith("T") and not compatible and not _has_known_part_number(pn):
            upper_context = context.upper()
            if _context_looks_like_pinmap_noise(context):
                continue
            if not any(hint in upper_context for hint in _TRANSFORMER_KEEP_HINTS):
                continue
        if _is_non_dts_helper_device(
            part_number=pn,
            normalized_part_number=normalized_pn,
            compatible=compatible,
            context=lookup_context,
        ):
            continue

        prov = {
            "pdfs": [pdf_id],
            "pages": [page] if page else [0],
            "refs": [ref],
            "method": "refdes_lookup",
            "confidence": 0.85 if compatible else (0.7 if pn != "UNKNOWN" else 0.3),
        }

        status = _determine_device_status(
            part_number=pn,
            compatible=compatible,
            bus=bus,
            address=address,
        )

        write_device(
            schema_path=schema_path,
            refdes=ref,
            part_number=pn,
            status=status,
            provenance=prov,
            compatible=compatible,
            bus=bus,
            address=address,
        )
        logger.debug("Device %s → %s (%s)", ref, pn, status)


# ── Direct-mode orchestrator ────────────────────────────────────────

async def _audit_direct(
    indices: dict[str, Any],
    gpio_rows: list[dict[str, str]],
    schema_path: Path,
    *,
    blockdiag_table: Path | None = None,
    network_table: Path | None = None,
) -> None:
    """Systematically trace every GPIO signal and write results."""
    sp = str(schema_path)

    logger.info("Starting direct audit of %d GPIO signals", len(gpio_rows))

    # 1. Trace each signal from the GPIO table
    for row in gpio_rows:
        signal_name = gpio_row_signal_name(row)
        gpio_pin = (row.get("pin_or_gpio") or "").strip()
        if not signal_name:
            continue

        signal_aliases = [
            candidate.strip("() ").strip()
            for candidate in signal_name.replace("\r", "\n").splitlines()
            if candidate.strip("() ").strip()
        ]
        signal_aliases = list(dict.fromkeys(signal_aliases))
        signal_name = signal_aliases[0]

        role = _classify_signal_role(signal_name)
        if is_grouped_pcie_wifi_power_signal(signal_name):
            _write_gpio_table_signal(signal_name, gpio_pin, role, sp)
            continue
        try:
            _trace_signal(
                signal_name,
                gpio_pin,
                role,
                indices,
                sp,
                aliases=signal_aliases[1:],
            )
        except Exception:
            logger.exception("Failed to trace signal %s", signal_name)
            # Write an INCOMPLETE record so it's tracked
            write_signal(
                schema_path=sp,
                name=signal_name,
                soc_pin=gpio_pin,
                traced_path=f"(trace failed for {signal_name})",
                role=role,
                status="INCOMPLETE",
                provenance={
                    "pdfs": ["unknown"],
                    "pages": [0],
                    "refs": [],
                    "method": "net_trace",
                    "confidence": 0.0,
                },
            )

    network_rows = _read_optional_table(network_table)

    # 2. Supplemental non-GPIO subsystem evidence
    _audit_usb_presence(indices, blockdiag_table, sp)
    _audit_usb_port_policy(indices, sp)
    _audit_uart_presence(indices, sp)
    _audit_wan_sfp_i2c_bus(indices, sp)
    _audit_network_topology(network_rows, sp)

    # 3. GPHY lane swap detection
    logger.info("Running GPHY lane swap detection")
    _audit_gphy_lane_swap(indices, sp, allowed_prefixes=_lane_swap_candidate_gphy_prefixes(network_rows))

    # 4. Device audit
    logger.info("Running device audit")
    _audit_devices(indices, sp)

    schema = load_schema(schema_path)
    logger.info(
        "Audit complete: %d signals, %d devices, %d hints",
        len(schema.signals), len(schema.devices), len(schema.dts_hints),
    )


# ── Public entry point ──────────────────────────────────────────────

async def run_auditor(
    indices: dict[str, Any],
    gpio_table: Path,
    schema_path: Path,
    *,
    mode: str = "direct",
) -> None:
    """Trace every signal in the GPIO table and write results to schema.

    Args:
        indices: Indices produced by Vision Indexer (run_indexer).
        gpio_table: Path to GPIO table CSV.
        schema_path: Path to the Hardware Schema YAML (created if absent).
        mode: ``"direct"`` for deterministic tracing,
              ``"agent"`` for Copilot SDK (future).
    """
    _ensure_schema(schema_path)
    _reset_schema(schema_path)
    gpio_rows = _read_gpio_table(gpio_table)
    blockdiag_table = gpio_table.parent / "blockdiag.csv"
    network_table = gpio_table.parent / "network.csv"
    logger.info("Read %d usable rows from %s", len(gpio_rows), gpio_table)

    if mode == "direct":
        await _audit_direct(
            indices,
            gpio_rows,
            schema_path,
            blockdiag_table=blockdiag_table,
            network_table=network_table,
        )
    elif mode == "agent":
        logger.warning("Agent mode not yet implemented, falling back to direct")
        await _audit_direct(
            indices,
            gpio_rows,
            schema_path,
            blockdiag_table=blockdiag_table,
            network_table=network_table,
        )
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Use 'direct' or 'agent'.")
