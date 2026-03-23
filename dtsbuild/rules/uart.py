"""UART subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts)
  - ``&uart0 { status = "okay"; };``
"""
from __future__ import annotations

import re

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &uart0 node)"


class UartRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "uart"

    @property
    def description(self) -> str:
        return "Debug / console UART"

    @property
    def required_evidence(self) -> list[str]:
        return ["signal with role containing UART"]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        return bool(self._signals_by_role(signals, "UART"))

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        uart_sigs = self._signals_by_role(signals, "UART")
        if not uart_sigs:
            return None

        instances: set[str] = set()
        for sig in uart_sigs:
            m = re.search(r"uart(\d+)", sig.name, re.IGNORECASE)
            instances.add(m.group(1) if m else "0")

        # Primary instance (typically uart0 for console)
        primary = sorted(instances)[0]

        return RuleMatch(
            subsystem="uart",
            node_name=f"&uart{primary}",
            properties={"status": '"okay"'},
            source=_SOURCE,
            confidence=1.0,
            notes=[f"UART instances detected: {sorted(instances)}"],
        )
