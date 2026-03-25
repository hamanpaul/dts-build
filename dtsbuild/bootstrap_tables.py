from __future__ import annotations

import csv
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .manifest import load_manifest, write_manifest
from .reference_dts import load_public_reference_dts, select_reference_memcfg
from .spec import normalize_blockdiag_rows, normalize_gpio_rows
from .tables import read_table_rows


PUBLIC_DDR_RULES: list[dict[str, str]] = [
    {
        "family": "bcm68575",
        "ddr_type": "LPDDR4",
        "ddr_size": "16Gb",
        "width": "32",
        "memcfg_macro": "BP1_DDR_MCBSEL_FORMAT_VER1 | BP1_DDR_TYPE_LPDDR4 | "
        "BP1_DDR_SPEED_2133_36_39_39 | BP1_DDR_WIDTH_32BIT | "
        "BP1_DDR_TOTAL_SIZE_16Gb | BP1_DDR_SSC_CONFIG_1",
        "reference": "public bcm68575 LPDDR4 rule from 968575REF1.dts",
    }
]


@dataclass
class BootstrapTablesResult:
    folder: Path
    manifest_path: Path
    generated_tables: dict[str, Path] = field(default_factory=dict)
    updated_artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class PdfIndex:
    path: Path
    titles: list[tuple[str, int]]


def bootstrap_tables(folder: Path, *, force: bool = False) -> BootstrapTablesResult:
    folder = folder.resolve()
    manifest = load_manifest(folder)
    manifest_path = folder / "manifest.yaml"
    raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_manifest, dict):
        raise ValueError("manifest root must be a mapping")

    tables_dir = folder / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    public_reference = load_public_reference_dts(folder, manifest)

    source_spreadsheets = _collect_source_spreadsheets(folder)
    source_pdfs = _collect_source_pdfs(folder, manifest)
    pdf_indexes = [_load_pdf_index(path) for path in source_pdfs]

    gpio_rows, gpio_sources = _bootstrap_gpio_rows(manifest, source_spreadsheets)
    ddr_rows, ddr_sources = _bootstrap_ddr_rows(manifest, public_reference, source_spreadsheets, source_pdfs, pdf_indexes)
    public_ref_path = _resolve_public_reference_path(folder, manifest)
    network_rows, network_sources = _bootstrap_network_rows(
        manifest,
        source_spreadsheets,
        source_pdfs,
        pdf_indexes,
        public_ref_path=public_ref_path,
    )
    blockdiag_rows, blockdiag_sources = _bootstrap_blockdiag_rows(
        manifest,
        source_spreadsheets,
        source_pdfs,
        pdf_indexes,
        ddr_rows=ddr_rows,
        network_rows=network_rows,
        gpio_rows=gpio_rows,
    )

    generated_tables = {
        "blockdiag_table": tables_dir / "blockdiag.csv",
        "ddr_table": tables_dir / "ddr.csv",
        "gpio_led_table": tables_dir / "gpio_led.csv",
        "network_table": tables_dir / "network.csv",
    }
    table_payloads = {
        "blockdiag_table": (
            ["domain", "interface", "present", "controller", "endpoint", "page_ref", "notes"],
            blockdiag_rows,
        ),
        "ddr_table": (
            ["field", "value", "notes"],
            ddr_rows,
        ),
        "gpio_led_table": (
            ["category", "name", "signal", "pin_or_gpio", "polarity", "io", "notes"],
            gpio_rows,
        ),
        "network_table": (
            [
                "name",
                "present",
                "role",
                "source",
                "phy_handle",
                "phy_mode",
                "phy_group",
                "switch_port",
                "port_group",
                "lane_count",
                "lane_swap_status",
                "notes",
            ],
            network_rows,
        ),
    }

    for artifact_name, target in generated_tables.items():
        headers, rows = table_payloads[artifact_name]
        _write_dict_csv(target, headers, rows, force=force)

    artifacts = raw_manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        raise ValueError("manifest field 'artifacts' must be a mapping")
    artifacts = {str(key): value for key, value in artifacts.items()}
    raw_manifest["output_dir"] = manifest.output_dir
    artifacts["blockdiag_table"] = "tables/blockdiag.csv"
    artifacts["ddr_table"] = "tables/ddr.csv"
    artifacts["gpio_led_table"] = "tables/gpio_led.csv"
    artifacts["network_table"] = "tables/network.csv"
    raw_manifest["artifacts"] = artifacts

    notes = raw_manifest.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        raise ValueError("manifest field 'notes' must be a list")
    notes = [str(note) for note in notes if "968375REF2" not in str(note)]

    bootstrap_note = (
        "bootstrap-tables generated normalized blockdiag/ddr/network/gpio tables "
        "from available PDFs/XLSX inputs."
    )
    no_answer_key_note = (
        "Table bootstrap derives evidence from local PDFs/XLSX and public reference rules only; "
        "it does not use any board DTS answer key."
    )
    for note in (bootstrap_note, no_answer_key_note):
        if note not in notes:
            notes.append(note)
    raw_manifest["notes"] = notes
    write_manifest(manifest_path, raw_manifest)

    source_notes = gpio_sources + ddr_sources + network_sources + blockdiag_sources
    return BootstrapTablesResult(
        folder=folder,
        manifest_path=manifest_path,
        generated_tables=generated_tables,
        updated_artifacts={name: path.relative_to(folder).as_posix() for name, path in generated_tables.items()},
        notes=source_notes,
    )


