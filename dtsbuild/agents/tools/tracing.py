"""
Net tracing tools for Agent B: Connectivity Auditor.

These tools perform circuit net tracing — following signals from SoC pins
through passive components, across pages and PDFs, to their destinations.
"""

from __future__ import annotations
import re
from typing import Any
from pydantic import BaseModel, Field

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


# ── Known part number → DT compatible string mapping ────────────────

KNOWN_COMPATIBLES: dict[str, str | None] = {
    "TCA9555": "nxp,pca9555",
    "TCA9555PWR": "nxp,pca9555",
    "PCA9555": "nxp,pca9555",
    "PCA9555PW": "nxp,pca9555",
    "SN74HC595": "generic,74hc595",
    "74HC595": "generic,74hc595",
    "SN74LVC1G08": None,
    "SN74LVC1G11": None,
    "HN2436G": None,  # Ethernet transceiver, no DT compatible
    "TPS562203": None,
    "FP6359S6": None,
    "BCM68575": "brcm,bcm68575",
    "BCM6726": "brcm,bcm6726",
    "BCM67263": "brcm,bcm67263",
    # Add more as discovered
}


# ── Regex patterns for schematic text parsing ───────────────────────

# Reference designators: R123, C456, U7, L8, D9, J10, Q11, etc.
_RE_REFDES = re.compile(r"\b([RCULDQJT]\d{1,5}[A-Z]?)\b")

# Net names: alphanumeric with underscores, often with +/- voltage prefixes
_RE_NET = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

# Pin numbers: standalone digits or alphanumeric ball names like A12, B33
_RE_PIN = re.compile(r"\b([A-Z]?\d{1,3})\b")

# Off-page tag pattern: (page_number) signal_name  e.g. "(5) 1V8_EN"
_RE_OFFTAG = re.compile(r"\((\d{1,3})\)\s+([A-Z][A-Z0-9_]+)")

# Passive value: 0R, 2.2, 100K, 10uF, 1000pF, etc.
_RE_VALUE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*([RKMuUnNpPfF]?(?:[oO][hH][mM])?[FHR]?)\b"
)

_UNKNOWN_PARTS = {"UNKNOWN", "?", "N/A", "TBD"}
_TRACE_NOISE_TOKENS = frozenset({
    "FLASH",
    "SAMSUNG",
    "PACKAGE",
    "TEST",
    "JTAG",
    "CC0201",
    "CC0402",
    "RC0201",
    "RC0402",
    "X5R",
    "X7R",
    "SO16",
    "FBGA153",
})
_TRACE_ENDPOINT_TOKENS = frozenset({
    "ALE",
    "CLE",
    "CLK",
    "CMD",
    "RCK",
    "SCK",
    "SCLR",
})
_RE_BALL_COORD = re.compile(r"^[A-Z]{1,2}\d{1,2}$")
_RE_POWER_NET = re.compile(r"^(?:VCC|VDD|VSS|GND|AGND|DGND|PGND|RGND|TGND|AVDD|DVDD)")
_RE_TRACE_VOLTAGE_SUFFIX = re.compile(r"_(?:\d+P\d+|\d+V\d+)$")
_RE_LOOKUP_PART = re.compile(r"\b([A-Z0-9][A-Z0-9.+/-]{4,})\b")
_RE_PART_VALUE = re.compile(
    r"^[+-]?\d+(?:\.\d+)?(?:[A-Z%]+(?:/[A-Z0-9]+)*)?(?:@\d+(?:\.\d+)?[A-Z]+)?$",
    re.IGNORECASE,
)
_RE_I2C_ADDRESS = re.compile(r"\b0x[0-9A-F]{1,2}\b", re.IGNORECASE)
_LOOKUP_NOISE_TOKENS = frozenset({
    "ARCADYAN",
    "TECHNOLOGY",
    "CORPORATION",
    "CONFIDENTIAL",
    "INFORMATION",
    "DOCUMENT",
    "NUMBER",
    "TITLE",
    "MODEL",
    "SHEET",
    "DATE",
    "DRAWN",
    "REV",
})
_SEMICONDUCTOR_PART_PREFIXES = (
    "TCA",
    "PCA",
    "SN74",
    "74HC",
    "74LVC",
    "U74",
    "TPS",
    "FP",
    "BCM",
    "HN",
    "RTL",
    "MT",
    "LM",
    "TLV",
    "MAX",
    "ADM",
    "ISL",
    "NCP",
    "SY",
    "MP",
    "AP",
)


# ── Helpers ─────────────────────────────────────────────────────────

def _normalize_trace_token(token: str) -> str:
    """Normalize signal-like tokens so naming variants can be compared."""
    normalized = re.sub(r"[^A-Z0-9]+", "_", token.upper()).strip("_")
    normalized = _RE_TRACE_VOLTAGE_SUFFIX.sub("", normalized)

    parts: list[str] = []
    for part in normalized.split("_"):
        if not part:
            continue
        if part.isdigit():
            parts.append(str(int(part)))
            continue

        m = re.fullmatch(r"([A-Z]+)0+(\d+)", part)
        if m:
            parts.append(f"{m.group(1)}{int(m.group(2))}")
            continue

        parts.append(part)

    return "_".join(parts)


def _expand_trace_aliases(
    net_name: str,
    tag_index: dict,
    extra_aliases: list[str] | None = None,
) -> list[str]:
    """Return exact and normalized naming variants worth tracing."""
    aliases: list[str] = []

    for candidate in [net_name, *(extra_aliases or [])]:
        cleaned = candidate.strip()
        if not cleaned:
            continue
        aliases.append(cleaned)
        underscored = re.sub(r"\s+", "_", cleaned)
        if underscored != cleaned:
            aliases.append(underscored)

    normalized_targets = {
        _normalize_trace_token(alias) for alias in aliases if alias.strip()
    }

    for tag in tag_index:
        normalized_tag = _normalize_trace_token(tag)
        if not normalized_tag:
            continue
        if normalized_tag in normalized_targets or any(
            normalized_tag.startswith(f"{target}_")
            or target.startswith(f"{normalized_tag}_")
            for target in normalized_targets
        ):
            aliases.append(tag)

    return list(dict.fromkeys(alias for alias in aliases if alias))


