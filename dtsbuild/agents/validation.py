"""Post-generation validation: compare schema vs DTS output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dtsbuild.schema import HardwareSchema, Signal, Device
from dtsbuild.schema_io import load_schema
from .issue_register import build_device_issue_item, build_signal_issue_item


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning" | "info"
    category: str  # e.g. "missing_node", "unresolved_signal", "syntax", "todo_remaining"
    message: str
    signal_name: str | None = None
    device_name: str | None = None
    dts_relevant: bool | None = None
    bucket: str | None = None


@dataclass
class ValidationReport:
    dts_path: Path
    schema_path: Path
    total_signals: int = 0
    verified_signals: int = 0
    incomplete_signals: int = 0
    total_devices: int = 0
    verified_devices: int = 0
    coverage_pct: float = 0.0
    todo_count: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Report passes when there are no error-level issues."""
        return not any(i.severity == "error" for i in self.issues)

    def summary_text(self) -> str:
        """Human-readable summary."""
        status = "PASS" if self.passed else "FAIL"
        summary = self.build_summary()
        lines = [
            f"Validation: {status}",
            f"  Signals : {self.verified_signals}/{self.total_signals} verified in DTS"
            f" ({self.incomplete_signals} incomplete)",
            f"  Devices : {self.verified_devices}/{self.total_devices} verified in DTS",
            f"  Coverage: {self.coverage_pct:.1f}%",
            f"  TODOs   : {self.todo_count}",
            f"  Issues  : {len(self.issues)}"
            f" ({sum(1 for i in self.issues if i.severity == 'error')} errors,"
            f" {sum(1 for i in self.issues if i.severity == 'warning')} warnings,"
            f" {sum(1 for i in self.issues if i.severity == 'info')} info)",
            "  Focus   : "
            f"{summary['unresolved']['actionable']} actionable unresolved, "
            f"{summary['unresolved']['informational']} informational unresolved",
        ]
        return "\n".join(lines)

    def build_summary(self) -> dict[str, Any]:
        """Structured summary that separates actionable vs informational unresolved items."""
        by_severity = {"error": 0, "warning": 0, "info": 0}
        by_category: dict[str, int] = {}
        unresolved = {
            "actionable": 0,
            "informational": 0,
            "by_kind": {
                "signal": {"actionable": 0, "informational": 0},
                "device": {"actionable": 0, "informational": 0},
            },
        }

        for issue in self.issues:
            by_severity.setdefault(issue.severity, 0)
            by_severity[issue.severity] += 1
            by_category[issue.category] = by_category.get(issue.category, 0) + 1

            if issue.category not in {
                "unresolved_signal",
                "unresolved_device",
                "informational_unresolved_signal",
                "informational_unresolved_device",
            }:
                continue

            kind = "signal" if issue.signal_name else "device"
            focus = "actionable" if issue.dts_relevant else "informational"
            unresolved[focus] += 1
            unresolved["by_kind"][kind][focus] += 1

        return {
            "total_issues": len(self.issues),
            "by_severity": by_severity,
            "by_category": by_category,
            "unresolved": unresolved,
        }

    def to_dict(self) -> dict:
        """JSON-serializable dict."""
        return {
            "dts_path": str(self.dts_path),
            "schema_path": str(self.schema_path),
            "total_signals": self.total_signals,
            "verified_signals": self.verified_signals,
            "incomplete_signals": self.incomplete_signals,
            "total_devices": self.total_devices,
            "verified_devices": self.verified_devices,
            "coverage_pct": self.coverage_pct,
            "todo_count": self.todo_count,
            "passed": self.passed,
            "summary": self.build_summary(),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "signal_name": i.signal_name,
                    "device_name": i.device_name,
                    "dts_relevant": i.dts_relevant,
                    "bucket": i.bucket,
                }
                for i in self.issues
            ],
        }


# ── Helpers ──────────────────────────────────────────────────────────

_TODO_BLOCK_RE = re.compile(r"/\*.*?TODO.*?\*/", re.DOTALL)
_TODO_LINE_RE = re.compile(r"//.*TODO")


def _signal_in_dts(sig: Signal, dts_lower: str) -> bool:
    """Check whether a signal is represented in the DTS text."""
    name_lower = sig.name.lower()
    role_lower = sig.role.lower()

    if role_lower == "led_control" and "&led_ctrl" in dts_lower:
        return True
    if role_lower == "spi" and "&hsspi" in dts_lower:
        return True

    if name_lower in dts_lower:
        return True
    if role_lower in dts_lower:
        return True

    # Check individual role tokens (e.g. "DEBUG_UART_TX" → "uart", "debug")
    role_tokens = set(role_lower.replace("_", " ").split())
    for token in role_tokens:
        if len(token) >= 4 and token in dts_lower:
            return True

    return False


def _device_in_dts(dev: Device, dts_lower: str) -> bool:
    """Check whether a device is represented in the DTS text."""
    if dev.refdes.lower() in dts_lower:
        return True
    if dev.compatible and dev.compatible.lower() in dts_lower:
        return True
    if dev.part_number.lower() in dts_lower:
        return True
    return False