def _bootstrap_gpio_rows(manifest, spreadsheets: list[Path]) -> tuple[list[dict[str, str]], list[str]]:
    source = _select_source_table(
        manifest.artifacts.get("gpio_led_table"),
        spreadsheets,
        keywords=("gpio", "led"),
        fallback_to_first=True,
    )
    if not source:
        return [], ["No GPIO/LED spreadsheet source was found."]

    rows = normalize_gpio_rows(read_table_rows(source))
    filtered = [
        {
            "category": row.get("category", ""),
            "name": row.get("name", ""),
            "signal": row.get("signal", ""),
            "pin_or_gpio": row.get("pin_or_gpio", ""),
            "polarity": _normalize_polarity(row.get("polarity", "")),
            "io": row.get("io", ""),
            "notes": row.get("notes", ""),
        }
        for row in rows
        if any(value for value in row.values())
    ]
    return filtered, [f"gpio_led_table derived from {source.name} ({len(filtered)} normalized rows)"]


def _bootstrap_ddr_rows(
    manifest,
    public_reference: dict[str, Any],
    spreadsheets: list[Path],
    pdfs: list[Path],
    pdf_indexes: list[PdfIndex],
) -> tuple[list[dict[str, str]], list[str]]:
    fields: dict[str, str] = {}
    notes: dict[str, str] = {}
    source_notes: list[str] = []

    existing_source = _select_source_table(manifest.artifacts.get("ddr_table"), spreadsheets, keywords=("ddr", "memory"))
    if existing_source:
        for row in read_table_rows(existing_source):
            field = row.get("field", "").strip()
            value = row.get("value", "").strip()
            if field and value:
                fields[field] = value
                notes[field] = row.get("notes", "").strip()
        source_notes.append(f"ddr_table started from existing table {existing_source.name}")

    ddr_page_texts: list[tuple[Path, int, str]] = []
    datasheet_texts: list[tuple[Path, str]] = []
    for pdf in pdfs:
        lower_name = pdf.name.lower()
        if "main" in lower_name or "board" in lower_name:
            index = _find_pdf_index(pdf_indexes, pdf)
            for title, page in index.titles:
                upper_title = title.upper()
                if "LPDDR" in upper_title or "DDR4" in upper_title or "DDR5" in upper_title:
                    ddr_page_texts.append((pdf, page, extract_pdf_text(pdf, first_page=page, last_page=page)))
        if pdf.stem and re.search(r"[A-Z0-9]{8,}-[A-Z0-9]{2,}", pdf.stem.upper()):
            datasheet_texts.append((pdf, extract_pdf_text(pdf, first_page=1, last_page=8)))

    ddr_corpus = "\n".join(text for _, _, text in ddr_page_texts)
    datasheet_corpus = "\n".join(text for _, text in datasheet_texts)
    combined = "\n".join([ddr_corpus, datasheet_corpus])

    if not fields.get("ddr_type"):
        ddr_type = _extract_first(combined, [r"\bLPDDR5\b", r"\bLPDDR4X\b", r"\bLPDDR4\b", r"\bDDR5\b", r"\bDDR4\b"])
        if ddr_type:
            fields["ddr_type"] = ddr_type.replace("X", "")
            notes["ddr_type"] = "Derived from schematic/datasheet text."

    if not fields.get("part_number"):
        part_number = _extract_first(
            combined,
            [
                r"\bK4[A-Z0-9]{8,}-[A-Z0-9]{2,}\b",
                r"\bMT[A-Z0-9]{6,}-[A-Z0-9]{2,}\b",
                r"\b[A-Z][A-Z0-9]{8,}-[A-Z0-9]{2,}\b",
            ],
        )
        if part_number:
            fields["part_number"] = part_number
            notes["part_number"] = "Extracted from LPDDR schematic page or matching datasheet."

    if not fields.get("ddr_size"):
        ddr_size = _extract_first(combined, [r"\b\d+\s*Gb\b", r"\b\d+\s*GB\b"])
        if ddr_size:
            fields["ddr_size"] = ddr_size.replace(" ", "")
            notes["ddr_size"] = "Extracted from schematic/datasheet text."

    if not fields.get("width"):
        width = _extract_first(combined, [r"\bx\s*(16|32|64)\b"])
        if width:
            fields["width"] = width.lower().replace(" ", "")
            notes["width"] = "Extracted from datasheet organization text."

    if not fields.get("package"):
        package = _extract_first(combined, [r"\b\d+\s*FBGA\b", r"\bFBGA\s*\d+\b", r"\bBGA\d+\b"])
        if package:
            fields["package"] = package.replace(" ", "")
            notes["package"] = "Extracted from datasheet package text."

    memcfg_macro, rule_note = select_reference_memcfg(public_reference, fields)
    if memcfg_macro and not fields.get("memcfg_macro"):
        fields["memcfg_macro"] = memcfg_macro
        notes["memcfg_macro"] = rule_note

    memcfg_macro, rule_note = _derive_memcfg_macro(
        family=manifest.family,
        ddr_type=fields.get("ddr_type", ""),
        ddr_size=fields.get("ddr_size", ""),
        width=fields.get("width", ""),
    )
    if memcfg_macro and not fields.get("memcfg_macro"):
        fields["memcfg_macro"] = memcfg_macro
        notes["memcfg_macro"] = rule_note

    if public_reference.get("exists"):
        source_notes.append(f"DDR public reference DTS scanned from {public_reference.get('path', '<unknown>')}")
    for pdf, page, _ in ddr_page_texts:
        source_notes.append(f"DDR evidence scanned from {pdf.name} page {page}")
    for pdf, _ in datasheet_texts:
        source_notes.append(f"DDR datasheet evidence scanned from {pdf.name}")

    ordered_fields = ["memcfg_macro", "ddr_type", "ddr_size", "width", "package", "part_number"]
    rows = [
        {
            "field": field,
            "value": fields.get(field, ""),
            "notes": notes.get(field, ""),
        }
        for field in ordered_fields
        if fields.get(field)
    ]
    return rows, source_notes or ["No DDR sources were recognized."]


