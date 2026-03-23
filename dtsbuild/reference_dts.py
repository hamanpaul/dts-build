from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .manifest import Manifest


def load_public_reference_dts(folder: Path, manifest: Manifest) -> dict[str, Any]:
    artifact = manifest.artifacts.get("public_ref_dts")
    if not artifact:
        return {}

    path = (folder / str(artifact)).resolve()
    if not path.exists():
        return {
            "path": str(artifact),
            "exists": False,
            "notes": ["public_ref_dts artifact is configured but the file does not exist"],
        }

    return parse_public_reference_dts(path)


def parse_public_reference_dts(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    memcfg_macro = _extract_memcfg_macro(text)
    ddr_signature = _extract_ddr_signature(memcfg_macro)

    patterns = [
        pattern
        for pattern, regex in {
            "buttons": r"\bbuttons\s*\{",
            "leds": r"\bleds\s*\{",
            "wan_sfp": r"\bwan_sfp\s*:",
            "lan_sfp": r"\blan_sfp\s*:",
            "wan_serdes": r"&wan_serdes\s*\{",
            "switch0": r"&switch0\s*\{",
            "mdio": r"&mdio(_bus)?\s*\{",
            "pcie": r"&pcie[0-9]+\s*\{",
            "i2c": r"&i2c[0-9]+\s*\{",
            "tod": r"\btod\s*\{",
        }.items()
        if re.search(regex, text)
    ]

    compatibles = sorted(set(re.findall(r'compatible\s*=\s*"([^"]+)";', text)))

    return {
        "path": path.name,
        "exists": True,
        "model": _extract_first_group(text, r'model\s*=\s*"([^"]+)";'),
        "includes": re.findall(r'^\s*#include\s+"([^"]+)"', text, flags=re.MULTILINE),
        "memcfg_macro": memcfg_macro,
        "ddr_signature": ddr_signature,
        "compatibles": compatibles,
        "patterns": patterns,
        "notes": [
            "Public reference DTS is treated as a pattern source only; it must not override schematic/table evidence."
        ],
    }


def select_reference_memcfg(public_reference: dict[str, Any], ddr_fields: dict[str, str]) -> tuple[str, str]:
    if not public_reference or not public_reference.get("exists"):
        return "", ""

    memcfg_macro = str(public_reference.get("memcfg_macro", "")).strip()
    if not memcfg_macro:
        return "", ""

    signature = public_reference.get("ddr_signature") or {}
    if not isinstance(signature, dict):
        signature = {}

    type_matches = _field_matches(signature.get("ddr_type", ""), ddr_fields.get("ddr_type", ""))
    size_matches = _field_matches(signature.get("ddr_size", ""), ddr_fields.get("ddr_size", ""))
    width_matches = _field_matches(signature.get("width", ""), ddr_fields.get("width", ""))

    if type_matches and size_matches and width_matches:
        path = public_reference.get("path", "<unknown>")
        return memcfg_macro, f"Derived from public_ref_dts {path} after matching DDR type/size/width."
    return "", ""


def _field_matches(reference_value: str, local_value: str) -> bool:
    ref = _normalize_compare_value(reference_value)
    local = _normalize_compare_value(local_value)
    if not ref or not local:
        return True
    return ref == local


def _normalize_compare_value(value: str) -> str:
    return value.strip().lower().replace(" ", "").replace("_", "")


def _extract_memcfg_macro(text: str) -> str:
    match = re.search(r"memcfg\s*=\s*<\((.*?)\)>;", text, flags=re.DOTALL)
    if not match:
        return ""
    macro = match.group(1)
    macro = macro.replace("\\", " ")
    macro = re.sub(r"\s+", " ", macro).strip()
    return macro


def _extract_ddr_signature(memcfg_macro: str) -> dict[str, str]:
    return {
        "ddr_type": _normalize_ddr_type(_extract_first_group(memcfg_macro, r"(BP1?_DDR_TYPE_[A-Z0-9]+)")),
        "ddr_size": _normalize_ddr_size(_extract_first_group(memcfg_macro, r"(BP1?_DDR_TOTAL_SIZE_[A-Za-z0-9]+)")),
        "width": _normalize_width(_extract_first_group(memcfg_macro, r"(BP1?_DDR_WIDTH_[0-9]+BIT)")),
    }


def _normalize_ddr_type(token: str) -> str:
    if "LPDDR4" in token:
        return "LPDDR4"
    if "LPDDR5" in token:
        return "LPDDR5"
    if "DDR4" in token:
        return "DDR4"
    if "DDR5" in token:
        return "DDR5"
    return ""


def _normalize_ddr_size(token: str) -> str:
    match = re.search(r"([0-9]+[A-Za-z]+)", token)
    return match.group(1) if match else ""


def _normalize_width(token: str) -> str:
    match = re.search(r"([0-9]+)BIT", token)
    return f"x{match.group(1)}" if match else ""


def _extract_first_group(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1).strip() if match else ""
