"""Base class for subsystem DTS rules."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from dtsbuild.schema import Signal, Device, DtsHint


@dataclass
class RuleMatch:
    """Result of applying a rule to schema data."""

    subsystem: str
    node_name: str
    properties: dict[str, Any]
    children: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


class SubsystemRule(ABC):
    """Abstract rule for a DTS subsystem."""

    @property
    @abstractmethod
    def subsystem_name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        """Return True if this rule applies to the given schema data."""
        ...

    @abstractmethod
    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        """Generate DTS properties from schema data."""
        ...

    @property
    def required_evidence(self) -> list[str]:
        """List of evidence types needed for this rule."""
        return []

    # ── shared helpers ───────────────────────────────────────────────

    @staticmethod
    def _signals_by_role(signals: list[Signal], role: str) -> list[Signal]:
        """Filter signals whose role contains *role* (case-insensitive)."""
        role_upper = role.upper()
        return [s for s in signals if role_upper in s.role.upper()]

    @staticmethod
    def _extract_gpio_num(soc_pin: str) -> str | None:
        """Extract numeric GPIO id from soc_pin like 'GPIO_12'."""
        m = re.search(r"(\d+)", soc_pin)
        return m.group(1) if m else None