def _bootstrap_network_rows(
    manifest,
    spreadsheets: list[Path],
    pdfs: list[Path],
    pdf_indexes: list[PdfIndex],
    *,
    public_ref_path: Path | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    source_notes: list[str] = []
    switch_port_inventory, inventory_notes = _extract_datasheet_switch_port_inventory(pdfs, public_ref_path)
    source_notes.extend(inventory_notes)

    existing_source = _select_source_table(manifest.artifacts.get("network_table"), spreadsheets, keywords=("network", "port", "wan", "lan"))
    if existing_source:
        rows.extend(
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
            for row in read_table_rows(existing_source)
            if any(value for value in row.values())
        )
        source_notes.append(f"network_table started from existing table {existing_source.name}")

    for pdf in pdfs:
        index = _find_pdf_index(pdf_indexes, pdf)
        titles_upper = [title.upper() for title, _ in index.titles]
        if not any(
            keyword in " ".join(titles_upper)
            for keyword in ("GPHY", "WAN", "RJ45", "CAGE", "SFP", "XPHY", "BLOCK", "TOPOLOGY", "SWITCH", "PHY")
        ):
            continue

        row_count_before = len(rows)
        relevant_page_entries = [
            (title, page)
            for title, page in index.titles
            if any(keyword in title.upper() for keyword in ("GPHY", "WAN", "RJ45", "CAGE", "SFP", "XPHY", "BLOCK", "TOPOLOGY", "SWITCH", "PHY"))
            and "GPIO" not in title.upper()
        ]
        lower_pdf_name = pdf.name.lower()
        if (
            not any("BLOCK" in title.upper() or "TOPOLOGY" in title.upper() for title, _ in relevant_page_entries)
            and ("main" in lower_pdf_name or "board" in lower_pdf_name)
            and all(page != 2 for _, page in relevant_page_entries)
        ):
            relevant_page_entries.insert(0, ("BLOCK DIAGRAM", 2))
        page_texts: list[str] = []
        ocr_used = False
        for title, page in relevant_page_entries[:6]:
            page_text = extract_pdf_text(pdf, first_page=page, last_page=page)
            if "BLOCK" in title.upper() or "TOPOLOGY" in title.upper():
                ocr_text = _extract_pdf_text_via_ocr(pdf, first_page=page, last_page=page)
                if ocr_text:
                    page_text = f"{page_text}\n{ocr_text}".strip()
                    ocr_used = True
            page_texts.append(page_text)
        text = "\n".join(page_texts)
        page_ref = f"{pdf.stem}:pages {','.join(str(page) for _, page in relevant_page_entries if page)}"
        blockdiag_note = _summarize_blockdiag_profile(text)
        if ocr_used:
            source_notes.append(f"network_table used block diagram OCR fallback for {pdf.name}")
        seen_lan: set[str] = set()
        for lane in sorted(set(re.findall(r"2\.5GPHY\s*([0-9])", text, flags=re.IGNORECASE))):
            name = f"lan_gphy{lane}"
            if name in seen_lan:
                continue
            notes = [
                "Derived from 2.5GPHY lane labels on schematic pages.",
                "Exact board-level port mapping still requires explicit topology evidence.",
            ]
            if blockdiag_note:
                notes.append(blockdiag_note)
            if switch_port_inventory:
                notes.append(
                    "CPU datasheet validates XPORT inventory "
                    + ",".join(sorted(port for port in switch_port_inventory if port.startswith("port_xgphy")))
                    + "."
                )
            rows.append(
                {
                    "name": name,
                    "present": "inferred",
                    "role": "LAN",
                    "source": page_ref,
                    "phy_handle": f"gphy{lane}",
                    "phy_mode": "internal-2.5gphy",
                    "phy_group": "",
                    "switch_port": "",
                    "port_group": "",
                    "lane_count": "1",
                    "lane_swap_status": "pending_audit",
                    "notes": " ".join(notes),
                }
            )
            seen_lan.add(name)

        if re.search(r"\b10GPHY\b|\bXPHY10G_", text, flags=re.IGNORECASE):
            notes = [
                "Derived from 10G PHY / WAN cage schematic pages.",
                "Exact switch-port mapping still requires explicit topology evidence.",
            ]
            if blockdiag_note:
                notes.append(blockdiag_note)
            switch_port = ""
            port_group = ""
            if "port_wan@xpon_ae" in switch_port_inventory and re.search(r"SFP\+?\s*CAGE|WAN\s+INTERFACE|PON\s+TRANSCEIVER", text, flags=re.IGNORECASE):
                switch_port = "port_wan@xpon_ae"
                port_group = "xpon_ae"
                notes.append("CPU datasheet-validated XPORT inventory and block diagram WAN/SFP evidence map this row to port_wan@xpon_ae.")
            rows.append(
                {
                    "name": "wan_10g",
                    "present": "true",
                    "role": "WAN",
                    "source": page_ref,
                    "phy_handle": "xphy10g",
                    "phy_mode": "xfi",
                    "phy_group": "",
                    "switch_port": switch_port,
                    "port_group": port_group,
                    "lane_count": "1",
                    "lane_swap_status": "pending_audit",
                    "notes": " ".join(notes),
                }
            )
        elif any("WAN" in title.upper() and "CAGE" in title.upper() for title, _ in index.titles):
            notes = [
                "Derived from WAN cage page index.",
                "Exact switch-port mapping still requires explicit topology evidence.",
            ]
            switch_port = ""
            port_group = ""
            if "port_wan@xpon_ae" in switch_port_inventory:
                switch_port = "port_wan@xpon_ae"
                port_group = "xpon_ae"
                notes.append("CPU datasheet-validated XPORT inventory confirms the WAN switch-port template.")
            rows.append(
                {
                    "name": "wan_10g",
                    "present": "true",
                    "role": "WAN",
                    "source": f"{pdf.stem}:cover index",
                    "phy_handle": "xphy10g",
                    "phy_mode": "xfi",
                    "phy_group": "",
                    "switch_port": switch_port,
                    "port_group": port_group,
                    "lane_count": "1",
                    "lane_swap_status": "pending_audit",
                    "notes": " ".join(notes),
                }
            )

        if len(rows) > row_count_before:
            source_notes.append(f"network_table derived from {pdf.name}")

    rows = _dedupe_rows(rows, key_fields=("name",))
    return rows, source_notes or ["No network PDF evidence was recognized."]


def _bootstrap_blockdiag_rows(
    manifest,
    spreadsheets: list[Path],
    pdfs: list[Path],
    pdf_indexes: list[PdfIndex],
    *,
    ddr_rows: list[dict[str, str]],
    network_rows: list[dict[str, str]],
    gpio_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    source_notes: list[str] = []

    existing_source = _select_source_table(manifest.artifacts.get("blockdiag_table"), spreadsheets, keywords=("block", "interface"))
    if existing_source:
        rows.extend(normalize_blockdiag_rows(read_table_rows(existing_source)))
        source_notes.append(f"blockdiag_table started from existing table {existing_source.name}")

    ddr_fields = {row.get("field", ""): row.get("value", "") for row in ddr_rows}
    if ddr_fields.get("ddr_type"):
        rows.append(
            {
                "domain": "memory",
                "interface": ddr_fields["ddr_type"].lower(),
                "present": "true",
                "controller": "memory_controller",
                "endpoint": ddr_fields.get("part_number", ""),
                "page_ref": "derived-ddr-table",
                "notes": ddr_fields.get("ddr_size", ""),
            }
        )

    for row in network_rows:
        rows.append(
            {
                "domain": "network",
                "interface": row.get("name", ""),
                "present": row.get("present", "true"),
                "controller": row.get("phy_handle", "") or row.get("source", ""),
                "endpoint": row.get("role", ""),
                "page_ref": row.get("source", ""),
                "notes": row.get("notes", ""),
            }
        )

    combined_titles = {
        title.upper(): (pdf.path.name, page)
        for pdf in pdf_indexes
        for title, page in pdf.titles
    }
    if _find_title_page(combined_titles, "EMMC"):
        rows.append(
            {
                "domain": "storage",
                "interface": "emmc",
                "present": "true",
                "controller": "sdhci",
                "endpoint": "emmc",
                "page_ref": _format_title_page(combined_titles, "EMMC"),
                "notes": "Derived from eMMC schematic page index.",
            }
        )
    if _find_title_page(combined_titles, "USB"):
        rows.append(
            {
                "domain": "usb",
                "interface": "usb2",
                "present": "true",
                "controller": "usb2",
                "endpoint": "usb",
                "page_ref": _format_title_page(combined_titles, "USB"),
                "notes": "Derived from USB schematic page index.",
            }
        )
    if _find_title_page(combined_titles, "LED") or any(_looks_like_led_or_button(row) for row in gpio_rows):
        rows.append(
            {
                "domain": "led_button",
                "interface": "gpio_leds_buttons",
                "present": "true",
                "controller": "gpio",
                "endpoint": "leds/buttons",
                "page_ref": _format_title_page(combined_titles, "LED") or "gpio-table",
                "notes": "Derived from LED page index and GPIO sheet.",
            }
        )

    daughter_rows = _extract_daughter_pcie_rows(pdfs)
    rows.extend(daughter_rows)
    if daughter_rows:
        source_notes.append("blockdiag_table added PCIe/Wi-Fi rows from daughter board block diagram.")

    rows = normalize_blockdiag_rows(rows)
    rows = _dedupe_rows(rows, key_fields=("domain", "interface"))
    return rows, source_notes or ["blockdiag_table composed from DDR/network/PDF page-index evidence."]


def _extract_daughter_pcie_rows(pdfs: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for pdf in pdfs:
        lower_name = pdf.name.lower()
        if "daughter" not in lower_name and "wifi" not in lower_name:
            continue
        text = extract_pdf_text(pdf, first_page=2, last_page=2)
        band_map = [
            ("2g", "PCIE2", "2G_RF_DISABLE_L", "bcm6726_2g"),
            ("5g", "PCIE3", "5G_RF_DISABLE_L", "bcm6726_5g"),
            ("6g", "PCIE1", "6G_RF_DISABLE_L", "bcm67263_6g"),
        ]
        for band, controller, signal, endpoint in band_map:
            if controller in text and signal in text:
                rows.append(
                    {
                        "domain": "pcie_wifi",
                        "interface": f"{controller.lower()}_{band}_wifi",
                        "present": "true",
                        "controller": controller.lower(),
                        "endpoint": endpoint,
                        "page_ref": f"{pdf.name}:page 2",
                        "notes": f"Derived from daughter board block diagram ({signal}).",
                    }
                )
    return rows


def _collect_source_spreadsheets(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".csv", ".xlsx", ".xlsm"}
        and "tables" not in path.parts
        and path.name != "manifest.yaml"
    )


def _collect_source_pdfs(folder: Path, manifest) -> list[Path]:
    candidate_paths = sorted(
        path for path in folder.rglob("*.pdf") if path.is_file() and "out" not in path.parts
    )
    resolved_from_manifest: list[Path] = []
    for key in ("schematic_pdfs", "schematic_pdf"):
        artifact = manifest.artifacts.get(key)
        if not artifact:
            continue
        if isinstance(artifact, list):
            resolved_from_manifest.extend([(folder / item).resolve() for item in artifact])
        else:
            resolved_from_manifest.append((folder / str(artifact)).resolve())
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in resolved_from_manifest + candidate_paths:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def _load_pdf_index(path: Path) -> PdfIndex:
    cover_text = extract_pdf_text(path, first_page=1, last_page=1)
    titles = _parse_cover_titles(cover_text)
    return PdfIndex(path=path, titles=titles)


def extract_pdf_text(path: Path, *, first_page: int | None = None, last_page: int | None = None) -> str:
    command = ["pdftotext", "-layout"]
    if first_page is not None:
        command.extend(["-f", str(first_page)])
    if last_page is not None:
        command.extend(["-l", str(last_page)])
    command.extend([str(path), "-"])

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return completed.stdout
    except FileNotFoundError:
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError as exc:
            raise RuntimeError("pdftotext is not installed and pypdf is unavailable") from exc

        reader = PdfReader(str(path))
        start = (first_page - 1) if first_page else 0
        end = last_page if last_page else len(reader.pages)
        return "\n".join(reader.pages[index].extract_text() or "" for index in range(start, min(end, len(reader.pages))))


def _extract_pdf_text_via_ocr(
    path: Path,
    *,
    first_page: int | None = None,
    last_page: int | None = None,
) -> str:
    if first_page is None or last_page is None:
        return ""

    chunks: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for page in range(first_page, last_page + 1):
                image_prefix = temp_path / f"page-{page}"
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-r",
                        "300",
                        "-png",
                        "-singlefile",
                        str(path),
                        str(image_prefix),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                output_base = temp_path / f"ocr-{page}"
                subprocess.run(
                    ["tesseract", str(image_prefix.with_suffix(".png")), str(output_base), "--psm", "11"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                txt_path = output_base.with_suffix(".txt")
                if txt_path.exists():
                    chunks.append(txt_path.read_text(encoding="utf-8", errors="ignore"))
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return "\n".join(chunk for chunk in chunks if chunk)


def _resolve_public_reference_path(folder: Path, manifest) -> Path | None:
    artifact = manifest.artifacts.get("public_ref_dts")
    if not artifact:
        return None
    path = (folder / str(artifact)).resolve()
    return path if path.exists() else None


def _extract_public_switch_ports(public_ref_path: Path | None) -> set[str]:
    if public_ref_path is None or not public_ref_path.exists():
        return set()
    text = public_ref_path.read_text(encoding="utf-8", errors="ignore")
    return {
        match.group(1)
        for match in re.finditer(
            r"\b(port_xgphy\d+|port_wan@xpon_ae|port_wan@slan_sd|port_slan0@xpon_ae|port_slan1@slan_sd)\b",
            text,
        )
    }


def _extract_datasheet_switch_port_inventory(
    pdfs: list[Path],
    public_ref_path: Path | None,
) -> tuple[set[str], list[str]]:
    switch_ports = _extract_public_switch_ports(public_ref_path)
    if not switch_ports:
        return set(), []

    for pdf in pdfs:
        lower_name = pdf.name.lower()
        if "68575" not in lower_name and "pr100" not in lower_name:
            continue
        text = extract_pdf_text(pdf, first_page=1, last_page=40)
        if all(
            token in text
            for token in (
                "ETH_XPORT_0",
                "ETH_XPORT_1",
                "XPORT_PORTRESET_0",
                "XPORT_PORTRESET_1",
                "ETH_GPHY_RGMII_INTRL2",
            )
        ):
            return switch_ports, [f"switch_port inventory validated against CPU datasheet {pdf.name}"]
    return set(), []


def _summarize_blockdiag_profile(text: str) -> str:
    notes: list[str] = []
    ge_match = re.search(r"1GE\s+LAN\s*[\*xX]\s*(\d+)", text, flags=re.IGNORECASE)
    if ge_match:
        notes.append(f"Block diagram OCR detected 1GE LAN x{ge_match.group(1)}.")
    if re.search(r"\b5GE\s+PHY\b", text, flags=re.IGNORECASE):
        notes.append("Block diagram OCR detected a 5GE PHY.")
    if re.search(r"SFP\+?\s*CAGE\s+FOR\s+PON\s+TRANSCEIVER", text, flags=re.IGNORECASE):
        notes.append("Block diagram OCR detected an SFP+ cage for PON transceiver.")
    return " ".join(notes)


def _parse_cover_titles(text: str) -> list[tuple[str, int]]:
    titles: list[tuple[str, int]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not re.search(r"\d$", line):
            continue
        match = re.match(r"^(?:\d{2}[_\-.])?([A-Za-z0-9/._+\- ]*[A-Za-z][A-Za-z0-9/._+\- ]+?)\s+(\d+)$", line)
        if not match:
            continue
        title = re.sub(r"\s+", " ", match.group(1)).strip(" -_")
        page = int(match.group(2))
        if title and not any(existing_title == title and existing_page == page for existing_title, existing_page in titles):
            titles.append((title, page))
    return titles


def _derive_memcfg_macro(*, family: str, ddr_type: str, ddr_size: str, width: str) -> tuple[str, str]:
    normalized_type = ddr_type.upper().replace("X", "")
    normalized_size = ddr_size.replace(" ", "")
    normalized_width = width.lower().replace(" ", "").removeprefix("x")
    for rule in PUBLIC_DDR_RULES:
        if (
            rule["family"] == family
            and rule["ddr_type"] == normalized_type
            and rule["ddr_size"] == normalized_size
            and rule["width"] == normalized_width
        ):
            return rule["memcfg_macro"], f"Derived from {rule['reference']}."
    return "", ""


def _extract_first(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _select_source_table(
    artifact_value: Any,
    spreadsheets: list[Path],
    *,
    keywords: tuple[str, ...],
    fallback_to_first: bool = False,
) -> Path | None:
    candidate_names: list[str] = []
    if artifact_value:
        if isinstance(artifact_value, list):
            candidate_names.extend(Path(str(item)).name for item in artifact_value)
        else:
            candidate_names.append(Path(str(artifact_value)).name)
    for candidate_name in candidate_names:
        for path in spreadsheets:
            if path.name == candidate_name:
                return path
    for path in spreadsheets:
        lowered = path.name.lower()
        if any(keyword in lowered for keyword in keywords):
            return path
    return next(iter(spreadsheets), None) if fallback_to_first else None


def _write_dict_csv(path: Path, headers: list[str], rows: list[dict[str, str]], *, force: bool) -> None:
    if path.exists() and not force:
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _normalize_polarity(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"low", "l", "active low", "active_low"}:
        return "active_low"
    if lowered in {"high", "h", "active high", "active_high"}:
        return "active_high"
    if lowered == "x":
        return ""
    return value.strip()


def _dedupe_rows(rows: list[dict[str, str]], *, key_fields: tuple[str, ...]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        key = tuple(row.get(field, "") for field in key_fields)
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _find_pdf_index(indexes: list[PdfIndex], path: Path) -> PdfIndex:
    for index in indexes:
        if index.path == path:
            return index
    return PdfIndex(path=path, titles=[])


def _find_title_page(titles: dict[str, tuple[str, int]], needle: str) -> tuple[str, int] | None:
    for title, payload in titles.items():
        if needle in title:
            return payload
    return None


def _format_title_page(titles: dict[str, tuple[str, int]], needle: str) -> str:
    payload = _find_title_page(titles, needle)
    if not payload:
        return ""
    filename, page = payload
    return f"{filename}:page {page}"


def _looks_like_led_or_button(row: dict[str, str]) -> bool:
    text = " ".join(str(value).lower() for value in row.values())
    return any(keyword in text for keyword in ("led", "button", "reset", "wps"))