# ── Main validation function ─────────────────────────────────────────

def validate_dts_against_schema(
    dts_path: Path,
    schema_path: Path,
) -> ValidationReport:
    """Compare generated DTS against the hardware schema.

    Checks:
    1. Every VERIFIED signal should appear in DTS
    2. Every VERIFIED device should appear in DTS
    3. Count TODO comments (unresolved items)
    4. Syntax check (balanced braces, /dts-v1/; header)
    5. Warn about INCOMPLETE/AMBIGUOUS signals not resolved
    6. Coverage percentage = verified items found in DTS / total verified items
    """
    dts_path = Path(dts_path)
    schema_path = Path(schema_path)

    schema = load_schema(schema_path)
    dts_text = dts_path.read_text(encoding="utf-8")
    dts_lower = dts_text.lower()

    issues: list[ValidationIssue] = []

    # ── 1. Signal presence ───────────────────────────────────────
    total_signals = len(schema.signals)
    signals_found = 0
    incomplete_signals = 0

    for sig in schema.signals:
        if sig.status == "VERIFIED":
            if _signal_in_dts(sig, dts_lower):
                signals_found += 1
            else:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="missing_node",
                    message=f"Verified signal '{sig.name}' (role={sig.role}) not found in DTS",
                    signal_name=sig.name,
                ))
        else:
            incomplete_signals += 1
            issue_item = build_signal_issue_item(sig)
            actionable = issue_item.dts_relevant
            issues.append(ValidationIssue(
                severity="warning" if actionable else "info",
                category=(
                    "unresolved_signal"
                    if actionable
                    else "informational_unresolved_signal"
                ),
                message=(
                    f"Signal '{sig.name}' is {sig.status} — DTS-relevant trace still unresolved"
                    if actionable
                    else (
                        f"Signal '{sig.name}' is {sig.status}, but it is classified as "
                        f"non-runtime DTS noise ({issue_item.bucket})."
                    )
                ),
                signal_name=sig.name,
                dts_relevant=issue_item.dts_relevant,
                bucket=issue_item.bucket,
            ))

    # ── 2. Device presence ───────────────────────────────────────
    total_devices = len(schema.devices)
    devices_found = 0

    for dev in schema.devices:
        if dev.status == "VERIFIED":
            if _device_in_dts(dev, dts_lower):
                devices_found += 1
            else:
                issues.append(ValidationIssue(
                    severity="warning",
                    category="missing_node",
                    message=(
                        f"Verified device '{dev.refdes}' "
                        f"(compatible={dev.compatible}) not found in DTS"
                    ),
                    device_name=dev.refdes,
                ))
        else:
            issue_item = build_device_issue_item(dev)
            actionable = issue_item.dts_relevant
            issues.append(ValidationIssue(
                severity="warning" if actionable else "info",
                category=(
                    "unresolved_device"
                    if actionable
                    else "informational_unresolved_device"
                ),
                message=(
                    f"Device '{dev.refdes}' is {dev.status} — DTS-relevant metadata still unresolved"
                    if actionable
                    else (
                        f"Device '{dev.refdes}' is {dev.status}, but it is classified as "
                        f"non-runtime DTS noise ({issue_item.bucket})."
                    )
                ),
                device_name=dev.refdes,
                dts_relevant=issue_item.dts_relevant,
                bucket=issue_item.bucket,
            ))

    # ── 3. TODO comments ─────────────────────────────────────────
    todo_count = len(_TODO_BLOCK_RE.findall(dts_text)) + len(_TODO_LINE_RE.findall(dts_text))
    if todo_count > 0:
        issues.append(ValidationIssue(
            severity="info",
            category="todo_remaining",
            message=f"{todo_count} TODO comment(s) remain in DTS output",
        ))

    # ── 4. Syntax checks ────────────────────────────────────────
    stripped = dts_text.lstrip()
    if not stripped.startswith("/dts-v1/;") and not stripped.startswith("#include"):
        issues.append(ValidationIssue(
            severity="error",
            category="syntax",
            message="Missing /dts-v1/; header",
        ))

    open_braces = dts_text.count("{")
    close_braces = dts_text.count("}")
    if open_braces != close_braces:
        issues.append(ValidationIssue(
            severity="error",
            category="syntax",
            message=f"Unbalanced braces: {open_braces} opening vs {close_braces} closing",
        ))

    # ── 5 & 6. Coverage ─────────────────────────────────────────
    total_verified = (
        sum(1 for s in schema.signals if s.status == "VERIFIED")
        + sum(1 for d in schema.devices if d.status == "VERIFIED")
    )
    items_in_dts = signals_found + devices_found
    coverage_pct = (
        round(items_in_dts / total_verified * 100.0, 1)
        if total_verified > 0
        else 100.0
    )

    return ValidationReport(
        dts_path=dts_path,
        schema_path=schema_path,
        total_signals=total_signals,
        verified_signals=signals_found,
        incomplete_signals=incomplete_signals,
        total_devices=total_devices,
        verified_devices=devices_found,
        coverage_pct=coverage_pct,
        todo_count=todo_count,
        issues=issues,
    )