def _extract_line_contexts(
    page_text: str,
    target: str,
    *,
    before: int = 1,
    after: int = 1,
) -> list[str]:
    """Return short line windows around every matching line for *target*."""
    target_lower = target.lower()
    lines = page_text.splitlines()
    contexts: list[str] = []

    for idx, line in enumerate(lines):
        if target_lower not in line.lower():
            continue
        lo = max(0, idx - before)
        hi = min(len(lines), idx + after + 1)
        window = "\n".join(lines[lo:hi]).strip()
        if window:
            contexts.append(window)

    return list(dict.fromkeys(contexts))


def _extract_refdes_line_contexts(
    page_text: str,
    refdes: str,
    *,
    before: int = 1,
    after: int = 1,
) -> list[str]:
    """Return short line windows around exact refdes-token matches."""
    pattern = re.compile(rf"\b{re.escape(refdes)}\b", re.IGNORECASE)
    lines = page_text.splitlines()
    contexts: list[str] = []

    for idx, line in enumerate(lines):
        if not pattern.search(line):
            continue
        lo = max(0, idx - before)
        hi = min(len(lines), idx + after + 1)
        window = "\n".join(lines[lo:hi]).strip()
        if window:
            contexts.append(window)

    return list(dict.fromkeys(contexts))


def _extract_refdes_value(context: str, refdes: str) -> str | None:
    """Best-effort extraction of a passive value near *refdes*."""
    after = re.search(
        rf"{re.escape(refdes)}\s+([0-9]+(?:\.\d+)?(?:[A-Za-z%]+)?)",
        context,
    )
    if after:
        return after.group(1).strip()

    return None


def _looks_like_meaningful_related_signal(
    token: str,
    primary_norms: set[str],
    tag_index: dict,
) -> bool:
    """Filter schematic OCR tokens down to endpoint-like signal names."""
    upper = token.upper().strip()
    if not upper:
        return False
    if _normalize_trace_token(upper) in primary_norms:
        return False
    if upper in _TRACE_NOISE_TOKENS:
        return False
    if upper.startswith(("GPIO_", "TP", "NC_", "RFU")):
        return False
    if _RE_REFDES.fullmatch(upper):
        return False
    if _RE_BALL_COORD.fullmatch(upper):
        return False
    if _RE_POWER_NET.match(upper):
        return False

    if re.fullmatch(r"DAT\d", upper):
        return True
    if upper in _TRACE_ENDPOINT_TOKENS:
        return True
    if upper in tag_index:
        return True
    if len(upper) < 4:
        return False

    return any(
        keyword in upper
        for keyword in (
            "RESET",
            "FAULT",
            "SFP",
            "WAN",
            "NAND",
            "BOOT",
            "STRAP",
            "LOS",
            "PRESENT",
            "TX",
            "RX",
            "CMD",
            "CLK",
            "ALE",
            "CLE",
            "SER",
        )
    )


def _related_signal_candidates(
    context: str,
    primary_norms: set[str],
    tag_index: dict,
) -> list[str]:
    """Return endpoint-like signals mentioned alongside the traced net."""
    ranked: list[tuple[int, str]] = []

    for token in _find_net_names(context):
        if not _looks_like_meaningful_related_signal(token, primary_norms, tag_index):
            continue

        score = 0
        upper = token.upper()
        if upper in tag_index:
            score += 5
        if re.fullmatch(r"DAT\d", upper):
            score += 4
        if upper in _TRACE_ENDPOINT_TOKENS:
            score += 4
        if "RESET" in upper or "FAULT" in upper:
            score += 3
        if "SFP" in upper or "WAN" in upper or "NAND" in upper:
            score += 2

        ranked.append((score, token))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    ordered = [token for _, token in ranked]
    return list(dict.fromkeys(ordered))


def _is_endpoint_pin_hint(token: str) -> bool:
    """Return True for short endpoint pin/function names worth surfacing."""
    upper = token.upper()
    return bool(re.fullmatch(r"DAT\d", upper)) or upper in _TRACE_ENDPOINT_TOKENS

def _extract_context(page_text: str, target: str, radius: int = 200) -> list[str]:
    """Extract context snippets around every occurrence of *target* in a page."""
    contexts = []
    start = 0
    lowered = page_text.lower()
    target_low = target.lower()
    while True:
        idx = lowered.find(target_low, start)
        if idx == -1:
            break
        lo = max(0, idx - radius)
        hi = min(len(page_text), idx + len(target) + radius)
        contexts.append(page_text[lo:hi].strip())
        start = idx + len(target)
    return contexts


def _find_refdes_near(context: str) -> list[str]:
    """Return unique reference designators found in *context*."""
    return list(dict.fromkeys(_RE_REFDES.findall(context)))


def _find_net_names(context: str) -> list[str]:
    """Return unique net names found in *context*."""
    return list(dict.fromkeys(_RE_NET.findall(context)))


def _page_text_for(page_num: int, pages: dict) -> str:
    """Get page text, tolerating both int and str keys."""
    return pages.get(page_num, pages.get(str(page_num), ""))


def _has_known_part_number(part_number: str | None) -> bool:
    """True when *part_number* is populated with a meaningful value."""
    if not part_number:
        return False
    return part_number.strip().upper() not in _UNKNOWN_PARTS


