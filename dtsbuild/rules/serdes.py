"""WAN SerDes (PON / Active Ethernet) subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &wan_serdes)
  - ``&wan_serdes { status = "okay"; }``
  - Child ``serdes0`` with pon-led, alarm-led, trx references
  - Child ``serdes1`` with trx reference (LAN SFP)
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &wan_serdes node)"


class SerdesRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "serdes"

    @property
    def description(self) -> str:
        return "WAN SerDes for PON / Active Ethernet"

    @property
    def required_evidence(self) -> list[str]:
        return ["signal with role containing SERDES or WAN or PON"]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        serdes_sigs = self._signals_by_role(signals, "SERDES")
        wan_sigs = self._signals_by_role(signals, "WAN")
        pon_sigs = self._signals_by_role(signals, "PON")
        return bool(serdes_sigs or wan_sigs or pon_sigs)

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        serdes_sigs = self._signals_by_role(signals, "SERDES")
        wan_sigs = self._signals_by_role(signals, "WAN")
        pon_sigs = self._signals_by_role(signals, "PON")

        all_sigs = serdes_sigs + wan_sigs + pon_sigs
        if not all_sigs:
            return None

        children: list[dict] = []
        notes: list[str] = []

        # serdes0 is typically the WAN/PON port
        if wan_sigs or pon_sigs:
            children.append({
                "node_name": "serdes0",
                "properties": {
                    "trx": "<&wan_sfp>",
                },
            })
            notes.append("serdes0 → WAN SFP")

        return RuleMatch(
            subsystem="serdes",
            node_name="&wan_serdes",
            properties={"status": '"okay"'},
            children=children,
            source=_SOURCE,
            confidence=0.9,
            notes=notes,
        )
