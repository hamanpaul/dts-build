"""Ethernet PHY subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &ethphytop)
  - ``&ethphytop { xphy0-enabled; ... status = "okay"; }``
  - ``enet-phy-lane-swap;`` boolean property when lane swap is detected
  - ``wakeup-trigger-pin-gpio`` for Wake-on-LAN
"""
from __future__ import annotations

import re

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &ethphytop node)"


class EthernetRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "ethernet"

    @property
    def description(self) -> str:
        return "Ethernet PHY topology (lane swap, Wake-on-LAN)"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "signal with role containing ETHERNET_PHY",
            "swap_detected field on signal (for lane swap)",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        return bool(self._signals_by_role(signals, "ETHERNET_PHY"))

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        eth_sigs = self._signals_by_role(signals, "ETHERNET_PHY")
        if not eth_sigs:
            return None

        properties: dict = {"status": '"okay"'}
        notes: list[str] = []

        # Enable xphy ports
        enabled_phys: set[int] = set()
        for sig in eth_sigs:
            m = re.search(r"(\d+)", sig.name)
            if m:
                enabled_phys.add(int(m.group(1)))

        for idx in sorted(enabled_phys):
            properties[f"xphy{idx}-enabled"] = None  # boolean

        # Lane swap detection
        swap_detected = False
        for sig in eth_sigs:
            if sig.swap_detected:
                swap_detected = True
                detail = sig.swap_detail or sig.name
                notes.append(f"Lane swap detected: {detail}")
                break

        if swap_detected:
            properties["enet-phy-lane-swap"] = None  # boolean

        # DTS hints (e.g. from traced evidence)
        ethphy_hints = [h for h in hints if h.target in ("ethphytop", "&ethphytop")]
        for hint in ethphy_hints:
            if hint.property:
                if hint.value:
                    properties[hint.property] = hint.value
                else:
                    properties[hint.property] = None

        return RuleMatch(
            subsystem="ethernet",
            node_name="&ethphytop",
            properties=properties,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