def _pick_best_refdes_entry(entry: dict | list[dict]) -> dict | None:
    """Choose the richest refdes entry, preferring one with a part number."""
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, list):
        candidates = [candidate for candidate in entry if isinstance(candidate, dict)]
        if not candidates:
            return None
        for candidate in candidates:
            if _has_known_part_number(candidate.get("part_number")):
                return candidate
        return candidates[0]
    return None


def _normalize_lookup_token(token: str) -> str:
    """Trim OCR punctuation from a lookup token."""
    return token.strip("(),;:[]{}<>\"'")


def _is_semiconductor_part(token: str | None) -> bool:
    """Return True when *token* resembles an IC family part number."""
    if not token:
        return False
    upper = _normalize_lookup_token(token).upper()
    if not upper:
        return False
    if upper.startswith(_SEMICONDUCTOR_PART_PREFIXES):
        return True
    return any(
        hint in upper
        for hint in ("LVC1G", "HC595", "PCA9555", "TCA9555", "TPS", "FP6359")
    )


def _normalize_part_number(part_number: str | None) -> str | None:
    """Collapse package/vendor suffixes to a stable series part number."""
    if not part_number:
        return None

    upper = _normalize_lookup_token(part_number).upper()
    if upper.startswith("TCA9555"):
        return "TCA9555"
    if upper.startswith("PCA9555"):
        return "PCA9555"
    if upper.startswith(("SN74HC595", "74HC595")):
        return "SN74HC595"
    if re.match(r"^(?:U|SN)?74LVC1G08", upper):
        return "SN74LVC1G08"
    if re.match(r"^(?:U|SN)?74LVC1G11", upper):
        return "SN74LVC1G11"
    if upper.startswith("TPS562203"):
        return "TPS562203"
    if upper.startswith("FP6359"):
        return "FP6359S6"
    if upper.startswith("BCM67263"):
        return "BCM67263"
    if upper.startswith("BCM6726"):
        return "BCM6726"
    return upper


def _part_lookup_candidates(part_number: str | None) -> list[str]:
    """Return raw + normalized lookup keys for compatible matching."""
    if not part_number:
        return []

    raw = _normalize_lookup_token(part_number).upper()
    candidates = [raw]

    normalized = _normalize_part_number(raw)
    if normalized and normalized not in candidates:
        candidates.append(normalized)

    if "-" in raw:
        base = raw.split("-", 1)[0]
        if base and base not in candidates:
            candidates.append(base)

    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _lookup_compatible_for_part(part_number: str | None) -> str | None:
    """Resolve Linux compatible string from raw/normalized part variants."""
    for candidate in _part_lookup_candidates(part_number):
        if candidate in KNOWN_COMPATIBLES:
            return KNOWN_COMPATIBLES[candidate]

    for candidate in _part_lookup_candidates(part_number):
        for known_pn, compat in KNOWN_COMPATIBLES.items():
            if candidate.startswith(known_pn) or known_pn.startswith(candidate):
                return compat

    return None


def _is_probable_lookup_part(token: str, refdes: str) -> bool:
    """Reject obvious OCR noise before considering *token* as a part number."""
    cleaned = _normalize_lookup_token(token)
    upper = cleaned.upper()

    if not cleaned or upper == refdes.upper():
        return False
    if len(cleaned) < 5:
        return False
    if _RE_REFDES.fullmatch(upper):
        return False
    if upper in _UNKNOWN_PARTS or upper in _LOOKUP_NOISE_TOKENS:
        return False
    if _RE_PART_VALUE.fullmatch(upper):
        return False
    if ":" in cleaned:
        return False
    if re.fullmatch(r"\d+/\d+[A-Z]+", upper):
        return False
    if re.fullmatch(r"\d{8,}[A-Z]?", upper):
        return False
    if re.fullmatch(r"SHU\d+[A-Z]?", upper):
        return False
    if "@" in cleaned or "=" in cleaned:
        return False
    if cleaned.startswith(("+", "-")):
        return False
    if "_" in cleaned:
        return False
    if any(c.islower() for c in cleaned):
        return False
    if re.match(r"^(CC|RC|BGA|QFN|QFP|SOP|SOT|SOIC|TSSOP|SSOP|MSOP|VSSOP)\d", upper):
        return False

    has_alpha = any(c.isalpha() for c in cleaned)
    has_digit = any(c.isdigit() for c in cleaned)
    return has_alpha and has_digit


def _collect_lookup_contexts(
    refdes: str,
    pages: dict[int, str],
    *,
    page_hint: int | None = None,
) -> list[str]:
    """Collect prioritized line-window contexts for refdes lookup."""
    ordered_pages: list[int] = []
    if page_hint is not None:
        ordered_pages.append(int(page_hint))
    ordered_pages.extend(int(page) for page in pages)
    ordered_pages = list(dict.fromkeys(ordered_pages))

    contexts: list[str] = []
    for pg_num in ordered_pages:
        pg_text = _page_text_for(pg_num, pages)
        if not pg_text or not re.search(rf"\b{re.escape(refdes)}\b", pg_text, re.IGNORECASE):
            continue
        page_contexts = _extract_refdes_line_contexts(pg_text, refdes, before=2, after=50)
        if not page_contexts:
            page_contexts = _extract_context(pg_text, refdes, radius=500)
        contexts.extend(page_contexts)

    return list(dict.fromkeys(ctx for ctx in contexts if ctx))


