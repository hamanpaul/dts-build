from __future__ import annotations

import csv
from pathlib import Path

from .manifest import FAMILY_DEFAULTS, write_manifest


CSV_TEMPLATES: dict[str, tuple[list[str], list[list[str]]]] = {
    "blockdiag.csv": (
        ["domain", "interface", "present", "controller", "endpoint", "page_ref", "notes"],
        [
            ["memory", "lpddr4", "true", "ddr", "", "blockdiag", ""],
            ["network", "wan_sfp", "true", "serdes0", "sfp", "blockdiag", ""],
            ["pcie_wifi", "pcie0_wifi", "true", "pcie0", "wifi-module", "blockdiag", ""],
            ["storage", "emmc", "true", "sdhci", "emmc", "blockdiag", ""],
            ["led_button", "service_leds_and_reset", "true", "gpioc", "", "blockdiag", ""],
        ],
    ),
    "ddr.csv": (
        ["field", "value", "notes"],
        [
            ["memcfg_macro", "", "例如 BP1_DDR_MCBSEL_FORMAT_VER1 | ..."],
            ["ddr_type", "", "LPDDR4 / LPDDR5 / DDR4"],
            ["ddr_size", "", "例如 16Gb"],
        ],
    ),
    "gpio_led.csv": (
        ["category", "name", "signal", "pin_or_gpio", "polarity", "notes"],
        [
            ["gpio", "reset_button", "RESET#", "", "active_low", ""],
            ["led", "wan_led", "WAN", "", "active_low", ""],
        ],
    ),
    "network.csv": (
        ["name", "present", "role", "source", "phy_handle", "phy_mode", "notes"],
        [
            ["port_xgphy0", "true", "LAN", "", "xphy0", "xfi", ""],
            ["port_wan", "true", "WAN", "serdes0", "", "serdes", ""],
        ],
    ),
}


def init_folder(
    root: Path,
    project: str,
    profile: str,
    refboard: str,
    family: str,
    model: str | None = None,
    base_include: str | None = None,
) -> Path:
    folder = root / f"dtsin_{project}"
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f"destination already exists and is not empty: {folder}")

    defaults = FAMILY_DEFAULTS.get(family, {})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tables").mkdir(exist_ok=True)
    (folder / "hw").mkdir(exist_ok=True)
    (folder / "notes").mkdir(exist_ok=True)
    (root / f"dtsout_{project}").mkdir(exist_ok=True)

    manifest_payload = {
        "project": project,
        "family": family,
        "profile": profile,
        "refboard": refboard,
        "model": model or project,
        "output_dts": f"{project}.dts",
        "output_dir": f"dtsout_{project}",
        "base_include": base_include or defaults.get("base_include", ""),
        "compatible": defaults.get("compatible", ""),
        "artifacts": {
            "blockdiag_table": "tables/blockdiag.csv",
            "ddr_table": "tables/ddr.csv",
            "gpio_led_table": "tables/gpio_led.csv",
            "network_table": "tables/network.csv",
            "schematic_pdf": "hw/schematic.pdf",
        },
        "notes": [
            "Keep profile and refboard in manifest.yaml; do not encode them in the folder name.",
            "Optional artifact: copy a public reference DTS into dtsin_<project>/ and point artifacts.public_ref_dts to it.",
        ],
    }
    write_manifest(folder / "manifest.yaml", manifest_payload)

    for filename, (headers, rows) in CSV_TEMPLATES.items():
        _write_csv(folder / "tables" / filename, headers, rows)

    (folder / "hw" / "README.txt").write_text(
        "Put schematic PDFs or other hardware attachments here.\n",
        encoding="utf-8",
    )
    (folder / "notes" / "README.txt").write_text(
        "Put manual notes, DTS diffs, or naming rules here.\n",
        encoding="utf-8",
    )
    return folder


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)
