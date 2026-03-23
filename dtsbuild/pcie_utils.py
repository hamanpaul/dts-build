"""Helpers for evidence-backed PCIe/Wi-Fi signal handling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re

_GROUPED_WIFI_PWR_SIGNALS = frozenset({
    "PCIE02_WIFI_PWR_DIS",
    "PCIE13_WIFI_PWR_DIS",
})
_TRI_BAND_PREFIXES = frozenset({"2G", "5G", "6G"})


def normalize_signal_name(name: str) -> str:
    """Return a stable uppercase token for loose GPIO/signal labels."""
    return re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")


def is_grouped_pcie_wifi_power_signal(name: str) -> bool:
    """True for BGW720 grouped Wi-Fi power-disable GPIO labels."""
    return normalize_signal_name(name) in _GROUPED_WIFI_PWR_SIGNALS


def is_pcie_wifi_aux_signal(name: str) -> bool:
    """True for auxiliary PCIe/Wi-Fi control nets such as RF_DISABLE/PEWAKE."""
    normalized = normalize_signal_name(name)
    return "RF_DISABLE" in normalized or "PEWAKE" in normalized


def is_pcie_wifi_signal_name(name: str) -> bool:
    """Return True when *name* is a PCIe/Wi-Fi control signal label."""
    normalized = normalize_signal_name(name)
    if not normalized:
        return False
    if is_grouped_pcie_wifi_power_signal(normalized):
        return True
    if is_pcie_wifi_aux_signal(normalized):
        return True
    return bool(re.match(r"^PCIE\d+(?:_|$)", normalized))


def gpio_row_signal_name(row: Mapping[str, str]) -> str | None:
    """Return the usable signal name carried by a GPIO row, if any.

    Most rows use the ``signal`` column directly. BGW720 PCIe/Wi-Fi power-disable
    rows store the meaningful label in ``name`` while ``signal`` is ``NA``.
    """
    signal = (row.get("signal") or "").strip()
    name = (row.get("name") or "").strip()

    if signal and normalize_signal_name(signal) != "NA":
        return signal
    if name and normalize_signal_name(name) != "NOT_USED" and is_grouped_pcie_wifi_power_signal(name):
        return name
    return None


def infer_pcie_instances(signal_names: Iterable[str]) -> set[int]:
    """Infer DTS PCIe host instances from verified signal names.

    Safety rules:
      * Explicit ``PCIE0``/``PCIE1``/``PCIE2`` signal names map directly.
      * Grouped BGW720 labels ``PCIE02_WiFi_PWR_DIS`` + ``PCIE13_WiFi_PWR_DIS``
        are *not* treated as instance numbers. They only unlock ``&pcie0..2``
        after independent tri-band Wi-Fi control evidence (2G/5G/6G RF/PEWAKE)
        confirms all three PCIe Wi-Fi endpoints are populated.
    """
    normalized_names = {
        normalized
        for name in signal_names
        if (normalized := normalize_signal_name(name))
    }

    instances: set[int] = set()
    for name in normalized_names:
        match = re.match(r"^PCIE([0-2])(?:_|$)", name)
        if match:
            instances.add(int(match.group(1)))

    if _GROUPED_WIFI_PWR_SIGNALS.issubset(normalized_names):
        tri_band_present = {
            prefix
            for prefix in _TRI_BAND_PREFIXES
            if any(name.startswith(f"{prefix}_") and "RF_DISABLE" in name for name in normalized_names)
            and any(name.startswith(f"{prefix}_") and "PEWAKE" in name for name in normalized_names)
        }
        if tri_band_present == _TRI_BAND_PREFIXES:
            instances.update({0, 1, 2})

    return instances