def _extract_part_from_contexts(refdes: str, contexts: list[str]) -> str | None:
    """Find the strongest-looking part number candidate inside lookup contexts."""
    best_token: str | None = None
    best_score = float("-inf")

    for context_idx, context in enumerate(contexts):
        for raw_token in _RE_LOOKUP_PART.findall(context.upper()):
            token = _normalize_lookup_token(raw_token)
            if not _is_probable_lookup_part(token, refdes):
                continue

            score = 0.0
            if _lookup_compatible_for_part(token) is not None:
                score += 140.0
            elif _is_semiconductor_part(token):
                score += 100.0
            score -= context_idx
            score += min(len(token), 24) / 8.0

            if score > best_score:
                best_score = score
                best_token = token

    return best_token


def _extract_address_from_contexts(contexts: list[str]) -> str | None:
    """Return the first I2C-style address found in the lookup contexts."""
    for context in contexts:
        match = _RE_I2C_ADDRESS.search(context)
        if match:
            return match.group(0).lower()
    return None


def _extract_bus_from_contexts(contexts: list[str]) -> str | None:
    """Infer an I2C bus name from bus-labelled net aliases in lookup contexts."""
    pattern_groups = (
        (
            re.compile(r"\b(?:SDA|SCL)_M(\d+)\b", re.IGNORECASE),
            re.compile(r"\bBSC_M(\d+)\b", re.IGNORECASE),
        ),
        (
            re.compile(r"\b(?:SDA|SCL|BSC_SDA|BSC_SCL)_(\d+)\b", re.IGNORECASE),
            re.compile(r"\bI2C[_\s-]?(\d+)\b", re.IGNORECASE),
        ),
    )

    for patterns in pattern_groups:
        candidates: list[str] = []
        for context in contexts:
            for pattern in patterns:
                for match in pattern.finditer(context):
                    bus = f"i2c{int(match.group(1))}"
                    if bus not in candidates:
                        candidates.append(bus)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return None

    return None


def _primary_lookup_context(contexts: list[str]) -> str:
    """Return the richest lookup context instead of a tiny single-line snippet."""
    for context in contexts:
        if "\n" in context or len(context.split()) >= 8:
            return context
    return contexts[0] if contexts else ""


# ── Tool 1: trace_net ──────────────────────────────────────────────

@define_tool(
    description=(
        "Trace a net name within a single PDF. Finds all pages where the "
        "net appears and identifies connected components and path segments."
    )
)
def trace_net(
    net_name: str,
    pdf_id: str,
    tag_index: dict,
    pages: dict[int, str],
    extra_aliases: list[str] | None = None,
) -> dict:
    """Trace *net_name* through schematic pages of one PDF.

    Parameters
    ----------
    net_name : str
        Signal / net name to trace (e.g. ``"GPHY1_DP0"``).
    pdf_id : str
        Identifier of the PDF being searched (e.g. ``"mainboard"``).
    tag_index : dict
        ``{tag_name: [{page, location, ...}]}`` built by Agent A.
    pages : dict[int, str]
        ``{page_number: page_text}`` raw text for each schematic page.

    Returns
    -------
    dict with ``net_name``, ``pdf_id``, ``pages_found``, ``connected_components``,
    ``path_segments``.
    """
    all_components: list[str] = []
    path_segments: list[dict] = []
    related_signals: list[str] = []
    page_hops: list[dict[str, int | str]] = []
    aliases = _expand_trace_aliases(net_name, tag_index, extra_aliases=extra_aliases)
    primary_norms = {_normalize_trace_token(alias) for alias in aliases}

    # 1. Check tag_index for pages where this net is known
    indexed_pages: set[int] = set()
    for alias in aliases:
        tag_entries = tag_index.get(alias, [])
        indexed_pages.update(
            int(e["page"]) for e in tag_entries if isinstance(e, dict) and "page" in e
        )

    # 2. Also scan all pages for the net name in raw text
    for pg_num, pg_text in pages.items():
        pg_num_int = int(pg_num)
        if any(alias.lower() in pg_text.lower() for alias in aliases):
            indexed_pages.add(pg_num_int)

    seen_cross_links: set[tuple[str, int, int]] = set()
    pages_queue = sorted(indexed_pages)
    pages_found: list[int] = []

    # 3. For each page, extract context and identify connected components
    while pages_queue:
        pg = pages_queue.pop(0)
        if pg in pages_found:
            continue
        pages_found.append(pg)
        pg_text = _page_text_for(pg, pages)
        contexts: list[str] = []
        for alias in aliases:
            contexts.extend(_extract_line_contexts(pg_text, alias))
        if not contexts:
            for alias in aliases:
                contexts.extend(_extract_context(pg_text, alias))
        contexts = list(dict.fromkeys(contexts))

        for ctx in contexts:
            refdes_list = _find_refdes_near(ctx)
            all_components.extend(refdes_list)
            related_signals.extend(_related_signal_candidates(ctx, primary_norms, tag_index))

            # Try to extract pin associations: refdes followed by pin number
            for ref in refdes_list:
                value = _extract_refdes_value(ctx, ref) if ref.startswith(("R", "C", "L")) else None
                passive = None
                if value and ref.startswith(("R", "C", "L")):
                    passive = infer_passive_role(ref, value, ctx)
                pin_match = None if value else re.search(
                    rf"{re.escape(ref)}\s+(\d{{1,3}})", ctx
                )
                pin = pin_match.group(1) if pin_match else None
                path_segments.append({
                    "page": pg,
                    "component": ref,
                    "pin": pin,
                    "net": net_name,
                    "value": value,
                    "passive_role": passive["role"] if passive else None,
                    "penetrable": passive["penetrable"] if passive else None,
                })

        for alias in aliases:
            cross = trace_tag_cross_page(
                alias,
                pg,
                pdf_id,
                tag_index,
                pages,
                extra_aliases=aliases,
            )
            related_signals.extend(cross.get("related_signals", []))
            for dest in cross["destination_pages"]:
                hop_key = (alias, min(pg, dest), max(pg, dest))
                if hop_key not in seen_cross_links:
                    seen_cross_links.add(hop_key)
                    page_hops.append({"signal": alias, "from": pg, "to": dest})
                if dest not in pages_found and dest not in pages_queue:
                    pages_queue.append(dest)
                    pages_queue.sort()

    connected = list(dict.fromkeys(all_components))

    return {
        "net_name": net_name,
        "pdf_id": pdf_id,
        "pages_found": sorted(set(pages_found)),
        "connected_components": connected,
        "path_segments": path_segments,
        "related_signals": list(dict.fromkeys(related_signals)),
        "page_hops": page_hops,
        "aliases": aliases,
    }


