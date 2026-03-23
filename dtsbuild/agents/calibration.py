"""Structured sidecars for evidence-gated DTS calibration."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

CandidateType = Literal[
    "missing_node",
    "missing_property",
    "extra_generated_node",
    "value_mismatch",
    "binding_mismatch",
    "role_suspicion",
    "unsupported_surface",
]

RouteHint = Literal[
    "trace",
    "lookup",
    "renderer",
    "ask-me",
    "reject",
    "defer",
    "capability",
]

DecisionType = Literal[
    "ACCEPT",
    "REJECT_REF_ONLY",
    "DEFER_UNPROVEN",
    "ASK_ME",
    "UNSUPPORTED_SURFACE",
]

EvidenceSource = Literal[
    "generated_dts",
    "reference_dts",
    "schema",
    "validation",
    "unresolved",
    "schematic",
    "gpio_table",
    "datasheet",
    "ask_me",
]

AppliedChangeKind = Literal[
    "schema",
    "hint",
    "renderer",
    "rule",
    "ask-me",
    "doc",
]

_KNOWN_CANDIDATES: tuple[CandidateType, ...] = (
    "missing_node",
    "missing_property",
    "extra_generated_node",
    "value_mismatch",
    "binding_mismatch",
    "role_suspicion",
    "unsupported_surface",
)
_KNOWN_ROUTES: tuple[RouteHint, ...] = (
    "trace",
    "lookup",
    "renderer",
    "ask-me",
    "reject",
    "defer",
    "capability",
)
_KNOWN_DECISIONS: tuple[DecisionType, ...] = (
    "ACCEPT",
    "REJECT_REF_ONLY",
    "DEFER_UNPROVEN",
    "ASK_ME",
    "UNSUPPORTED_SURFACE",
)


@dataclass(slots=True)
class EvidencePointer:
    source: EvidenceSource
    path: str
    locator: str | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RefDiffCandidate:
    id: str
    candidate_type: CandidateType
    target: str
    project: str
    summary: str
    route_hint: RouteHint
    subsystem: str
    dts_relevant: bool
    generated_value: str | None = None
    reference_value: str | None = None
    generated_locator: str | None = None
    reference_locator: str | None = None
    reason: str | None = None
    compiler_surface: str | None = None
    schema_link: str | None = None
    validation_link: str | None = None
    evidence: list[EvidencePointer] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [pointer.to_dict() for pointer in self.evidence]
        return payload


@dataclass(slots=True)
class RefDiffReport:
    project: str
    generated_dts_path: str
    reference_dts_path: str
    schema_path: str | None = None
    validation_path: str | None = None
    unresolved_path: str | None = None
    candidates: list[RefDiffCandidate] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary:
            self.summary = _build_refdiff_summary(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "generated_dts_path": self.generated_dts_path,
            "reference_dts_path": self.reference_dts_path,
            "schema_path": self.schema_path,
            "validation_path": self.validation_path,
            "unresolved_path": self.unresolved_path,
            "summary": self.summary,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(slots=True)
class AppliedChange:
    kind: AppliedChangeKind
    target: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CalibrationDecision:
    candidate_id: str
    decision: DecisionType
    route: RouteHint
    rationale: str
    evidence: list[EvidencePointer] = field(default_factory=list)
    applied_changes: list[AppliedChange] = field(default_factory=list)
    follow_up: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [pointer.to_dict() for pointer in self.evidence]
        payload["applied_changes"] = [change.to_dict() for change in self.applied_changes]
        return payload


@dataclass(slots=True)
class CalibrationLog:
    project: str
    refdiff_path: str
    schema_path: str | None = None
    decisions: list[CalibrationDecision] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.summary:
            self.summary = _build_decision_summary(self.decisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "refdiff_path": self.refdiff_path,
            "schema_path": self.schema_path,
            "summary": self.summary,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def make_candidate_id(candidate_type: CandidateType, target: str, locator: str | None = None) -> str:
    """Create a stable identifier for a refdiff candidate."""
    parts = [candidate_type, target]
    if locator:
        parts.append(locator)
    slug = "-".join(_slugify(part) for part in parts if part)
    return slug or candidate_type


def write_refdiff_report(report: RefDiffReport, output_path: Path) -> Path:
    """Write a normalized refdiff report JSON artifact."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_refdiff_report(path: Path) -> RefDiffReport:
    """Load a refdiff report from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _report_from_dict(data)


def write_calibration_log(log: CalibrationLog, output_path: Path) -> Path:
    """Write a calibration decision log JSON artifact."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(log.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_calibration_log(path: Path) -> CalibrationLog:
    """Load a calibration decision log from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _log_from_dict(data)


def refresh_refdiff_summary(report: RefDiffReport) -> RefDiffReport:
    """Recompute the summary after mutating refdiff candidates in-place."""
    report.summary = _build_refdiff_summary(report.candidates)
    return report


def refresh_decision_summary(log: CalibrationLog) -> CalibrationLog:
    """Recompute the summary after mutating decisions in-place."""
    log.summary = _build_decision_summary(log.decisions)
    return log


def _build_refdiff_summary(candidates: list[RefDiffCandidate]) -> dict[str, Any]:
    by_type = {name: 0 for name in _KNOWN_CANDIDATES}
    by_route = {name: 0 for name in _KNOWN_ROUTES}
    by_relevance = {"true": 0, "false": 0}
    by_subsystem: dict[str, int] = {}

    for candidate in candidates:
        by_type.setdefault(candidate.candidate_type, 0)
        by_type[candidate.candidate_type] += 1
        by_route.setdefault(candidate.route_hint, 0)
        by_route[candidate.route_hint] += 1
        key = "true" if candidate.dts_relevant else "false"
        by_relevance[key] += 1
        subsystem = candidate.subsystem or "unknown"
        by_subsystem[subsystem] = by_subsystem.get(subsystem, 0) + 1

    return {
        "total_candidates": len(candidates),
        "by_type": by_type,
        "by_route_hint": by_route,
        "dts_relevant": by_relevance,
        "by_subsystem": by_subsystem,
    }


def _build_decision_summary(decisions: list[CalibrationDecision]) -> dict[str, Any]:
    by_decision = {name: 0 for name in _KNOWN_DECISIONS}
    by_route = {name: 0 for name in _KNOWN_ROUTES}
    by_change_kind: dict[str, int] = {}

    for decision in decisions:
        by_decision.setdefault(decision.decision, 0)
        by_decision[decision.decision] += 1
        by_route.setdefault(decision.route, 0)
        by_route[decision.route] += 1
        for change in decision.applied_changes:
            by_change_kind[change.kind] = by_change_kind.get(change.kind, 0) + 1

    return {
        "total_decisions": len(decisions),
        "by_decision": by_decision,
        "by_route": by_route,
        "by_change_kind": by_change_kind,
    }


def _report_from_dict(data: dict[str, Any]) -> RefDiffReport:
    candidates = [
        _candidate_from_dict(candidate_data)
        for candidate_data in data.get("candidates", [])
    ]
    return RefDiffReport(
        project=data["project"],
        generated_dts_path=data["generated_dts_path"],
        reference_dts_path=data["reference_dts_path"],
        schema_path=data.get("schema_path"),
        validation_path=data.get("validation_path"),
        unresolved_path=data.get("unresolved_path"),
        candidates=candidates,
        summary=data.get("summary", {}),
    )


def _log_from_dict(data: dict[str, Any]) -> CalibrationLog:
    decisions = [
        _decision_from_dict(decision_data)
        for decision_data in data.get("decisions", [])
    ]
    return CalibrationLog(
        project=data["project"],
        refdiff_path=data["refdiff_path"],
        schema_path=data.get("schema_path"),
        decisions=decisions,
        summary=data.get("summary", {}),
    )


def _candidate_from_dict(data: dict[str, Any]) -> RefDiffCandidate:
    return RefDiffCandidate(
        id=data["id"],
        candidate_type=data["candidate_type"],
        target=data["target"],
        project=data["project"],
        summary=data["summary"],
        route_hint=data["route_hint"],
        subsystem=data.get("subsystem", "unknown"),
        dts_relevant=bool(data.get("dts_relevant", True)),
        generated_value=data.get("generated_value"),
        reference_value=data.get("reference_value"),
        generated_locator=data.get("generated_locator"),
        reference_locator=data.get("reference_locator"),
        reason=data.get("reason"),
        compiler_surface=data.get("compiler_surface"),
        schema_link=data.get("schema_link"),
        validation_link=data.get("validation_link"),
        evidence=[_pointer_from_dict(item) for item in data.get("evidence", [])],
    )


def _decision_from_dict(data: dict[str, Any]) -> CalibrationDecision:
    return CalibrationDecision(
        candidate_id=data["candidate_id"],
        decision=data["decision"],
        route=data["route"],
        rationale=data["rationale"],
        evidence=[_pointer_from_dict(item) for item in data.get("evidence", [])],
        applied_changes=[
            AppliedChange(
                kind=item["kind"],
                target=item["target"],
                summary=item["summary"],
            )
            for item in data.get("applied_changes", [])
        ],
        follow_up=data.get("follow_up"),
    )


def _pointer_from_dict(data: dict[str, Any]) -> EvidencePointer:
    return EvidencePointer(
        source=data["source"],
        path=data["path"],
        locator=data.get("locator"),
        summary=data.get("summary"),
    )


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return normalized.strip("-")
