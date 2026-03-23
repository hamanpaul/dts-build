"""Join refdiff candidates with schema/validation/unresolved evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dtsbuild.schema_io import load_schema

from .calibration import EvidencePointer, RefDiffCandidate, RefDiffReport, refresh_refdiff_summary


def triage_refdiff_report(
    report: RefDiffReport,
    schema_path: Path,
    validation_path: Path | None = None,
    unresolved_path: Path | None = None,
) -> RefDiffReport:
    """Enrich refdiff candidates with local evidence and routing hints."""
    schema = load_schema(schema_path)
    validation_issues = _load_validation_issues(validation_path)
    unresolved_items = _load_unresolved_items(unresolved_path)

    for idx, candidate in enumerate(report.candidates):
        candidate.schema_link = str(schema_path)

        if candidate.candidate_type == "extra_generated_node":
            candidate.route_hint = "reject"
            candidate.reason = (
                candidate.reason
                or "Generated-only node should be rejected or explicitly justified before retention."
            )
        elif candidate.candidate_type == "unsupported_surface" or not candidate.compiler_surface:
            candidate.route_hint = "capability"
            candidate.reason = (
                candidate.reason
                or "No known compiler surface exists for this candidate yet."
            )

        schema_pointers = _match_schema(candidate, schema_path, schema)
        validation_pointers = _match_validation(candidate, validation_path, validation_issues)
        unresolved_pointers = _match_unresolved(candidate, unresolved_path, unresolved_items)

        candidate.evidence.extend(schema_pointers)
        candidate.evidence.extend(validation_pointers)
        candidate.evidence.extend(unresolved_pointers)

        if candidate.route_hint != "capability" and any(
            "lookup-gap" in (pointer.summary or "") for pointer in unresolved_pointers
        ):
            candidate.route_hint = "lookup"
            candidate.reason = (
                _describe_lookup_gap_reason(candidate, schema)
                or "Unresolved lookup-gap evidence overlaps with this candidate; complete device metadata first."
            )
        elif candidate.route_hint != "capability" and any(
            "trace-gap" in (pointer.summary or "") for pointer in unresolved_pointers
        ):
            candidate.route_hint = "trace"
            candidate.reason = (
                "Unresolved trace-gap evidence overlaps with this candidate; schematic tracing should run first."
            )
        elif candidate.route_hint == "renderer" and validation_pointers:
            candidate.reason = (
                "Validation already reports a schema-backed coverage gap for this subsystem; review renderer/rule gating."
            )
        elif candidate.route_hint == "renderer" and schema_pointers and candidate.reason is None:
            candidate.reason = (
                "Schema already carries related evidence; prefer renderer or rule fixes before new board-specific hints."
            )

        if validation_pointers:
            candidate.validation_link = _validation_locator(idx, validation_pointers[0])

    return refresh_refdiff_summary(report)


def _describe_lookup_gap_reason(candidate: RefDiffCandidate, schema) -> str | None:
    if candidate.subsystem != "i2c":
        return None

    target_lower = candidate.target.lower()
    for device in schema.devices:
        compat_lower = (device.compatible or "").lower()
        address_lower = (device.address or "").lower()
        if not (
            "pca9555" in compat_lower
            or "tca9555" in device.part_number.lower()
            or (address_lower and address_lower in target_lower)
        ):
            continue

        missing_fields: list[str] = []
        if not device.bus:
            missing_fields.append("bus")
        if not device.address:
            missing_fields.append("address")
        if not missing_fields:
            continue

        known_fields = []
        if device.compatible:
            known_fields.append(f"compatible={device.compatible}")
        if device.address:
            known_fields.append(f"address={device.address}")
        if device.bus:
            known_fields.append(f"bus={device.bus}")
        known_suffix = f" (known: {', '.join(known_fields)})" if known_fields else ""

        return (
            "Unresolved lookup-gap evidence overlaps with this candidate; "
            f"confirm the missing I2C {'/'.join(missing_fields)} metadata for device "
            f"{device.refdes} before emitting this node{known_suffix}."
        )

    return None


def _match_schema(candidate: RefDiffCandidate, schema_path: Path, schema) -> list[EvidencePointer]:
    pointers: list[EvidencePointer] = []
    target_lower = candidate.target.lower()
    subsystem = candidate.subsystem

    for idx, signal in enumerate(schema.signals):
        name_lower = signal.name.lower()
        role_lower = signal.role.lower()
        if subsystem == "spi" and ("spi" in role_lower or name_lower.startswith("spis_")):
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"signals[{idx}]",
                    summary=f"signal={signal.name}; role={signal.role}; status={signal.status}",
                )
            )
        elif subsystem == "led" and ("led" in role_lower or "led" in name_lower):
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"signals[{idx}]",
                    summary=f"signal={signal.name}; role={signal.role}; status={signal.status}",
                )
            )
        elif subsystem == "power" and ("power" in role_lower or "ps_en" in name_lower):
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"signals[{idx}]",
                    summary=f"signal={signal.name}; role={signal.role}; status={signal.status}",
                )
            )
        elif subsystem in {"serdes", "ethernet"} and (
            "sfp" in role_lower
            or "serdes" in role_lower
            or "ethernet" in role_lower
            or "wan_" in name_lower
        ):
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"signals[{idx}]",
                    summary=f"signal={signal.name}; role={signal.role}; status={signal.status}",
                )
            )

    for idx, device in enumerate(schema.devices):
        refdes_lower = device.refdes.lower()
        compat_lower = (device.compatible or "").lower()
        address_lower = (device.address or "").lower()
        if subsystem == "i2c" and (
            "pca9555" in compat_lower
            or "tca9555" in device.part_number.lower()
            or (address_lower and address_lower in target_lower)
        ):
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"devices[{idx}]",
                    summary=(
                        f"device={device.refdes}; compatible={device.compatible}; "
                        f"bus={device.bus}; address={device.address}; status={device.status}"
                    ),
                )
            )
        elif subsystem == "led" and "595" in device.part_number.lower():
            pointers.append(
                EvidencePointer(
                    source="schema",
                    path=str(schema_path),
                    locator=f"devices[{idx}]",
                    summary=f"device={device.refdes}; part={device.part_number}; status={device.status}",
                )
            )

    return pointers


def _match_validation(
    candidate: RefDiffCandidate,
    validation_path: Path | None,
    issues: list[dict[str, Any]],
) -> list[EvidencePointer]:
    if validation_path is None:
        return []

    subsystem = candidate.subsystem
    matches: list[EvidencePointer] = []
    for idx, issue in enumerate(issues):
        message = str(issue.get("message", ""))
        if candidate.compiler_surface and candidate.compiler_surface in {"_render_hsspi"}:
            if "role=SPI" in message or "SPIS_" in message:
                matches.append(
                    EvidencePointer(
                        source="validation",
                        path=str(validation_path),
                        locator=f"issues[{idx}]",
                        summary=message,
                    )
                )
        elif subsystem == "power" and "POWER" in message.upper():
            matches.append(
                EvidencePointer(
                    source="validation",
                    path=str(validation_path),
                    locator=f"issues[{idx}]",
                    summary=message,
                )
            )
        elif subsystem == "i2c" and ("U41" in message or "pca9555" in message.lower()):
            matches.append(
                EvidencePointer(
                    source="validation",
                    path=str(validation_path),
                    locator=f"issues[{idx}]",
                    summary=message,
                )
            )
        elif subsystem == "serdes" and ("SFP" in message or "SERDES" in message.upper()):
            matches.append(
                EvidencePointer(
                    source="validation",
                    path=str(validation_path),
                    locator=f"issues[{idx}]",
                    summary=message,
                )
            )

    return matches


def _match_unresolved(
    candidate: RefDiffCandidate,
    unresolved_path: Path | None,
    items: list[dict[str, Any]],
) -> list[EvidencePointer]:
    if unresolved_path is None:
        return []

    subsystem = candidate.subsystem
    target_lower = candidate.target.lower()
    matches: list[EvidencePointer] = []

    for idx, item in enumerate(items):
        refdes = str(item.get("refdes") or "").lower()
        name = str(item.get("name") or "").lower()
        role = str(item.get("role") or "").lower()
        bucket = str(item.get("bucket") or "")
        summary = (
            f"bucket={bucket}; role={item.get('role')}; "
            f"dts_relevant={item.get('dts_relevant')}; name={item.get('name') or item.get('refdes')}"
        )

        if subsystem == "i2c" and ("u41" == refdes or "tca9555" in name or "0x27" in target_lower):
            matches.append(
                EvidencePointer(
                    source="unresolved",
                    path=str(unresolved_path),
                    locator=f"items[{idx}]",
                    summary=summary,
                )
            )
        elif subsystem == "power" and (
            ("ext_pwr" in target_lower or "pwr-ctrl" in target_lower or "power" in target_lower)
            and ("power" in role or "ps_en" in name or "pwr" in name)
        ):
            matches.append(
                EvidencePointer(
                    source="unresolved",
                    path=str(unresolved_path),
                    locator=f"items[{idx}]",
                    summary=summary,
                )
            )
        elif subsystem == "serdes" and ("sfp" in role or "wan" in name):
            matches.append(
                EvidencePointer(
                    source="unresolved",
                    path=str(unresolved_path),
                    locator=f"items[{idx}]",
                    summary=summary,
                )
            )

    return matches


def _load_validation_issues(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    issues = payload.get("issues", [])
    return issues if isinstance(issues, list) else []


def _load_unresolved_items(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def _validation_locator(index: int, pointer: EvidencePointer) -> str:
    return pointer.locator or f"issues[{index}]"