# ── Tool 2: trace_tag_cross_page ──────────────────────────────────

@define_tool(
    description=(
        "Follow an off-page connector tag to other pages within the same "
        "PDF. Returns destination pages and continuation context."
    )
)
def trace_tag_cross_page(
    tag: str,
    source_page: int,
    pdf_id: str,
    tag_index: dict,
    pages: dict[int, str],
    extra_aliases: list[str] | None = None,
) -> dict:
    """Follow off-page tag *tag* from *source_page* to destinations.

    Arcadyan schematics use the pattern ``(page_number) SIGNAL_NAME`` for
    off-page connectors (e.g. ``(5) 1V8_EN`` means signal continues on
    page 5).

    Parameters
    ----------
    tag : str
        Off-page tag / signal name (e.g. ``"1V8_EN"``).
    source_page : int
        Page number where the tag originates.
    pdf_id : str
        PDF identifier.
    tag_index : dict
        Tag index built by Agent A.
    pages : dict[int, str]
        Raw page texts.

    Returns
    -------
    dict with ``tag``, ``source_page``, ``destination_pages``, ``continuations``.
    """
    destination_pages: list[int] = []
    continuations: list[dict] = []
    related_signals: list[str] = []
    aliases = _expand_trace_aliases(tag, tag_index, extra_aliases=extra_aliases)
    primary_norms = {_normalize_trace_token(alias) for alias in aliases}

    # Check tag_index first
    for alias in aliases:
        tag_entries = tag_index.get(alias, [])
        for entry in tag_entries:
            if isinstance(entry, dict) and "page" in entry:
                pg = int(entry["page"])
                if pg != source_page:
                    destination_pages.append(pg)

    # Also scan all pages for the off-page pattern "(N) TAG"
    for pg_num, pg_text in pages.items():
        pg_int = int(pg_num)
        if pg_int == source_page:
            continue
        # Look for "(source_page) tag" on other pages (meaning they ref us)
        # or just the tag name appearing on other pages
        if any(alias.lower() in pg_text.lower() for alias in aliases):
            if pg_int not in destination_pages:
                destination_pages.append(pg_int)

    destination_pages = sorted(set(destination_pages))

    # Extract continuation context from each destination page
    for pg in destination_pages:
        pg_text = _page_text_for(pg, pages)
        contexts: list[str] = []
        for alias in aliases:
            contexts.extend(_extract_line_contexts(pg_text, alias, before=1, after=1))
        if not contexts:
            for alias in aliases:
                contexts.extend(_extract_context(pg_text, alias, radius=300))
        contexts = list(dict.fromkeys(contexts))
        refdes_found = []
        for ctx in contexts:
            refdes_found.extend(_find_refdes_near(ctx))
            related_signals.extend(_related_signal_candidates(ctx, primary_norms, tag_index))

        # Check for chained off-page refs on destination page
        chained_tags: list[dict] = []
        for m in _RE_OFFTAG.finditer(pg_text):
            if _normalize_trace_token(m.group(2)) in primary_norms:
                chained_tags.append({
                    "target_page": int(m.group(1)),
                    "signal": m.group(2),
                })

        continuations.append({
            "page": pg,
            "context_snippets": contexts[:3],  # limit to avoid huge output
            "components": list(dict.fromkeys(refdes_found)),
            "related_signals": _related_signal_candidates(
                "\n".join(contexts[:3]),
                primary_norms,
                tag_index,
            ),
            "chained_tags": chained_tags,
        })

    return {
        "tag": tag,
        "source_page": source_page,
        "destination_pages": destination_pages,
        "continuations": continuations,
        "related_signals": list(dict.fromkeys(related_signals)),
    }


# ── Tool 3: trace_cross_pdf ───────────────────────────────────────


