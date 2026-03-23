"""I2C bus + devices subsystem rule.

Pattern source: BCM68575 BDK public reference (968575REF1.dts &i2c0, &i2c1)
  - ``&i2c0 { pinctrl-0 = <...>; status = "okay"; }``
  - Child device nodes with ``compatible``, ``reg``, ``#gpio-cells``,
    ``gpio-controller`` for GPIO expanders (PCA9555, PCA9557).
"""
from __future__ import annotations

from dtsbuild.schema import Signal, Device, DtsHint
from .base import SubsystemRule, RuleMatch

_SOURCE = "BCM68575 BDK public reference (968575REF1.dts &i2c0/i2c1 nodes)"


class I2cRule(SubsystemRule):

    @property
    def subsystem_name(self) -> str:
        return "i2c"

    @property
    def description(self) -> str:
        return "I2C bus with attached devices"

    @property
    def required_evidence(self) -> list[str]:
        return [
            "signal with role containing I2C",
            "device with bus i2c and compatible string",
        ]

    def match(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> bool:
        has_i2c_sig = bool(self._signals_by_role(signals, "I2C"))
        has_i2c_dev = any(d.bus and "i2c" in d.bus.lower() for d in devices)
        return has_i2c_sig or has_i2c_dev

    def apply(self, signals: list[Signal], devices: list[Device],
              hints: list[DtsHint]) -> RuleMatch | None:
        i2c_devs = [d for d in devices if d.bus and "i2c" in d.bus.lower()]
        i2c_sigs = self._signals_by_role(signals, "I2C")

        if not i2c_devs and not i2c_sigs:
            return None

        # Group devices by bus instance
        bus_groups: dict[str, list[Device]] = {}
        for dev in i2c_devs:
            bus = dev.bus or "i2c0"
            bus_groups.setdefault(bus, []).append(dev)

        # If we only have signals, derive bus from signal names
        if not bus_groups and i2c_sigs:
            bus_groups["i2c0"] = []

        # Use the first bus for the primary RuleMatch
        primary_bus = sorted(bus_groups.keys())[0]
        primary_devs = bus_groups[primary_bus]

        children: list[dict] = []
        notes: list[str] = []

        for dev in primary_devs:
            if not dev.compatible or not dev.address:
                notes.append(f"Device {dev.refdes} missing compatible or address")
                continue

            child_props: dict = {
                "compatible": f'"{dev.compatible}"',
                "reg": f"<{dev.address}>",
            }

            # GPIO expanders get extra properties
            compat_lower = dev.compatible.lower()
            if "pca955" in compat_lower or "pca953" in compat_lower:
                child_props["#gpio-cells"] = "<2>"
                child_props["gpio-controller"] = None  # boolean
                child_props["polarity"] = "<0x00>"

            node_label = dev.refdes.lower().replace("-", "_")
            child_name = f"{node_label}: gpio@{dev.address.replace('0x', '')}"
            children.append({
                "node_name": child_name,
                "properties": child_props,
            })

        # Note additional buses
        for bus in sorted(bus_groups.keys()):
            if bus != primary_bus:
                notes.append(f"Additional bus {bus} with {len(bus_groups[bus])} device(s)")

        return RuleMatch(
            subsystem="i2c",
            node_name=f"&{primary_bus}",
            properties={
                "pinctrl-names": '"default"',
                "status": '"okay"',
            },
            children=children,
            source=_SOURCE,
            confidence=1.0,
            notes=notes,
        )
