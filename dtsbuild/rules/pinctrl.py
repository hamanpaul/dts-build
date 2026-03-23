"""Pin controller / pin muxing subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &pincontroller)
  - ``&pincontroller { pincontroller-functions { ... }; }``
  - Each pin function: ``label: label_pinconf { pins = <N>; function = <F>; }``
  - Optional bias: ``bias-pull-down;`` or ``bias-pull-up;``
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &pincontroller node)"


class PinctrlRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "pinctrl"

    @property
    def description(self) -> str:
        return "Pin controller and pin muxing configuration"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "DTS hint targeting pincontroller",
            "signal with role containing PINMUX or PINCTRL",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        pin_hints = [
            h for h in hints
            if "pincontroller" in h.target.lower() or "pinctrl" in h.target.lower()
        ]
        pin_sigs = self._signals_by_role(signals, "PINMUX")
        return bool(pin_hints or pin_sigs)

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        pin_hints = [
            h for h in hints
            if "pincontroller" in h.target.lower() or "pinctrl" in h.target.lower()
        ]
        pin_sigs = self._signals_by_role(signals, "PINMUX")

        if not pin_hints and not pin_sigs:
            return None

        children: list[dict] = []
        notes: list[str] = []

        for sig in pin_sigs:
            gpio = self._extract_gpio_num(sig.soc_pin)
            if gpio is None:
                notes.append(f"Could not extract pin number from {sig.soc_pin}")
                continue

            pin_name = sig.name.lower().replace(" ", "_")
            children.append({
                "node_name": f"{pin_name}_pin_{gpio}: {pin_name}_pin_{gpio}_pinconf",
                "properties": {
                    "pins": f"<{gpio}>",
                    "function": "<4>",
                },
            })

        for hint in pin_hints:
            if hint.property and hint.value:
                notes.append(f"Hint: {hint.property} = {hint.value} ({hint.reason})")

        return RuleMatch(
            subsystem="pinctrl",
            node_name="&pincontroller",
            properties={},
            children=[{
                "node_name": "pincontroller-functions",
                "properties": {},
                "children": children,
            }] if children else [],
            source=_SOURCE,
            confidence=0.8,
            notes=notes,
        )