def _connector_pdf_entries(connector_info: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Normalize connector metadata into ``pdf_id -> {signal: pin}`` entries."""
    entries: dict[str, dict[str, str]] = {}
    if not isinstance(connector_info, dict):
        return entries

    pdf_entries = connector_info.get("pdfs")
    if isinstance(pdf_entries, dict):
        for pdf_id, info in pdf_entries.items():
            if not isinstance(info, dict):
                continue
            pins = info.get("pins", {})
            if isinstance(pins, dict):
                entries[str(pdf_id)] = {
                    str(signal_name): str(pin_number)
                    for signal_name, pin_number in pins.items()
                }
        if entries:
            return entries

    if "pins" in connector_info and connector_info.get("pdf_id"):
        pins = connector_info.get("pins", {})
        if isinstance(pins, dict):
            entries[str(connector_info["pdf_id"])] = {
                str(signal_name): str(pin_number)
                for signal_name, pin_number in pins.items()
            }
        return entries

    for pdf_id, info in connector_info.items():
        if not isinstance(info, dict):
            continue
        pins = info.get("pins", {})
        if isinstance(pins, dict):
            entries[str(pdf_id)] = {
                str(signal_name): str(pin_number)
                for signal_name, pin_number in pins.items()
            }
    return entries


def _matching_connector_pins(net_name: str, pins: dict[str, str]) -> list[tuple[str, str]]:
    """Return connector signals whose name best matches *net_name*."""
    target_norm = _normalize_trace_token(net_name)
    matches: list[tuple[int, str, str]] = []

    for signal_name, pin_number in pins.items():
        signal_norm = _normalize_trace_token(signal_name)
        if not signal_norm:
            continue

        score = None
        if signal_norm == target_norm:
            score = 0
        elif signal_norm.startswith(f"{target_norm}_") or target_norm.startswith(f"{signal_norm}_"):
            score = 1
        elif target_norm in signal_norm or signal_norm in target_norm:
            score = 2

        if score is None:
            continue
        matches.append((score, str(signal_name), str(pin_number)))

    matches.sort(key=lambda item: (item[0], item[1]))
    ordered = [(signal_name, pin_number) for _, signal_name, pin_number in matches]
    return list(dict.fromkeys(ordered))

@define_tool(
    description=(
        "Follow a signal across PDF boundaries via a board-to-board "
        "connector. Looks up connector pin mapping to find the "
        "destination PDF and continued net name."
    )
)
def trace_cross_pdf(
    net_name: str,
    source_pdf: str,
    connector_refdes: str,
    connector_index: dict,
    all_tag_indices: dict,
) -> dict:
    """Trace *net_name* through a board-to-board connector to another PDF.

    Parameters
    ----------
    net_name : str
        Signal name in the source PDF.
    source_pdf : str
        PDF id of the source schematic (e.g. ``"mainboard"``).
    connector_refdes : str
        Reference designator of the B2B connector (e.g. ``"J1"``).
    connector_index : dict
        ``{connector_refdes: {pdf_id: str, pins: {pin_name: pin_number}}}``
        built by Agent A. Connectors may also carry a ``pdfs`` side-map
        keyed by PDF. When board-to-board connectors use different refdes on
        each schematic, the tracer falls back to same-pin continuation across
        other connector entries.
    all_tag_indices : dict
        ``{pdf_id: tag_index}`` for all PDFs.

    Returns
    -------
    dict with ``net_name``, ``source_pdf``, ``connector``,
    ``destination_pdf``, ``destination_connector``, ``continued_as``.
    """
    destination_pdf: str | None = None
    destination_connector: str | None = None
    continued_as: str | None = None
    pin_number: str | None = None

    connector_info = connector_index.get(connector_refdes, {})
    source_entries = _connector_pdf_entries(connector_info)
    source_pins = source_entries.get(source_pdf, {})
    source_matches = _matching_connector_pins(net_name, source_pins)
    if source_matches:
        pin_number = source_matches[0][1]

    candidate_matches: list[tuple[int, str, str, str]] = []
    if source_matches:
        for conn_name, info in connector_index.items():
            for candidate_pdf, candidate_pins in _connector_pdf_entries(info).items():
                if candidate_pdf == source_pdf:
                    continue
                for source_signal, source_pin in source_matches:
                    for candidate_signal, candidate_pin in candidate_pins.items():
                        if str(candidate_pin) != source_pin:
                            continue

                        score = 0
                        if conn_name == connector_refdes:
                            score -= 100
                        if (
                            _normalize_trace_token(candidate_signal)
                            == _normalize_trace_token(source_signal)
                        ):
                            score -= 20
                        if candidate_signal in all_tag_indices.get(candidate_pdf, {}):
                            score -= 5

                        candidate_matches.append(
                            (score, conn_name, candidate_pdf, candidate_signal)
                        )

        if candidate_matches:
            candidate_matches.sort(key=lambda item: item)
            _, destination_connector, destination_pdf, continued_as = candidate_matches[0]

    # Fallback: search all tag indices for the net_name in other PDFs
    if continued_as is None:
        for pid, tidx in all_tag_indices.items():
            if pid == source_pdf:
                continue
            if net_name in tidx:
                destination_pdf = pid
                continued_as = net_name  # same net name on other side
                break

    return {
        "net_name": net_name,
        "source_pdf": source_pdf,
        "connector": connector_refdes,
        "pin_number": pin_number,
        "destination_pdf": destination_pdf,
        "destination_connector": destination_connector,
        "continued_as": continued_as,
    }


# ── Tool 4: detect_lane_swap ──────────────────────────────────────

@define_tool(
    description=(
        "Detect differential pair lane swap from SoC to endpoint. "
        "Traces GPHY/XPHY differential pairs through magnetics to RJ45 "
        "and checks pin ordering against 10/100/1000BASE-T standard."
    )
)
def detect_lane_swap(
    soc_prefix: str,
    target_type: str,
    tag_index: dict,
    pages: dict[int, str],
    refdes_index: dict,
) -> dict:
    """Detect MDI lane swap for differential pairs with *soc_prefix*.

    The Arcadyan schematics show SoC GPHY pins (e.g. ``GPHY1_DP0_P/N``)
    connected through series resistors, AC coupling caps, and Ethernet
    magnetics (e.g. HN2436G) to RJ45 jack pins (``P0_1`` … ``P0_8``).
    Standard 1000BASE-T pin mapping expects pair 0 on pins 1-2,
    pair 1 on pins 3-6, pair 2 on pins 4-5, pair 3 on pins 7-8.

    Parameters
    ----------
    soc_prefix : str
        Prefix for the SoC PHY (e.g. ``"GPHY0"``, ``"GPHY1"``).
    target_type : str
        Endpoint type (e.g. ``"RJ45"``).
    tag_index : dict
        Tag index for the PDF.
    pages : dict[int, str]
        Page texts.
    refdes_index : dict
        ``{refdes: {part_number, page, ...}}`` from Agent A.

    Returns
    -------
    dict with ``soc_prefix``, ``pairs_traced``, ``swap_detected``,
    ``swap_detail``, ``trace_paths``.
    """
    # 1000BASE-T standard pair → RJ45 pin mapping (T568B)
    STANDARD_PAIR_PINS = {
        0: ("1", "2"),     # Pair A: pins 1, 2
        1: ("3", "6"),     # Pair B: pins 3, 6
        2: ("4", "5"),     # Pair C: pins 4, 5
        3: ("7", "8"),     # Pair D: pins 7, 8
    }

    pairs_traced: list[dict] = []
    trace_paths: list[dict] = []
    swap_detected = False
    swap_details: list[str] = []

    # Find all differential pairs matching the prefix
    pair_pattern = re.compile(
        rf"\b{re.escape(soc_prefix)}_D([PN])(\d)\b"
    )

    # Collect pair indices found in the schematic
    pair_indices: set[int] = set()
    for pg_text in pages.values():
        for m in pair_pattern.finditer(pg_text):
            pair_indices.add(int(m.group(2)))

    for pair_idx in sorted(pair_indices):
        dp_name = f"{soc_prefix}_DP{pair_idx}"
        dn_name = f"{soc_prefix}_DN{pair_idx}"
        rj45_pins: list[str] = []
        path_components: list[str] = []

        # Trace each signal through pages
        for sig_name in (dp_name, dn_name):
            for pg_num, pg_text in pages.items():
                if sig_name not in pg_text:
                    continue
                contexts = _extract_context(pg_text, sig_name, radius=400)
                for ctx in contexts:
                    path_components.extend(_find_refdes_near(ctx))

                    # Look for RJ45/connector pin patterns: P0_1, P1_2, etc.
                    pin_matches = re.findall(
                        r"\bP(\d+)_(\d+)\b", ctx
                    )
                    for port, pin in pin_matches:
                        rj45_pins.append(f"P{port}_{pin}")

        path_components = list(dict.fromkeys(path_components))

        # Determine actual RJ45 pin positions for this pair
        actual_pins = sorted(set(rj45_pins))

        trace_entry = {
            "pair": pair_idx,
            "dp": dp_name,
            "dn": dn_name,
            "components": path_components,
            "rj45_pins": actual_pins,
        }
        trace_paths.append(trace_entry)

        # Check against standard
        if actual_pins and pair_idx in STANDARD_PAIR_PINS:
            expected = set(STANDARD_PAIR_PINS[pair_idx])
            # Extract just the pin numbers from P*_N patterns
            actual_pin_nums = set()
            for p in actual_pins:
                m = re.match(r"P\d+_(\d+)", p)
                if m:
                    actual_pin_nums.add(m.group(1))

            if actual_pin_nums and actual_pin_nums != expected:
                swap_detected = True
                swap_details.append(
                    f"Pair {pair_idx} ({dp_name}/{dn_name}): "
                    f"expected pins {sorted(expected)}, "
                    f"got {sorted(actual_pin_nums)}"
                )

        pairs_traced.append({
            "pair": pair_idx,
            "rj45_pins": actual_pins,
        })

    return {
        "soc_prefix": soc_prefix,
        "target_type": target_type,
        "pairs_traced": pairs_traced,
        "swap_detected": swap_detected,
        "swap_detail": "; ".join(swap_details) if swap_details else "no swap",
        "trace_paths": trace_paths,
    }


# ── Tool 5: lookup_refdes ─────────────────────────────────────────

@define_tool(
    description=(
        "Look up a reference designator to find its part number and "
        "Linux device-tree compatible string."
    )
)
def lookup_refdes(
    refdes: str,
    refdes_index: dict,
    pages: dict[int, str],
) -> dict:
    """Look up *refdes* to find part number and DT compatible.

    Parameters
    ----------
    refdes : str
        Reference designator (e.g. ``"U7"``, ``"J4"``).
    refdes_index : dict
        ``{refdes: {part_number, page, ...}}`` from Agent A.
    pages : dict[int, str]
        Page texts for fallback search.

    Returns
    -------
    dict with ``refdes``, ``part_number``, ``compatible``, ``description``.
    """
    part_number: str | None = None
    normalized_part_number: str | None = None
    compatible: str | None = None
    address: str | None = None
    bus: str | None = None
    description = ""

    entry = _pick_best_refdes_entry(refdes_index.get(refdes, {}))
    if entry:
        part_number = entry.get("part_number")
        page = entry.get("page")
    else:
        page = None

    contexts: list[str] = []
    if entry and entry.get("context"):
        contexts.append(entry["context"])
    contexts.extend(_collect_lookup_contexts(refdes, pages, page_hint=page))
    contexts = list(dict.fromkeys(ctx for ctx in contexts if ctx))

    # Fallback: search page text for part number near refdes
    if part_number is None or (refdes.upper().startswith("U") and not _is_semiconductor_part(part_number)):
        context_candidate = _extract_part_from_contexts(refdes, contexts)
        if context_candidate and (
            part_number is None or _is_semiconductor_part(context_candidate)
        ):
            part_number = context_candidate

    normalized_part_number = _normalize_part_number(part_number)
    address = _extract_address_from_contexts(contexts)
    bus = _extract_bus_from_contexts(contexts)

    # Map part number to DT compatible string
    if part_number:
        compatible = _lookup_compatible_for_part(part_number)

        # Generate description from prefix
        prefix = refdes[0]
        desc_map = {
            "U": "IC",
            "R": "Resistor",
            "C": "Capacitor",
            "L": "Inductor",
            "D": "Diode/LED",
            "Q": "Transistor",
            "J": "Connector",
            "T": "Transformer/Magnetics",
        }
        description = desc_map.get(prefix, "Component")
        if part_number:
            description = f"{description}: {part_number}"

    return {
        "refdes": refdes,
        "part_number": part_number,
        "normalized_part_number": normalized_part_number,
        "compatible": compatible,
        "address": address,
        "bus": bus,
        "lookup_context": _primary_lookup_context(contexts),
        "description": description,
    }


# ── Tool 6: infer_passive_role ─────────────────────────────────────

@define_tool(
    description=(
        "Determine the role of a passive component (resistor/capacitor) "
        "in a circuit based on its value and surrounding context."
    )
)
def infer_passive_role(
    refdes: str,
    value: str,
    context: str,
) -> dict:
    """Infer the circuit role of passive component *refdes*.

    Parameters
    ----------
    refdes : str
        Reference designator (e.g. ``"R23"``).
    value : str
        Component value string (e.g. ``"0R"``, ``"10K"``, ``"100pF"``).
    context : str
        Surrounding schematic text for placement analysis.

    Returns
    -------
    dict with ``refdes``, ``value``, ``role``, ``penetrable`` (can trace through).
    """
    val_lower = value.lower().strip()
    ctx_lower = context.lower()

    # Parse numeric value and unit
    numeric_val: float | None = None
    unit = ""
    m = re.match(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]*)", val_lower)
    if m:
        numeric_val = float(m.group(1))
        unit = m.group(2)

    role = "unknown"
    penetrable = False

    # 0R / 0 ohm → direct connect (wire jumper)
    if val_lower in ("0r", "0", "0ohm", "0 ohm", "0.0r"):
        role = "direct_connect"
        penetrable = True

    # Resistors
    elif refdes.startswith("R"):
        if numeric_val is not None:
            # Parse resistance to ohms
            ohms = numeric_val
            if "k" in unit:
                ohms = numeric_val * 1_000
            elif "m" in unit and "ohm" not in unit:
                ohms = numeric_val * 1_000_000

            # Pull-up: large R connected to VCC/+voltage
            if ohms >= 1_000 and any(
                kw in ctx_lower
                for kw in ("vcc", "+3.3v", "+1.8v", "pull", "v_dd", "vdd", "+5v")
            ):
                role = "pull_up"
                penetrable = False

            # Pull-down: large R connected to GND/ground
            elif ohms >= 1_000 and any(
                kw in ctx_lower for kw in ("gnd", "ground", "vss", "pull_down")
            ):
                role = "pull_down"
                penetrable = False

            # Series termination: small R in signal path
            elif ohms < 100 and any(
                kw in ctx_lower
                for kw in ("series", "term", "dp", "dn", "trd", "clk", "data")
            ):
                role = "series_termination"
                penetrable = True

            # Small series R (< 100 Ω) — default to series, traceable
            elif ohms < 100:
                role = "series_resistor"
                penetrable = True

            else:
                role = "resistor"
                penetrable = False

    # Capacitors
    elif refdes.startswith("C"):
        if any(kw in ctx_lower for kw in ("gnd", "ground", "vss", "bypass", "decoupl")):
            role = "bypass"
            penetrable = False
        elif any(kw in ctx_lower for kw in ("ac_coupl", "ac coupl", "series", "dc block")):
            role = "ac_coupling"
            penetrable = True
        elif "cc0201" in ctx_lower or "cc0402" in ctx_lower:
            # Small package AC coupling caps are common on diff pairs
            if any(kw in ctx_lower for kw in ("dp", "dn", "trd", "gphy", "pcie")):
                role = "ac_coupling"
                penetrable = True
            else:
                role = "bypass"
                penetrable = False
        else:
            role = "capacitor"
            penetrable = False

    # Inductors / ferrite beads
    elif refdes.startswith("L"):
        role = "inductor"
        penetrable = True  # signal passes through

    return {
        "refdes": refdes,
        "value": value,
        "role": role,
        "penetrable": penetrable,
    }


# ── Tool 7: check_bom_population ──────────────────────────────────

@define_tool(
    description=(
        "Check if a component is populated or DNP (Do Not Populate) "
        "by consulting the BOM or schematic annotations."
    )
)
def check_bom_population(
    refdes: str,
    bom_path: str | None = None,
) -> dict:
    """Check whether *refdes* is populated on the board.

    Parameters
    ----------
    refdes : str
        Reference designator to check.
    bom_path : str | None
        Path to BOM file (CSV/Excel). If ``None``, assume populated.

    Returns
    -------
    dict with ``refdes``, ``populated``, ``dnp_reason``.
    """
    populated = True
    dnp_reason: str | None = None

    if bom_path is None:
        return {
            "refdes": refdes,
            "populated": True,
            "dnp_reason": None,
        }

    try:
        from pathlib import Path
        bom_file = Path(bom_path)
        if not bom_file.exists():
            return {
                "refdes": refdes,
                "populated": True,
                "dnp_reason": "BOM file not found, assuming populated",
            }

        content = bom_file.read_text(errors="replace")

        # Search for refdes in BOM lines
        for line in content.splitlines():
            if refdes not in line:
                continue

            line_lower = line.lower()
            # Check for DNP / NL (Not Loaded) / NP (Not Populated) markers
            if any(
                marker in line_lower
                for marker in ("dnp", "do not populate", "not loaded", "/nl", " nl ", "n/l")
            ):
                populated = False
                dnp_reason = f"Marked as DNP in BOM: {line.strip()[:120]}"
                break

            # Arcadyan schematics use "/NL" suffix on values (e.g. "10K /NL")
            if re.search(r"/\s*NL\b", line, re.IGNORECASE):
                populated = False
                dnp_reason = f"Value marked /NL: {line.strip()[:120]}"
                break

    except Exception as exc:
        dnp_reason = f"Error reading BOM: {exc}"

    return {
        "refdes": refdes,
        "populated": populated,
        "dnp_reason": dnp_reason,
    }
