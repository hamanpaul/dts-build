"""LED controller subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &led_ctrl)
  - ``&led_ctrl`` node with pinctrl for serial LED data/clk/mask pins
  - ``serial-shifters-installed = <N>`` for 74HC595 shift registers
  - Child LED nodes require explicit controller/datasheet/circuit-derived mapping
    and are not guessed from signal ordering alone.
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &led_ctrl node)"


class LedRule(SubsystemRule):
    @staticmethod
    def _endpoint_signals(signals: list[Signal]) -> list[Signal]:
        endpoints: list[Signal] = []
        for signal in signals:
            role_upper = signal.role.upper()
            if role_upper == "LED" or (
                role_upper.startswith("LED_") and role_upper != "LED_CONTROL"
            ):
                endpoints.append(signal)
        return endpoints

    @property
    def subsystem_name(self) -> str:
        return "led"

    @property
    def description(self) -> str:
        return "LED controller (serial shift register + GPIO LEDs)"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "signal with role containing LED or SER_LED",
            "device with 74HC595 for serial-shifters-installed count",
            "explicit LED mapping evidence for child nodes (if any)",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        has_led_sig = bool(self._endpoint_signals(signals))
        has_led_control = bool(self._signals_by_role(signals, "LED_CONTROL"))
        has_shift_reg = any(
            "595" in d.part_number.upper() for d in devices
        )
        return has_led_sig or has_led_control or has_shift_reg

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        led_sigs = self._endpoint_signals(signals)
        shift_regs = [d for d in devices if "595" in d.part_number.upper()]
        led_control_sigs = self._signals_by_role(signals, "LED_CONTROL")

        if not led_sigs and not shift_regs and not led_control_sigs:
            return None

        properties: dict = {
            "pinctrl-names": '"default"',
            "pinctrl-0": "<&c_ser_led_data_pin_55 &c_ser_led_clk_pin_56 &c_ser_led_mask_pin_57>",
            "status": '"okay"',
        }
        notes: list[str] = []

        if shift_regs:
            num = len(shift_regs)
            properties["serial-shifters-installed"] = f"<{num}>"
            notes.append(f"{num} serial shift register(s) detected")

        children: list[dict] = []
        if led_sigs:
            notes.append(
                "Child LED nodes require explicit crossbar/trigger mapping and are not guessed from signal order."
            )

        return RuleMatch(
            subsystem="led",
            node_name="&led_ctrl",
            properties=properties,
            children=children,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
