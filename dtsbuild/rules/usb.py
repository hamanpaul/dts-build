"""USB controller subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &usb_ctrl, &usb0_xhci)
  - ``&usb_ctrl { xhci-enable; status = "okay"; }``
  - ``&usb0_xhci { status = "okay"; }`` with port child nodes
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &usb_ctrl node)"


class UsbRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "usb"

    @property
    def description(self) -> str:
        return "USB host controller (xHCI)"

    @property
    def required_evidence(self) -> list[str]:
        return ["signal with role containing USB"]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        return bool(self._signals_by_role(signals, "USB"))

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        usb_sigs = self._signals_by_role(signals, "USB")
        if not usb_sigs:
            return None

        properties: dict = {
            "pinctrl-names": '"default"',
            "pinctrl-0": "<&usb0_pwr_pins &usb1_pwr_pins>",
            "xhci-enable": None,  # boolean
            "status": '"okay"',
        }

        # Check for disabled ports
        notes: list[str] = []
        disabled = [s for s in usb_sigs if "DISABLE" in s.role.upper()]
        if disabled:
            for s in disabled:
                port_num = self._extract_gpio_num(s.name)
                if port_num:
                    properties[f"port{port_num}-disabled"] = None
                    notes.append(f"USB port {port_num} disabled per schema")

        return RuleMatch(
            subsystem="usb",
            node_name="&usb_ctrl",
            properties=properties,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
