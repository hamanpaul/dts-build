"""External power control / cpufreq subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts)
  - ``&ext_pwr_ctrl { pwr-ctrl-0-gpios = <&gpioc N GPIO_ACTIVE_HIGH>; }``
  - ``&cpufreq { op-mode = "dvfs"; }``
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &ext_pwr_ctrl / &cpufreq)"


class PowerRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "power"

    @property
    def description(self) -> str:
        return "External power control GPIOs and CPU frequency scaling"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "signal with role containing POWER or PS_EN or PWR_CTRL",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        pwr_sigs = (
            self._signals_by_role(signals, "POWER")
            + self._signals_by_role(signals, "PS_EN")
            + self._signals_by_role(signals, "PWR_CTRL")
        )
        return bool(pwr_sigs)

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        pwr_sigs = (
            self._signals_by_role(signals, "POWER")
            + self._signals_by_role(signals, "PS_EN")
            + self._signals_by_role(signals, "PWR_CTRL")
        )
        if not pwr_sigs:
            return None

        properties: dict = {}
        notes: list[str] = []

        for i, sig in enumerate(pwr_sigs):
            gpio = self._extract_gpio_num(sig.soc_pin)
            if gpio is None:
                notes.append(f"Could not extract GPIO from {sig.soc_pin}")
                continue

            # Determine polarity from role or name
            if "PHY" in sig.role.upper() or "PHY" in sig.name.upper():
                prop_name = "phy-pwr-ctrl-gpios"
            else:
                prop_name = f"pwr-ctrl-{i}-gpios"

            polarity = "GPIO_ACTIVE_HIGH"
            properties[prop_name] = f"<&gpioc {gpio} {polarity}>"

        if not properties:
            return None

        return RuleMatch(
            subsystem="power",
            node_name="&ext_pwr_ctrl",
            properties=properties,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
