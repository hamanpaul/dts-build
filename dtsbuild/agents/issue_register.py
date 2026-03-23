"""Build an unresolved issue register from schema + validation artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from dtsbuild.schema import Device, Provenance, Signal
from dtsbuild.schema_io import load_schema

IssueKind = Literal["signal", "device"]
IssueBucket = Literal["trace-gap", "lookup-gap", "exclude-from-dts"]
EvidenceStrength = Literal["strong", "medium", "weak"]

_KNOWN_BUCKETS: tuple[IssueBucket, ...] = (
    "trace-gap",
    "lookup-gap",
    "exclude-from-dts",
)
_KNOWN_KINDS: tuple[IssueKind, ...] = ("signal", "device")
_KNOWN_STRENGTHS: tuple[EvidenceStrength, ...] = ("strong", "medium", "weak")

_TESTPOINT_RE = re.compile(r"^TP\d+[A-Z]?$")
_SPLIT_UNIT_RE = re.compile(r"^U\d+[A-Z]$")
_CONNECTOR_RE = re.compile(r"^J\d+[A-Z]?$")
_T_PREFIX_RE = re.compile(r"^T\d+[A-Z]?$")

_LOW_RUNTIME_SIGNAL_TOKENS = ("STRAP", "BOOT STRAP", "BOOTSTRAP", "INTERNAL")


@dataclass(slots=True)
class IssueRegisterItem:
    kind: IssueKind
    name: str | None
    refdes: str | None
    status: str
    bucket: IssueBucket
    subsystem: str
    role: str
    dts_relevant: bool
    evidence_strength: EvidenceStrength
    reason: str
    provenance_summary: str
    validation_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class IssueRegister:
    project: str
    schema_path: str
    validation_path: str | None
    items: list[IssueRegisterItem] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary:
            self.summary = _build_summary(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "schema_path": self.schema_path,
            "validation_path": self.validation_path,
            "summary": self.summary,
            "items": [item.to_dict() for item in self.items],
        }


def build_issue_register(
    schema_path: Path,
    validation_path: Path | None = None,
) -> IssueRegister:
    """Build an issue register for all schema items whose status is unresolved."""
    schema_path = Path(schema_path)
    validation_path = Path(validation_path) if validation_path else None

    schema = load_schema(schema_path)
    validation_messages = _load_validation_messages(validation_path)

    items_by_key: dict[tuple[IssueKind, str], IssueRegisterItem] = {}
    for signal in schema.signals:
        if signal.status == "VERIFIED":
            continue
        candidate = build_signal_issue_item(
            signal,
            validation_messages["signal"].get(signal.name),
        )
        _store_unique_issue(
            items_by_key,
            ("signal", signal.name),
            candidate,
        )

    for device in schema.devices:
        if device.status == "VERIFIED":
            continue
        candidate = build_device_issue_item(
            device,
            validation_messages["device"].get(device.refdes),
        )
        _store_unique_issue(
            items_by_key,
            ("device", device.refdes),
            candidate,
        )

    return IssueRegister(
        project=schema.project,
        schema_path=str(schema_path),
        validation_path=str(validation_path) if validation_path else None,
        items=list(items_by_key.values()),
    )


def write_issue_register(register: IssueRegister, output_path: Path) -> Path:
    """Write the issue register to JSON and return the destination path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(register.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def build_and_write_issue_register(
    schema_path: Path,
    output_path: Path,
    validation_path: Path | None = None,
) -> IssueRegister:
    """Convenience helper for rebuilding the unresolved issue register artifact."""
    register = build_issue_register(schema_path=schema_path, validation_path=validation_path)
    write_issue_register(register, output_path)
    return register


def build_signal_issue_item(
    signal: Signal,
    validation_message: str | None = None,
) -> IssueRegisterItem:
    role = signal.role or "unknown"
    subsystem = _derive_signal_subsystem(signal.name, role)
    dts_relevant = not _is_low_runtime_signal(signal)
    evidence_strength = _strength_from_confidence(signal.provenance.confidence)

    reason = "Unresolved signal still needs endpoint/path tracing; keep it in trace-gap."
    if not dts_relevant:
        reason = (
            "Unresolved signal looks like strap/internal wiring; keep as trace-gap "
            "but mark low DTS runtime relevance."
        )

    return IssueRegisterItem(
        kind="signal",
        name=signal.name,
        refdes=None,
        status=signal.status,
        bucket="trace-gap",
        subsystem=subsystem,
        role=role,
        dts_relevant=dts_relevant,
        evidence_strength=evidence_strength,
        reason=reason,
        provenance_summary=_summarize_provenance(signal.provenance, validation_message),
        validation_message=validation_message,
    )


def build_device_issue_item(
    device: Device,
    validation_message: str | None = None,
) -> IssueRegisterItem:
    refdes = device.refdes.upper()
    role = _derive_device_role(device)
    subsystem = _derive_device_subsystem(device)
    part_unknown = _is_unknown_part(device.part_number)
    compatible_missing = not device.compatible

    if device.dnp:
        bucket: IssueBucket = "exclude-from-dts"
        dts_relevant = False
        evidence_strength: EvidenceStrength = "strong"
        reason = "DNP component is not populated on the board build and should not emit DTS."
    elif _TESTPOINT_RE.match(refdes):
        bucket = "exclude-from-dts"
        dts_relevant = False
        evidence_strength = "strong"
        reason = "TP* refdes is a board test point / probe target, not a runtime DTS node."
    elif _SPLIT_UNIT_RE.match(refdes):
        bucket = "lookup-gap"
        dts_relevant = False
        evidence_strength = "strong"
        reason = (
            "Split-unit refdes indicates a schematic sub-unit of a parent IC; "
            "resolve the parent device instead of tracing it as a standalone DTS node."
        )
    elif not compatible_missing:
        bucket = "lookup-gap"
        dts_relevant = True
        evidence_strength = max(
            _strength_from_confidence(device.provenance.confidence),
            "medium",
            key=_strength_rank,
        )
        reason = _build_known_device_reason(device)
    elif compatible_missing and part_unknown and _CONNECTOR_RE.match(refdes):
        bucket = "exclude-from-dts"
        dts_relevant = False
        evidence_strength = "strong"
        reason = (
            "J* refdes with no compatible/part looks like connector-only board "
            "structure and should be excluded from DTS-focused unresolved work."
        )
    elif compatible_missing and part_unknown and _T_PREFIX_RE.match(refdes):
        bucket = "lookup-gap"
        dts_relevant = True
        evidence_strength = max(
            _strength_from_confidence(device.provenance.confidence),
            "medium",
            key=_strength_rank,
        )
        reason = (
            "T* refdes lacks compatible/part evidence; classify as lookup-gap "
            "rather than trace-gap until its actual function is identified."
        )
    elif compatible_missing and part_unknown:
        bucket = "lookup-gap"
        dts_relevant = not _looks_excludable_unknown(device)
        evidence_strength = _strength_from_confidence(device.provenance.confidence)
        if dts_relevant:
            reason = (
                "Device has no compatible and unknown part, so this is a lookup gap "
                "instead of a trace problem."
            )
        else:
            bucket = "exclude-from-dts"
            evidence_strength = max(evidence_strength, "medium", key=_strength_rank)
            reason = (
                "Unknown device looks like connector / fixture-only board detail; "
                "exclude it from DTS-focused unresolved tracking."
            )
    else:
        bucket = "lookup-gap"
        dts_relevant = True
        evidence_strength = _strength_from_confidence(device.provenance.confidence)
        reason = (
            "Device identity is only partially resolved; finish compatible/part lookup "
            "before treating it as a DTS candidate."
        )

    return IssueRegisterItem(
        kind="device",
        name=None if part_unknown else device.part_number,
        refdes=device.refdes,
        status=device.status,
        bucket=bucket,
        subsystem=subsystem,
        role=role,
        dts_relevant=dts_relevant,
        evidence_strength=evidence_strength,
        reason=reason,
        provenance_summary=_summarize_provenance(device.provenance, validation_message),
        validation_message=validation_message,
    )


def _build_known_device_reason(device: Device) -> str:
    missing_bits = _describe_missing_device_metadata(device)
    if not missing_bits:
        return (
            f"Device already maps to compatible '{device.compatible}'; keep it in "
            "lookup-gap until the remaining device metadata is resolved."
        )

    return (
        f"Device already maps to compatible '{device.compatible}'; "
        f"missing {'; '.join(missing_bits)}."
    )


def _describe_missing_device_metadata(device: Device) -> list[str]:
    missing: list[str] = []

    if _is_i2c_expander(device):
        if not device.bus:
            detail = "I2C bus metadata"
            if device.address:
                detail += f" (address={device.address} already known)"
            missing.append(detail)
        if not device.address:
            detail = "I2C address metadata"
            if device.bus:
                detail += f" (bus={device.bus} already known)"
            missing.append(detail)

    return missing


def _is_i2c_expander(device: Device) -> bool:
    part_upper = device.part_number.upper()
    compatible_upper = (device.compatible or "").upper()
    return any(token in part_upper for token in ("TCA9555", "PCA9555", "PCA9557")) or any(
        token in compatible_upper for token in ("PCA9555", "PCA9557")
    )


def _load_validation_messages(validation_path: Path | None) -> dict[str, dict[str, str]]:
    messages: dict[str, dict[str, list[str]]] = {
        "signal": {},
        "device": {},
    }
    if validation_path is None or not validation_path.exists():
        return {"signal": {}, "device": {}}

    data = json.loads(validation_path.read_text(encoding="utf-8"))
    for issue in data.get("issues", []):
        signal_name = issue.get("signal_name")
        device_name = issue.get("device_name")
        message = issue.get("message")
        if not message:
            continue
        if signal_name:
            messages["signal"].setdefault(signal_name, []).append(message)
        if device_name:
            messages["device"].setdefault(device_name, []).append(message)

    return {
        kind: {name: " | ".join(msgs) for name, msgs in values.items()}
        for kind, values in messages.items()
    }


def _build_summary(items: list[IssueRegisterItem]) -> dict[str, Any]:
    by_bucket = {bucket: 0 for bucket in _KNOWN_BUCKETS}
    by_kind = {kind: 0 for kind in _KNOWN_KINDS}
    by_kind_and_bucket = {
        kind: {bucket: 0 for bucket in _KNOWN_BUCKETS}
        for kind in _KNOWN_KINDS
    }
    by_evidence_strength = {strength: 0 for strength in _KNOWN_STRENGTHS}
    dts_relevant = {"true": 0, "false": 0}

    for item in items:
        by_bucket[item.bucket] += 1
        by_kind[item.kind] += 1
        by_kind_and_bucket[item.kind][item.bucket] += 1
        by_evidence_strength[item.evidence_strength] += 1
        dts_relevant["true" if item.dts_relevant else "false"] += 1

    return {
        "total_items": len(items),
        "actionable_items": dts_relevant["true"],
        "informational_items": dts_relevant["false"],
        "by_bucket": by_bucket,
        "by_kind": by_kind,
        "by_kind_and_bucket": by_kind_and_bucket,
        "dts_relevant": dts_relevant,
        "by_evidence_strength": by_evidence_strength,
    }


def _store_unique_issue(
    items_by_key: dict[tuple[IssueKind, str], IssueRegisterItem],
    key: tuple[IssueKind, str],
    candidate: IssueRegisterItem,
) -> None:
    existing = items_by_key.get(key)
    if existing is None:
        items_by_key[key] = candidate
        return

    if (
        bool(candidate.validation_message) and not bool(existing.validation_message)
    ) or _strength_rank(candidate.evidence_strength) > _strength_rank(existing.evidence_strength):
        items_by_key[key] = candidate


def _summarize_provenance(
    provenance: Provenance,
    validation_message: str | None,
) -> str:
    parts = [
        f"method={provenance.method}",
        f"confidence={provenance.confidence:.2f}",
    ]
    if provenance.pdfs:
        pages = ",".join(str(page) for page in provenance.pages)
        parts.append(f"source={','.join(provenance.pdfs)}@{pages}")
    if provenance.refs:
        parts.append(f"refs={','.join(provenance.refs)}")
    if validation_message:
        parts.append(f"validation={validation_message}")
    return "; ".join(parts)


def _strength_from_confidence(confidence: float) -> EvidenceStrength:
    if confidence >= 0.8:
        return "strong"
    if confidence >= 0.4:
        return "medium"
    return "weak"


def _strength_rank(strength: EvidenceStrength) -> int:
    return {
        "weak": 0,
        "medium": 1,
        "strong": 2,
    }[strength]


def _derive_signal_subsystem(name: str, role: str) -> str:
    haystack = f"{name} {role}".upper()
    keyword_map = (
        (("STRAP", "BOOT"), "boot-config"),
        (("ONU", "PON"), "pon"),
        (("NAND",), "nand"),
        (("EMMC", "MMC", "SD"), "storage"),
        (("SPI",), "spi"),
        (("RESET",), "reset"),
        (("ETH", "PHY"), "ethernet"),
        (("GPIO",), "gpio"),
    )
    for keywords, subsystem in keyword_map:
        if any(keyword in haystack for keyword in keywords):
            return subsystem
    return "misc"


def _is_low_runtime_signal(signal: Signal) -> bool:
    haystack = f"{signal.name} {signal.role}".upper()
    return any(token in haystack for token in _LOW_RUNTIME_SIGNAL_TOKENS)


def is_signal_dts_relevant(signal: Signal) -> bool:
    """Return whether an unresolved signal should stay in DTS-focused review."""
    return build_signal_issue_item(signal).dts_relevant


def _derive_device_role(device: Device) -> str:
    refdes = device.refdes.upper()
    if device.dnp:
        return "dnp"
    if _TESTPOINT_RE.match(refdes):
        return "testpoint"
    if _SPLIT_UNIT_RE.match(refdes):
        return "split-unit"
    if _CONNECTOR_RE.match(refdes):
        return "connector"
    if _T_PREFIX_RE.match(refdes):
        return "transformer-or-unknown"
    if refdes.startswith("U"):
        return "integrated-circuit"
    return "component"


def _derive_device_subsystem(device: Device) -> str:
    refdes = device.refdes.upper()
    if _TESTPOINT_RE.match(refdes):
        return "test-fixture"
    if _CONNECTOR_RE.match(refdes):
        return "external-interface"
    if _T_PREFIX_RE.match(refdes):
        return "external-interface"
    if _SPLIT_UNIT_RE.match(refdes):
        return "lookup"
    if refdes.startswith("U"):
        return "lookup"
    return "misc"


def _looks_excludable_unknown(device: Device) -> bool:
    refdes = device.refdes.upper()
    return bool(_CONNECTOR_RE.match(refdes) or _TESTPOINT_RE.match(refdes))


def _is_unknown_part(part_number: str | None) -> bool:
    if not part_number:
        return True
    return part_number.strip().upper() in {"UNKNOWN", "?", "N/A", "TBD"}


def is_device_dts_relevant(device: Device) -> bool:
    """Return whether an unresolved device should stay in DTS-focused review."""
    return build_device_issue_item(device).dts_relevant


__all__ = [
    "IssueRegister",
    "IssueRegisterItem",
    "build_device_issue_item",
    "build_and_write_issue_register",
    "build_issue_register",
    "build_signal_issue_item",
    "is_device_dts_relevant",
    "is_signal_dts_relevant",
    "write_issue_register",
]
