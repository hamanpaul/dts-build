"""PCIe + WiFi regulator subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &pcie0..2)
  - PCIe slots enabled with ``status = "okay"``
  - WiFi power regulators via GPIO:
    PCIE0_REG_GPIO, PCIE1_REG_GPIO etc.
  - Polarity: GPIO_ACTIVE_LOW for power-disable signals
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
        children: list[dict] = []

        # WiFi regulator GPIOs
        for sig in wifi_sigs:
            gpio = self._extract_gpio_num(sig.soc_pin)
            if gpio is None:
                notes.append(f"Could not extract GPIO from WiFi signal {sig.name}")
                continue
            polarity = "GPIO_ACTIVE_LOW" if "DIS" in sig.name.upper() else "GPIO_ACTIVE_HIGH"
            children.append({
                "node_name": f"wifi_reg_{sig.name.lower()}",
                "properties": {
                    "gpio": f"<&gpioc {gpio} {polarity}>",
                },
            })

        primary = sorted(instances)[0]
        notes.append(f"PCIe instances: {sorted(instances)}")

        return RuleMatch(
            subsystem="pcie",
            node_name=f"&pcie{primary}",
            properties={"status": '"okay"'},
            children=children,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
