"""
Indexing tools for Agent A: Vision Indexer.

These tools parse extracted schematic text and build structured indices
for use by the Connectivity Auditor.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Import if copilot SDK available, otherwise provide standalone functions
try:
    from copilot.tools import define_tool as _sdk_define_tool

    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    _sdk_define_tool = None


def define_tool(description: str = ""):
    """
    Decorator that records tool metadata but keeps the function callable.

    When the Copilot SDK is available the decorated function is *also*
    registered as a ``copilot.types.Tool`` accessible via the
    ``_tool`` attribute.  The function itself remains directly callable
    so that ``index_all_pdfs`` and tests can invoke it without await.
    """

    def decorator(func):
        func._tool_description = description
        if HAS_SDK and _sdk_define_tool is not None:
            func._tool = _sdk_define_tool(description=description)(func)
        return func

    return decorator


# ---------------------------------------------------------------------------
# Pydantic models for structured I/O
# ---------------------------------------------------------------------------

class PageIndex(BaseModel):
    """Result from index_pdf_pages."""
    pdf_id: str = Field(description="Identifier derived from filename (e.g. 'mainboard')")
    pages: dict[int, str] = Field(description="page_number → page content text")


class TagEntry(BaseModel):
    pdf_id: str
    page: int
    context: str = Field(description="Short snippet surrounding the tag occurrence")


class RefDesEntry(BaseModel):
    pdf_id: str
    page: int
    part_number: str | None = Field(default=None, description="Part number if found nearby")
    context: str


class ConnectorInfo(BaseModel):
    pdf_id: str
    pins: dict[str, str] = Field(
        description="Mapping of pin_name → pin_number (both stored as strings)"
    )


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

# Page boundary: lines containing "Sheet  <N>  of  <M>"
_RE_SHEET = re.compile(r"Sheet\s+(\d+)\s+of\s+(\d+)")

# Tag / net-label patterns
# Matches signal names that are ≥ 3 chars, start with a letter, contain at
# least one underscore or digit, and are composed of upper-case letters,
# digits, and underscores.  Excludes pure grid-coords like "AA12" and common
# schematic frame labels.
_RE_TAG = re.compile(
    r"""
    \b
    (?!(?:CC|RC|NP|FB|SH|NL)\d)     # skip package/footprint codes
    ([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+) # require at least one underscore
    \b
    """,
    re.VERBOSE,
)

# Off-page cross-reference like "(10)" or "(7,14)"
_RE_PAGE_REF = re.compile(r"\((\d+(?:,\d+)*)\)")

# Reference designator: letter prefix + digits, optional trailing letter
_RE_REFDES = re.compile(
    r"\b((?:U|R|C|L|J|Q|D|FB|F|T|SW|TP|S)\d{1,5}[A-Z]?)\b"
)

# Test-point refdes should not be promoted into DTS device candidates
_RE_TESTPOINT = re.compile(r"^TP\d{1,5}[A-Z]?$")

# Connector refdes specifically
_RE_CONNECTOR = re.compile(r"\b(J\d{1,4}[A-Z]?)\b")

# Part-number heuristic: alphanumeric token ≥ 5 chars that isn't a pure
# number, signal name, or grid coordinate.  Must contain both letters
# and digits.
_RE_PART_NUMBER = re.compile(
    r"\b([A-Z][A-Z0-9]{2,}[-/]?[A-Z0-9]{2,}(?:[-/][A-Z0-9]+)*)\b"
)

# Pin-number pattern in connector context: bare integers 1–999
_RE_PIN_NUM = re.compile(r"\b(\d{1,3})\b")
_RE_CONNECTOR_SIGNAL_WITH_REF = re.compile(
    r"(?:"
    r"\(\d+(?:,\d+)*\)\s+([A-Z][A-Z0-9_]{2,})"
    r"|"
    r"([A-Z][A-Z0-9_]{2,})\s+\(\d+(?:,\d+)*\)"
    r")"
)

# Likely BGA/ball-map coordinates (but not refdes)
_RE_BALL_COORD = re.compile(r"^[A-Z]{1,2}\d{1,2}$")

# Package / footprint descriptors that should not be treated as part numbers
_RE_FOOTPRINT = re.compile(
    r"^(CC|RC|BGA|QFN|QFP|SOP|SOT|SOIC|TSSOP|SSOP|MSOP|LQFP|UFDFPN|DFN|LGA|WLCSP)\d",
    re.IGNORECASE,
)
_RE_VALUE_LIKE = re.compile(
    r"^[+-]?\d+(?:\.\d+)?(?:[A-Z%]+(?:/[A-Z0-9]+)*)?(?:@\d+(?:\.\d+)?[A-Z]+)?$",
    re.IGNORECASE,
)

# Noise words to skip when looking for part numbers
_NOISE_WORDS = frozenset({
    "PACKAGE", "COVER", "FRAME", "GASKET", "SCREW", "DATE", "SIZE",
    "TITLE", "MODEL", "SHEET", "DRAWN", "ARCADYAN", "TECHNOLOGY",
    "CORPORATION", "CONFIDENTIAL", "INFORMATION", "RESERVED", "BLOCK",
    "DIAGRAM", "REVISION", "HISTORY", "NOTES", "DESIGN", "TABLE",
    "GND", "VCC", "VDD", "VSS", "NL",
})

# Exclude tags that are just schematic frame/boilerplate
_BOILERPLATE_TAGS = frozenset({
    "TECHNOLOGY_CORPORATION", "ALL_RIGHTS", "PRIOR_WRITTEN",
    "INTELLECTUAL_PROPERTY", "ARCADYAN_CONFIDENTIAL",
    "DOCUMENT_NUMBER", "DESIGN_NOTES", "REVISION_HISTORY",
    "POWER_CONSUMPTION_TABLE", "COVER_SHEET", "BLOCK_DIAGRAM",
    "SYSTEM_BLOCK_DIAGRAM", "CHANGE_HISTORY",
})

_PINMAP_MARKERS = (
    "BGA",
    "BALL",
    "PIN MAP",
    "PINOUT",
    "<PACKAGE>",
    "LAYOUT NOTES",
    "10DEGREE",
    " MIL",
    "PRELIM",
)

_POWER_CONTEXT_MARKERS = (
    "VDD",
    "VSS",
    "AVDD",
    "DVDD",
    "PVDD",
    "GND",
    "RFU",
    "NC_",
)

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


def _is_page_reference_number(line: str, match: re.Match[str]) -> bool:
    """Return True when a numeric token is part of an off-page reference like ``(7,14)``."""
    before = line[match.start() - 1] if match.start() > 0 else ""
    after = line[match.end()] if match.end() < len(line) else ""
    return before in {"(", ","} and after in {",", ")"}


def _extract_connector_pin_candidates(line: str) -> list[tuple[str, int]]:
    """Return connector pin-number candidates while skipping page-reference numbers."""
    candidates: list[tuple[str, int]] = []
    for pin_match in _RE_PIN_NUM.finditer(line):
        pin_value = int(pin_match.group(1))
        if pin_value < 1 or pin_value > 200:
            continue
        if _is_page_reference_number(line, pin_match):
            continue
        candidates.append((pin_match.group(1), pin_match.start()))
    return candidates


def _extract_connector_signal_candidates(line: str) -> list[tuple[str, int]]:
    """Return signal names with approximate x-position for connector pin matching."""
    signals: list[tuple[str, int]] = []
    for signal_match in _RE_CONNECTOR_SIGNAL_WITH_REF.finditer(line):
        signal = signal_match.group(1) or signal_match.group(2)
        if not signal:
            continue
        if signal in _BOILERPLATE_TAGS:
            continue
        if re.match(r"^(CC|RC|BGA)\d", signal):
            continue
        signals.append((signal, signal_match.start()))
    return signals


# ---------------------------------------------------------------------------
# Helper: context snippet extractor
# ---------------------------------------------------------------------------

def _snippet(line: str, match_start: int, match_end: int, width: int = 80) -> str:
    """Return a trimmed context snippet around a match position."""
    start = max(0, match_start - width // 2)
    end = min(len(line), match_end + width // 2)
    s = line[start:end].strip()
    if start > 0:
        s = "…" + s
    if end < len(line):
        s = s + "…"
    return s


def _normalize_token(token: str) -> str:
    """Trim OCR punctuation from a token."""
    return token.strip("(),;:[]{}<>\"'")


def _is_semiconductor_part_number(token: str) -> bool:
    """Return True when *token* looks like an IC / silicon family part number."""
    upper = _normalize_token(token).upper()
    if not upper:
        return False
    if upper.startswith(_SEMICONDUCTOR_PART_PREFIXES):
        return True
    return any(
        hint in upper
        for hint in ("LVC1G", "HC595", "PCA9555", "TCA9555", "TPS", "FP6359")
    )


def _is_probable_part_number(token: str, refdes: str) -> bool:
    """Return True when *token* looks like a real component part number."""
    tok_clean = _normalize_token(token)
    upper = tok_clean.upper()
    if upper == refdes.upper():
        return False
    if _RE_REFDES.fullmatch(upper):
        return False
    if len(tok_clean) < 5:
        return False
    if any(c.islower() for c in tok_clean):
        return False
    if "_" in tok_clean:
        return False
    if "@" in tok_clean or "=" in tok_clean:
        return False
    if ":" in tok_clean:
        return False
    if tok_clean.startswith(("+", "-")):
        return False
    if _RE_VALUE_LIKE.fullmatch(upper):
        return False
    if re.fullmatch(r"\d+/\d+[A-Z]+", upper):
        return False
    if re.fullmatch(r"\d{8,}[A-Z]?", upper):
        return False
    if re.fullmatch(r"SHU\d+[A-Z]?", upper):
        return False

    has_alpha = any(c.isalpha() for c in tok_clean)
    has_digit = any(c.isdigit() for c in tok_clean)
    if not (has_alpha and has_digit):
        return False

    if any(upper.startswith(nw) for nw in _NOISE_WORDS):
        return False
    if _RE_BALL_COORD.match(upper):
        return False
    if _RE_FOOTPRINT.match(tok_clean):
        return False

    return True


def _window_text(lines: list[str], line_idx: int, radius: int = 1) -> str:
    """Return a small multi-line window around *line_idx* for context checks."""
    start = max(0, line_idx - radius)
    end = min(len(lines), line_idx + radius + 1)
    return "\n".join(lines[start:end]).upper()


def _ball_coord_count(text: str) -> int:
    """Count grid/ball coordinates that are not legal component refdes."""
    count = 0
    for raw in text.split():
        token = _normalize_token(raw).upper()
        if not token:
            continue
        if _RE_BALL_COORD.match(token) and not _RE_REFDES.fullmatch(token):
            count += 1
    return count


def _net_like_count(text: str) -> int:
    """Count net/power-like tokens in OCR text."""
    count = 0
    for raw in text.split():
        token = _normalize_token(raw).upper()
        if not token:
            continue
        if "_" in token or "/" in token or token.startswith(_POWER_CONTEXT_MARKERS):
            count += 1
    return count


def _looks_like_pinmap_noise(
    refdes: str,
    line: str,
    lines: list[str],
    line_idx: int,
) -> bool:
    """
    Detect BGA ball-table / pin-map noise such as J19, T17, T18 that are not
    actual schematic devices but package coordinates or layout references.
    """
    if _RE_TESTPOINT.match(refdes):
        return True

    radius = 6 if refdes.startswith("U") else 1
    window = _window_text(lines, line_idx, radius=radius)
    if refdes.startswith("T") and any(marker in window for marker in ("LAYOUT NOTES", "10DEGREE", " MIL")):
        return True

    ball_coords = _ball_coord_count(window)
    net_like = _net_like_count(window)
    has_pinmap_marker = any(marker in window for marker in _PINMAP_MARKERS)
    power_hits = sum(window.count(marker) for marker in _POWER_CONTEXT_MARKERS)

    if ball_coords >= 2 and net_like >= 2:
        return True
    if refdes.startswith("U") and has_pinmap_marker and net_like >= 2:
        return True
    if refdes.startswith(("J", "T")) and has_pinmap_marker and (ball_coords >= 1 or net_like >= 2):
        return True
    if refdes.startswith("T") and power_hits >= 2 and net_like >= 2:
        return True

    return False


# ---------------------------------------------------------------------------
# Tool 1: index_pdf_pages
# ---------------------------------------------------------------------------

@define_tool(
    description=(
        "Parse an extracted schematic .txt file and split it into pages. "
        "Returns a PageIndex with pdf_id and a dict mapping page numbers to content."
    )
)
def index_pdf_pages(path: str) -> dict[str, Any]:
    """
    Parse a schematic text file, split by "Sheet N of M" page markers.

    Args:
        path: Path to an .analysis/*.txt file.

    Returns:
        {"pdf_id": str, "pages": {page_number: content_text}}
    """
    p = Path(path)
    pdf_id = p.stem  # e.g. "mainboard" from "mainboard.txt"
    text = p.read_text(errors="replace")
    lines = text.splitlines()

    pages: dict[int, list[str]] = {}
    current_page = 1
    buf: list[str] = []

    for line in lines:
        buf.append(line)
        m = _RE_SHEET.search(line)
        if m:
            page_num = int(m.group(1))
            # Store accumulated buffer under this page number
            pages[page_num] = buf
            buf = []
            current_page = page_num + 1

    # Any trailing content after the last Sheet marker
    if buf:
        # If we never found any Sheet markers, put everything in page 1
        if not pages:
            pages[1] = buf
        # Otherwise leftover lines are start of next page (unlikely to matter)

    # Convert list-of-lines to joined text
    result_pages = {pn: "\n".join(plines) for pn, plines in sorted(pages.items())}

    return {"pdf_id": pdf_id, "pages": result_pages}


# ---------------------------------------------------------------------------
# Tool 2: build_tag_index
# ---------------------------------------------------------------------------

@define_tool(
    description=(
        "Scan schematic pages for TAG / net-label names (signal names like "
        "UART0_TX, PCIE2_CLKP, CPU_VDD, etc.) and off-page references. "
        "Returns a dict mapping tag_name to a list of occurrences."
    )
)
def build_tag_index(
    pdf_id: str,
    pages: dict[int, str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Build an index of signal / net-label tags found across pages.

    Args:
        pdf_id: Identifier for the PDF source (e.g. "mainboard").
        pages:  {page_number: content_text} as returned by index_pdf_pages.

    Returns:
        {tag_name: [{pdf_id, page, context}]}
    """
    # Convert string keys to int if needed (JSON round-trip safety)
    pages_int: dict[int, str] = {int(k): v for k, v in pages.items()}

    tag_index: dict[str, list[dict[str, Any]]] = {}

    for page_num, content in sorted(pages_int.items()):
        for line in content.splitlines():
            # Skip boilerplate footer lines
            if "Arcadyan" in line or "http://" in line:
                continue

            for m in _RE_TAG.finditer(line):
                tag = m.group(1)

                # Filter out boilerplate / non-signal names
                if tag in _BOILERPLATE_TAGS:
                    continue
                # Skip short tags that are likely noise
                if len(tag) < 4:
                    continue
                # Skip pure footprint/package codes like CC0201, RC1206
                if re.match(r"^(CC|RC|BGA|QFN|QFP|SOP|SOT|SOIC)\d", tag):
                    continue

                ctx = _snippet(line, m.start(), m.end())
                entry = {"pdf_id": pdf_id, "page": page_num, "context": ctx}

                tag_index.setdefault(tag, []).append(entry)

    # Deduplicate: keep unique (pdf_id, page) pairs per tag
    for tag in tag_index:
        seen = set()
        deduped = []
        for e in tag_index[tag]:
            key = (e["pdf_id"], e["page"])
            if key not in seen:
                seen.add(key)
                deduped.append(e)
        tag_index[tag] = deduped

    return tag_index


# ---------------------------------------------------------------------------
# Tool 3: build_refdes_index
# ---------------------------------------------------------------------------

def _find_part_number(lines: list[str], line_idx: int, refdes: str) -> str | None:
    """
    Heuristic: look for a part-number-like token in a small vertical window
    around the refdes.  Real schematic OCR often places the IC refdes, pin
    names, and part number on different text lines of the same symbol block.
    """
    search_before = 2
    search_after = 50 if refdes.upper().startswith("U") else 6
    start = max(0, line_idx - search_before)
    end = min(len(lines), line_idx + search_after + 1)

    best_token: str | None = None
    best_score = float("-inf")

    for candidate_idx in range(start, end):
        raw_line = lines[candidate_idx]
        for raw_token in raw_line.split():
            token = _normalize_token(raw_token)
            if not _is_probable_part_number(token, refdes):
                continue

            score = 0.0
            distance = abs(candidate_idx - line_idx)
            score -= distance
            if candidate_idx == line_idx:
                score += 8.0
            if refdes.upper().startswith("U") and _is_semiconductor_part_number(token):
                score += 100.0
            score += min(len(token), 24) / 8.0

            if score > best_score:
                best_score = score
                best_token = token

    return best_token


@define_tool(
    description=(
        "Scan schematic pages for reference designators (U, R, C, L, J, Q, D, "
        "FB, etc.) and attempt to extract nearby part numbers. "
        "Returns {refdes: [{pdf_id, page, part_number, context}]}."
    )
)
def build_refdes_index(
    pdf_id: str,
    pages: dict[int, str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Build an index of reference designators found across pages.

    Args:
        pdf_id: Identifier for the PDF source.
        pages:  {page_number: content_text}.

    Returns:
        {refdes: [{pdf_id, page, part_number (or None), context}]}
    """
    pages_int: dict[int, str] = {int(k): v for k, v in pages.items()}
    refdes_index: dict[str, list[dict[str, Any]]] = {}

    for page_num, content in sorted(pages_int.items()):
        lines = content.splitlines()
        for line_idx, line in enumerate(lines):
            # Skip footer/boilerplate
            if "Arcadyan" in line or "http://" in line:
                continue

            for m in _RE_REFDES.finditer(line):
                rd = m.group(1)
                if _RE_TESTPOINT.match(rd):
                    continue
                if rd.startswith(("J", "T", "U")) and _looks_like_pinmap_noise(rd, line, lines, line_idx):
                    continue
                # Skip tiny single-component refs that are really BGA coords
                # like U2, U3, U4 when they appear in pin-map context
                # (we still index them; downstream can filter)
                part = _find_part_number(lines, line_idx, rd)
                ctx = _snippet(line, m.start(), m.end())
                entry = {
                    "pdf_id": pdf_id,
                    "page": page_num,
                    "part_number": part,
                    "context": ctx,
                }
                refdes_index.setdefault(rd, []).append(entry)

    # Deduplicate per (pdf_id, page) — keep the one with a part_number if any
    for rd in refdes_index:
        best: dict[tuple, dict] = {}
        for e in refdes_index[rd]:
            key = (e["pdf_id"], e["page"])
            prev = best.get(key)
            if prev is None:
                best[key] = e
            elif e["part_number"] and not prev["part_number"]:
                best[key] = e
        refdes_index[rd] = list(best.values())

    return refdes_index


# ---------------------------------------------------------------------------
# Tool 4: build_connector_index
# ---------------------------------------------------------------------------

@define_tool(
    description=(
        "Find connector components (J-refdes) and extract pin name ↔ pin number "
        "mappings from the schematic pages. "
        "Returns {connector_refdes: {pdf_id, pins: {pin_name: pin_number}}}."
    )
)
def build_connector_index(
    pdf_id: str,
    pages: dict[int, str],
) -> dict[str, dict[str, Any]]:
    """
    Build an index of connectors and their pin mappings.

    Looks for patterns like:
      - "(10) PCIE2_CLKP" with nearby pin numbers (e.g. "6  5")
      - "signal_name  pin_number" or "pin_number  signal_name"

    Args:
        pdf_id: Identifier for the PDF source.
        pages:  {page_number: content_text}.

    Returns:
        {connector_refdes: {pdf_id, pins: {pin_name: pin_number}}}
    """
    pages_int: dict[int, str] = {int(k): v for k, v in pages.items()}
    connector_index: dict[str, dict[str, Any]] = {}

    # First pass: identify which pages contain each connector
    connector_pages: dict[str, list[int]] = {}
    for page_num, content in sorted(pages_int.items()):
        for m in _RE_CONNECTOR.finditer(content):
            conn = m.group(1)
            connector_pages.setdefault(conn, []).append(page_num)

    # Deduplicate page lists
    for conn in connector_pages:
        connector_pages[conn] = sorted(set(connector_pages[conn]))

    # Second pass: on connector pages, extract pin ↔ signal mappings.
    #
    # In the schematic text, connector pin sections have patterns like:
    #   (page_ref) SIGNAL_NAME   <whitespace>   pin_number
    #   pin_number   <whitespace>   SIGNAL_NAME (page_ref)
    # where pin_number is a small integer and SIGNAL_NAME is an upper-case
    # identifier with underscores.

    for conn, page_list in connector_pages.items():
        pins: dict[str, str] = {}
        pin_scores: dict[str, int] = {}

        for page_num in page_list:
            content = pages_int.get(page_num, "")
            lines = content.splitlines()
            connector_anchor_lines = [
                idx for idx, line in enumerate(lines)
                if re.search(rf"\b{re.escape(conn)}\b", line)
            ]

            for idx, line in enumerate(lines):
                if connector_anchor_lines and not any(
                    anchor - 4 <= idx <= anchor + 60
                    for anchor in connector_anchor_lines
                ):
                    continue
                # Skip boilerplate
                if "Arcadyan" in line or "http://" in line or "Date:" in line:
                    continue

                signal_matches = _extract_connector_signal_candidates(line)
                if not signal_matches:
                    continue

                candidate_groups = [(0, _extract_connector_pin_candidates(line))]
                if idx > 0:
                    candidate_groups.append((1, _extract_connector_pin_candidates(lines[idx - 1])))
                if idx + 1 < len(lines):
                    candidate_groups.append((1, _extract_connector_pin_candidates(lines[idx + 1])))

                if not any(candidates for _, candidates in candidate_groups):
                    continue

                for signal, signal_pos in signal_matches:
                    best_pin = None
                    best_score = None
                    for line_distance, pin_candidates in candidate_groups:
                        for pin_number, pin_pos in pin_candidates:
                            score = abs(pin_pos - signal_pos) + (line_distance * 500)
                            if best_score is None or score < best_score:
                                best_score = score
                                best_pin = pin_number

                    if best_pin is None or best_score is None:
                        continue
                    if signal not in pin_scores or best_score < pin_scores[signal]:
                        pins[signal] = best_pin
                        pin_scores[signal] = best_score

        if pins:
            connector_index[conn] = {"pdf_id": pdf_id, "pins": pins}

    # For connectors found but without extracted pins, still record them
    for conn in connector_pages:
        if conn not in connector_index:
            connector_index[conn] = {"pdf_id": pdf_id, "pins": {}}

    return connector_index


# ---------------------------------------------------------------------------
# Convenience: index everything in an analysis directory
# ---------------------------------------------------------------------------

def index_all_pdfs(analysis_dir: Path) -> dict[str, Any]:
    """
    Index all .txt files in the analysis directory.

    Returns:
        {
            "page_indices": {pdf_id: {page_num: content}},
            "tag_index":    {tag: [{pdf_id, page, context}]},
            "refdes_index": {refdes: [{pdf_id, page, part_number, context}]},
            "connector_index": {
                connector: {
                    pdf_id,
                    pins: {name: number},
                    pdfs: {pdf_id: {pdf_id, pins: {name: number}}}
                }
            }
        }
    """
    analysis_dir = Path(analysis_dir)
    page_indices: dict[str, dict[int, str]] = {}
    tag_index: dict[str, list[dict[str, Any]]] = {}
    refdes_index: dict[str, list[dict[str, Any]]] = {}
    connector_index: dict[str, dict[str, Any]] = {}

    for txt_file in sorted(analysis_dir.glob("*.txt")):
        result = index_pdf_pages(str(txt_file))
        pdf_id = result["pdf_id"]
        pages = result["pages"]

        page_indices[pdf_id] = pages

        # Merge tag index
        tags = build_tag_index(pdf_id, pages)
        for tag, entries in tags.items():
            tag_index.setdefault(tag, []).extend(entries)

        # Merge refdes index
        refdes = build_refdes_index(pdf_id, pages)
        for rd, entries in refdes.items():
            refdes_index.setdefault(rd, []).extend(entries)

        # Merge connector index
        connectors = build_connector_index(pdf_id, pages)
        for conn, info in connectors.items():
            pdf_entry = {
                "pdf_id": info.get("pdf_id"),
                "pins": dict(info.get("pins", {})),
            }
            if conn in connector_index:
                # Merge pins from multiple sources
                connector_index[conn].setdefault("pdfs", {})
                if pdf_entry["pdf_id"]:
                    connector_index[conn]["pdfs"][pdf_entry["pdf_id"]] = pdf_entry
                existing_pins = connector_index[conn].get("pins", {})
                existing_pins.update(pdf_entry["pins"])
                connector_index[conn]["pins"] = existing_pins
            else:
                connector_index[conn] = {
                    "pdf_id": pdf_entry["pdf_id"],
                    "pins": dict(pdf_entry["pins"]),
                    "pdfs": (
                        {pdf_entry["pdf_id"]: pdf_entry}
                        if pdf_entry["pdf_id"] else {}
                    ),
                }

    return {
        "page_indices": page_indices,
        "tag_index": tag_index,
        "refdes_index": refdes_index,
        "connector_index": connector_index,
    }
