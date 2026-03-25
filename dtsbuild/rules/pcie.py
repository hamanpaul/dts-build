"""PCIe + WiFi regulator subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &pcie0..2)
  - PCIe slots enabled with ``status = "okay"``
  - Per-slot regulator GPIO/polarity/share relationships are compiler-level
    decisions and are not guessed by this rule library.
"""
from __future__ import annotations

from dtsbuild.pcie_utils import infer_pcie_instances
from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts PCIe/WiFi regulator section)"


class PcieRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "pcie"

    @property
    def description(self) -> str:
        return "PCIe slots and WiFi power regulators"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "signal with role containing PCIE or WIFI_PWR",
            "GPIO for WiFi power regulator (if present)",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        pcie_sigs = self._signals_by_role(signals, "PCIE")
        wifi_sigs = self._signals_by_role(signals, "WIFI")
        return bool(pcie_sigs or wifi_sigs)

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        pcie_sigs = self._signals_by_role(signals, "PCIE")
        wifi_sigs = self._signals_by_role(signals, "WIFI")

        if not pcie_sigs and not wifi_sigs:
            return None

        # Detect PCIe instances
        instances = infer_pcie_instances(sig.name for sig in signals)

        if not instances:
            return None

        notes: list[str] = []
        primary = sorted(instances)[0]
        notes.append(f"PCIe instances: {sorted(instances)}")
        if wifi_sigs:
            notes.append(
                "Per-slot regulator GPIO/polarity/share mapping requires separate compiler-level proof."
            )

        return RuleMatch(
            subsystem="pcie",
            node_name=f"&pcie{primary}",
            properties={"status": '"okay"'},
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
