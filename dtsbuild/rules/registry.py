"""Rule registry — lookup by subsystem name."""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch
from .buttons import ButtonRule
from .uart import UartRule
from .led import LedRule
from .i2c import I2cRule
from .usb import UsbRule
from .pcie import PcieRule
from .serdes import SerdesRule
from .ethernet import EthernetRule
from .power import PowerRule
from .memory import MemoryRule
from .pinctrl import PinctrlRule

_ALL_RULES: list[SubsystemRule] = [
    ButtonRule(),
    UartRule(),
    LedRule(),
    I2cRule(),
    UsbRule(),
    PcieRule(),
    SerdesRule(),
    EthernetRule(),
    PowerRule(),
    MemoryRule(),
    PinctrlRule(),
]


def get_all_rules() -> list[SubsystemRule]:
    """Return all registered rules."""
    return list(_ALL_RULES)


def get_rule(subsystem: str) -> SubsystemRule | None:
    """Look up a rule by subsystem name."""
    for r in _ALL_RULES:
        if r.subsystem_name == subsystem:
            return r
    return None


def auto_match(
    signals: list[Signal],
    devices: list[Device],
    hints: list[DtsHint],
) -> list[SubsystemRule]:
    """Return all rules that match the given schema data."""
    return [r for r in _ALL_RULES if r.match(signals, devices, hints)]
