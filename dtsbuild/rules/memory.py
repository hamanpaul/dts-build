"""LPDDR4 memory configuration subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts)
  - ``memory_controller { memcfg = <(BP1_DDR_... | ...)>; }``
  - Bitfield-OR macro combining DDR type, speed, width, size, SSC config.
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts memory_controller node)"


class MemoryRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "LPDDR4 memory controller configuration"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "DTS hint for memory_controller or memcfg",
            "DDR type, speed, width, size from BOM/schematic",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        mem_hints = [
            h for h in hints
            if "memory" in h.target.lower() or "memcfg" in (h.property or "").lower()
        ]
        mem_devices = [
            d for d in devices
            if "DDR" in d.part_number.upper() or "LPDDR" in d.part_number.upper()
        ]
        return bool(mem_hints or mem_devices)

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        mem_hints = [
            h for h in hints
            if "memory" in h.target.lower() or "memcfg" in (h.property or "").lower()
        ]

        if not mem_hints:
            return None

        # Use the first hint that has a value
        for hint in mem_hints:
            if hint.value:
                return RuleMatch(
                    subsystem="memory",
                    node_name="memory_controller",
                    properties={"memcfg": hint.value},
                    source=_SOURCE,
                    confidence=0.9,
                    notes=[f"memcfg from hint: {hint.reason}"],
                )

        return None
