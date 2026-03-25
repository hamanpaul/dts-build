from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_parser import AgentExtractionError, extract_spec_with_agent
from .manifest import Manifest
from .reference_dts import load_public_reference_dts
from .tables import read_table_rows


def extract_board_spec(
    folder: Path,
    manifest: Manifest,
    *,
    backend: str = "auto",
    model: str = "gpt-4.1",
    cli_url: str | None = None,
) -> dict[str, Any]:
    normalized_backend = backend.lower()
    if normalized_backend not in {"auto", "agent", "manual"}:
        raise ValueError(f"unsupported backend: {backend}")

    if normalized_backend in {"auto", "agent"}:
        try:
            return extract_spec_with_agent(folder, manifest, model=model, cli_url=cli_url)
        except AgentExtractionError as exc:
            if normalized_backend == "agent":
                raise
            fallback = _extract_manual_spec(folder, manifest)
            fallback.setdefault("meta", {})
            fallback["meta"]["backend"] = "manual"
            fallback["meta"]["fallback_reason"] = str(exc)
            return fallback

    spec = _extract_manual_spec(folder, manifest)
    spec.setdefault("meta", {})
    spec["meta"]["backend"] = "manual"
    return spec


def _extract_manual_spec(folder: Path, manifest: Manifest) -> dict[str, Any]:
    blockdiag_rows = read_optional_table(folder, manifest.artifacts.get("blockdiag_table"))
    ddr_rows = read_optional_table(folder, manifest.artifacts.get("ddr_table"))
    network_rows = read_optional_table(folder, manifest.artifacts.get("network_table"))
    gpio_rows = read_optional_table(folder, manifest.artifacts.get("gpio_led_table"))
    ddr_fields = _collect_key_value_rows(ddr_rows)
    public_reference = load_public_reference_dts(folder, manifest)

    return {
        "meta": {
            "project": manifest.project,
            "family": manifest.family,
            "profile": manifest.profile,
            "refboard": manifest.refboard,
            "output_dir": manifest.output_dir,
        },
        "public_reference": public_reference,
        "block_diagram": {"rows": normalize_blockdiag_rows(blockdiag_rows)},
        "memory": {
            "memcfg_macro": ddr_fields.get("memcfg_macro", ""),
            "fields": ddr_fields,
        },
        "network": {
            "rows": [
                {
                    "name": row.get("name", ""),
                    "present": row.get("present", ""),
                    "role": row.get("role", ""),
                    "source": row.get("source", ""),
                    "phy_handle": row.get("phy_handle", ""),
                    "phy_mode": row.get("phy_mode", ""),
                    "phy_group": row.get("phy_group", ""),
                    "switch_port": row.get("switch_port", ""),
                    "port_group": row.get("port_group", ""),
                    "lane_count": row.get("lane_count", ""),
                    "lane_swap_status": row.get("lane_swap_status", ""),
                    "notes": row.get("notes", ""),
                }
                for row in network_rows
                if any(value for value in row.values())
            ],
        },
        "gpio": {
            "rows": normalize_gpio_rows(gpio_rows),
        },
        "missing_fields": [],
        "assumptions": [],
    }


def read_optional_table(folder: Path, relative_path: Any) -> list[dict[str, str]]:
    if not relative_path:
        return []
    if isinstance(relative_path, list):
        candidates = [(folder / item).resolve() for item in relative_path]
    else:
        candidates = [(folder / str(relative_path)).resolve()]

    for path in candidates:
        if path.exists() and path.suffix.lower() in {".csv", ".xlsx", ".xlsm"}:
            return read_table_rows(path)
    return []


def _collect_key_value_rows(rows: list[dict[str, str]]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for row in rows:
        field = row.get("field", "").strip().lower()
        if field:
            normalized[field] = row.get("value", "").strip()
    return normalized


def normalize_blockdiag_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        if {"domain", "interface", "present"}.issubset(row.keys()):
            normalized.append(
                {
                    "domain": row.get("domain", "").strip(),
                    "interface": row.get("interface", "").strip(),
                    "present": row.get("present", "").strip(),
                    "controller": row.get("controller", "").strip(),
                    "endpoint": row.get("endpoint", "").strip(),
                    "page_ref": row.get("page_ref", "").strip(),
                    "notes": row.get("notes", "").strip(),
                }
            )
            continue
        if any(str(value).strip() for value in row.values()):
            normalized.append({str(k): str(v) for k, v in row.items()})
    return normalized


def normalize_gpio_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        if {"category", "name", "signal", "pin_or_gpio", "polarity"}.issubset(row.keys()):
            normalized.append(row)
            continue

        pinout_key = _find_key(row, "pinout")
        function_key = _find_key(row, "function description")
        pin_key = _find_exact_key(row, "pin")
        active_key = _find_exact_key(row, "active")
        note_key = _find_exact_key(row, "note")
        io_key = _find_key(row, "i/o") or _find_exact_key(row, "io")

        if pinout_key and pin_key:
            name = row.get(pinout_key, "").strip()
            signal = row.get(function_key, "").strip() if function_key else ""
            normalized.append(
                {
                    "category": _guess_gpio_category(name, signal),
                    "name": name,
                    "signal": signal or name,
                    "pin_or_gpio": row.get(pin_key, "").strip(),
                    "polarity": row.get(active_key, "").strip() if active_key else "",
                    "io": row.get(io_key, "").strip() if io_key else "",
                    "notes": row.get(note_key, "").strip() if note_key else "",
                }
            )
            continue

        if any(str(value).strip() for value in row.values()):
            normalized.append({"category": "raw", "name": str(row)})

    return [row for row in normalized if any(value for value in row.values())]


def _find_key(row: dict[str, str], needle: str) -> str | None:
    lowered = needle.lower()
    for key in row:
        if lowered in key.lower():
            return key
    return None


def _find_exact_key(row: dict[str, str], needle: str) -> str | None:
    lowered = needle.lower()
    for key in row:
        if key.lower() == lowered:
            return key
    return None


def _guess_gpio_category(name: str, signal: str) -> str:
    text = f"{name} {signal}".lower()
    if "led" in text:
        return "led"
    return "gpio"
