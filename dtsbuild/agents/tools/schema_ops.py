"""
Schema operation tools used by multiple agents.

Agent B (Auditor) uses: write_signal, write_device, write_traced_path,
                        write_dts_hint, query_schema
Agent C (Resolver) uses: query_schema, find_ambiguities, emit_clarification,
                         record_answer
Agent D (Compiler) uses: query_schema, get_schema_summary
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Literal

try:
    from copilot.tools import define_tool as _sdk_define_tool
    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    _sdk_define_tool = None


def define_tool(description: str = ""):
    """Decorator that registers a tool with the SDK (if available) while
    keeping the decorated function directly callable."""
    def decorator(func):
        func._tool_description = description
        if _sdk_define_tool is not None:
            tool_obj = _sdk_define_tool(description=description)(func)
            func._tool = tool_obj
        return func
    return decorator

from dtsbuild.schema import (
    HardwareSchema,
    Signal,
    Device,
    TracedPath,
    ClarificationRequest,
    DtsHint,
    Provenance,
)
from dtsbuild.schema_io import save_schema, load_schema


# ── helpers ──────────────────────────────────────────────────────────

def _load(schema_path: str) -> HardwareSchema:
    return load_schema(Path(schema_path))


def _save(schema: HardwareSchema, schema_path: str) -> None:
    save_schema(schema, Path(schema_path))


def _to_provenance(prov: dict | Provenance) -> Provenance:
    if isinstance(prov, Provenance):
        return prov
    return Provenance.model_validate(prov)


# ── write tools ──────────────────────────────────────────────────────

@define_tool(description="Add a Signal record to the hardware schema.")
def write_signal(
    *,
    schema_path: str,
    name: str,
    soc_pin: str,
    traced_path: str,
    role: str,
    status: Literal["VERIFIED", "INCOMPLETE", "AMBIGUOUS"],
    provenance: dict | Provenance,
    swap_detected: bool | None = None,
    swap_detail: str | None = None,
) -> dict[str, Any]:
    """Add a Signal to the schema, save, and return confirmation."""
    schema = _load(schema_path)
    sig = Signal(
        name=name,
        soc_pin=soc_pin,
        traced_path=traced_path,
        role=role,
        status=status,
        provenance=_to_provenance(provenance),
        swap_detected=swap_detected,
        swap_detail=swap_detail,
    )
    schema.signals.append(sig)
    _save(schema, schema_path)
    return {"status": "ok", "record": "signal", "name": name, "signal_status": status}


@define_tool(description="Add a Device record to the hardware schema.")
def write_device(
    *,
    schema_path: str,
    refdes: str,
    part_number: str,
    status: Literal["VERIFIED", "INCOMPLETE", "AMBIGUOUS"],
    provenance: dict | Provenance,
    compatible: str | None = None,
    bus: str | None = None,
    address: str | None = None,
    dnp: bool = False,
) -> dict[str, Any]:
    """Add a Device to the schema, save, and return confirmation."""
    schema = _load(schema_path)
    dev = Device(
        refdes=refdes,
        part_number=part_number,
        compatible=compatible,
        bus=bus,
        address=address,
        status=status,
        dnp=dnp,
        provenance=_to_provenance(provenance),
    )
    schema.devices.append(dev)
    _save(schema, schema_path)
    return {"status": "ok", "record": "device", "refdes": refdes, "device_status": status}


@define_tool(description="Add a TracedPath record to the hardware schema.")
def write_traced_path(
    *,
    schema_path: str,
    id: str,
    source: str,
    destination: str,
    segments: list[str],
    pdf_sequence: list[str],
    passive_components: list[str],
    provenance: dict | Provenance,
    crosses_pdf: bool = False,
) -> dict[str, Any]:
    """Add a TracedPath to the schema, save, and return confirmation."""
    schema = _load(schema_path)
    tp = TracedPath(
        id=id,
        source=source,
        destination=destination,
        segments=segments,
        crosses_pdf=crosses_pdf,
        pdf_sequence=pdf_sequence,
        passive_components=passive_components,
        provenance=_to_provenance(provenance),
    )
    schema.paths.append(tp)
    _save(schema, schema_path)
    return {"status": "ok", "record": "traced_path", "id": id}


@define_tool(description="Add a DtsHint record to the hardware schema.")
def write_dts_hint(
    *,
    schema_path: str,
    target: str,
    reason: str,
    provenance: dict | Provenance,
    property: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    """Add a DtsHint to the schema, save, and return confirmation."""
    schema = _load(schema_path)
    hint = DtsHint(
        target=target,
        property=property,
        value=value,
        reason=reason,
        provenance=_to_provenance(provenance),
    )
    schema.dts_hints.append(hint)
    _save(schema, schema_path)
    return {"status": "ok", "record": "dts_hint", "target": target}


# ── query tools ──────────────────────────────────────────────────────

@define_tool(description="Query schema records with flexible filtering by type, status, and name pattern.")
def query_schema(
    *,
    schema_path: str,
    record_type: Literal["signal", "device", "path", "clarification", "hint"],
    status: str | None = None,
    name_pattern: str | None = None,
) -> list[dict[str, Any]]:
    """Return matching records as a list of dicts."""
    schema = _load(schema_path)

    collection_map: dict[str, tuple[list, str]] = {
        "signal":        (schema.signals, "name"),
        "device":        (schema.devices, "refdes"),
        "path":          (schema.paths, "id"),
        "clarification": (schema.clarification_requests, "id"),
        "hint":          (schema.dts_hints, "target"),
    }

    records, name_field = collection_map[record_type]
    results: list[dict[str, Any]] = []

    for rec in records:
        if status is not None and hasattr(rec, "status") and rec.status != status:
            continue
        if name_pattern is not None:
            val = getattr(rec, name_field, "")
            if not fnmatch.fnmatch(val, name_pattern):
                continue
        results.append(rec.model_dump(mode="python"))

    return results


# ── ambiguity / clarification tools ─────────────────────────────────

@define_tool(description="Find all INCOMPLETE and AMBIGUOUS records across the entire schema.")
def find_ambiguities(*, schema_path: str) -> dict[str, Any]:
    """Return counts and lists of unresolved records."""
    schema = _load(schema_path)

    incomplete_signals = [
        s.model_dump(mode="python") for s in schema.signals if s.status == "INCOMPLETE"
    ]
    ambiguous_signals = [
        s.model_dump(mode="python") for s in schema.signals if s.status == "AMBIGUOUS"
    ]
    incomplete_devices = [
        d.model_dump(mode="python") for d in schema.devices if d.status == "INCOMPLETE"
    ]
    ambiguous_devices = [
        d.model_dump(mode="python") for d in schema.devices if d.status == "AMBIGUOUS"
    ]
    pending_clarifications = [
        c.model_dump(mode="python")
        for c in schema.clarification_requests
        if c.status == "pending"
    ]

    total = (
        len(incomplete_signals)
        + len(ambiguous_signals)
        + len(incomplete_devices)
        + len(ambiguous_devices)
        + len(pending_clarifications)
    )

    return {
        "incomplete_signals": incomplete_signals,
        "ambiguous_signals": ambiguous_signals,
        "incomplete_devices": incomplete_devices,
        "ambiguous_devices": ambiguous_devices,
        "pending_clarifications": pending_clarifications,
        "total_unresolved": total,
    }


@define_tool(description="Create a new ClarificationRequest in the schema.")
def emit_clarification(
    *,
    schema_path: str,
    id: str,
    blocking: bool,
    domain: str,
    question: str,
    choices: list[str],
    evidence_context: str,
    missing_evidence: str,
    status: Literal["pending", "answered", "skipped"] = "pending",
    answer: str | None = None,
    answer_provenance: str | None = None,
) -> dict[str, Any]:
    """Append a ClarificationRequest to the schema and return it."""
    schema = _load(schema_path)
    cr = ClarificationRequest(
        id=id,
        blocking=blocking,
        domain=domain,
        question=question,
        choices=choices,
        evidence_context=evidence_context,
        missing_evidence=missing_evidence,
        status=status,
        answer=answer,
        answer_provenance=answer_provenance,
    )
    schema.clarification_requests.append(cr)
    _save(schema, schema_path)
    return cr.model_dump(mode="python")


@define_tool(description="Record a user's answer to a ClarificationRequest.")
def record_answer(
    *,
    schema_path: str,
    cr_id: str,
    answer: str,
    was_freeform: bool = False,
) -> dict[str, Any]:
    """Find a CR by id, mark it answered, persist, and return confirmation."""
    schema = _load(schema_path)

    target: ClarificationRequest | None = None
    for cr in schema.clarification_requests:
        if cr.id == cr_id:
            target = cr
            break

    if target is None:
        return {"status": "error", "message": f"ClarificationRequest '{cr_id}' not found"}

    normalized_answer = answer.strip().lower()
    is_skip = normalized_answer in {"skipped", "skip", "跳過"}

    target.status = "skipped" if is_skip else "answered"
    target.answer = answer
    if is_skip:
        prov_method = "user_skip"
    else:
        prov_method = "user_freeform" if was_freeform else "user_choice"
    target.answer_provenance = prov_method

    schema.user_answers[cr_id] = answer
    _save(schema, schema_path)
    return {"status": "ok", "cr_id": cr_id, "answer": answer, "method": prov_method}


# ── summary tool ─────────────────────────────────────────────────────

@define_tool(description="Get a high-level summary of schema state.")
def get_schema_summary(*, schema_path: str) -> dict[str, Any]:
    """Return aggregated counts and status breakdowns."""
    schema = _load(schema_path)

    def _status_counts(items) -> dict[str, int]:
        total = len(items)
        verified = sum(1 for i in items if i.status == "VERIFIED")
        incomplete = sum(1 for i in items if i.status == "INCOMPLETE")
        ambiguous = sum(1 for i in items if i.status == "AMBIGUOUS")
        return {
            "total": total,
            "verified": verified,
            "incomplete": incomplete,
            "ambiguous": ambiguous,
        }

    cr_total = len(schema.clarification_requests)
    cr_pending = sum(1 for c in schema.clarification_requests if c.status == "pending")
    cr_answered = sum(1 for c in schema.clarification_requests if c.status == "answered")

    lane_swaps = sum(1 for s in schema.signals if s.swap_detected)

    return {
        "project": schema.project,
        "signals": _status_counts(schema.signals),
        "devices": _status_counts(schema.devices),
        "paths": {
            "total": len(schema.paths),
            "cross_pdf": sum(1 for p in schema.paths if p.crosses_pdf),
        },
        "clarifications": {
            "total": cr_total,
            "pending": cr_pending,
            "answered": cr_answered,
        },
        "dts_hints": len(schema.dts_hints),
        "lane_swaps_detected": lane_swaps,
    }
