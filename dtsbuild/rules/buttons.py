"""Button / reset subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts)
  - ``buttons`` node uses ``compatible = "brcm,buttons"``
  - Children: ``reset_button``, ``ses_button``
  - Each child has ext_irq-gpio, interrupt-parent, interrupts,
    and press/hold/release sub-nodes.
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts buttons node)"


class ButtonRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "buttons"

    @property
    def description(self) -> str:
        return "GPIO-based buttons (reset, SES/WPS)"

    @property
    def required_evidence(self) -> list[str]:
        return ["signal with role RESET_BUTTON or SES_BUTTON", "GPIO number"]

    # ── matching ─────────────────────────────────────────────────────

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        return bool(
            self._signals_by_role(signals, "RESET_BUTTON")
            or self._signals_by_role(signals, "SES_BUTTON")
            or self._signals_by_role(signals, "BUTTON")
        )

    # ── apply ────────────────────────────────────────────────────────

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        reset_sigs = self._signals_by_role(signals, "RESET_BUTTON")
        ses_sigs = self._signals_by_role(signals, "SES_BUTTON")

        if not reset_sigs and not ses_sigs:
            # Fallback: generic BUTTON role
            generic = self._signals_by_role(signals, "BUTTON")
            if not generic:
                return None
            reset_sigs = [s for s in generic if "reset" in s.name.lower()]
            ses_sigs = [s for s in generic if "ses" in s.name.lower() or "wps" in s.name.lower()]
            if not reset_sigs and not ses_sigs:
                reset_sigs = generic  # treat unknown buttons as reset

        children: list[dict] = []
        notes: list[str] = []

        for sig in reset_sigs:
            gpio = self._extract_gpio_num(sig.soc_pin)
            if gpio is None:
                notes.append(f"Could not extract GPIO from {sig.soc_pin}")
                continue
            active = "GPIO_ACTIVE_LOW"
            children.append({
                "node_name": "reset_button",
                "properties": {
                    "ext_irq-gpio": f"<&gpioc {gpio} {active}>",
                    "interrupt-parent": "<&gpioc>",
                    "interrupts": f"<{gpio} IRQ_TYPE_EDGE_FALLING>",
                },
                "children": [
                    {
                        "node_name": "press",
                        "properties": {
                            "print": '"Button Press -- Hold for 5s to do restore to default"',
                        },
                    },
                    {
                        "node_name": "hold",
                        "properties": {"rst_to_dflt": "<5>"},
                    },
                    {
                        "node_name": "release",
                        "properties": {"reset": "<0>"},
                    },
                ],
            })

        for sig in ses_sigs:
            gpio = self._extract_gpio_num(sig.soc_pin)
            if gpio is None:
                notes.append(f"Could not extract GPIO from {sig.soc_pin}")
                continue
            active = "GPIO_ACTIVE_LOW"
            children.append({
                "node_name": "ses_button",
                "properties": {
                    "ext_irq-gpio": f"<&gpioc {gpio} {active}>",
                    "interrupt-parent": "<&gpioc>",
                    "interrupts": f"<{gpio} IRQ_TYPE_EDGE_FALLING>",
                },
                "children": [
                    {
                        "node_name": "press",
                        "properties": {
                            "print": '"Session Button pressed"',
                        },
                    },
                    {
                        "node_name": "release",
                        "properties": {
                            "ses_short_period": "<0>",
                            "ses_long_period": "<3>",
                        },
                    },
                ],
            })

        if not children:
            return None

        return RuleMatch(
            subsystem="buttons",
            node_name="buttons",
            properties={"compatible": '"brcm,buttons"'},
            children=children,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
