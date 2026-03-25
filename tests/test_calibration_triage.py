"""Tests for dtsbuild.agents.calibration_triage."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from dtsbuild.agents.calibration import RefDiffCandidate, RefDiffReport
from dtsbuild.agents.calibration_triage import triage_refdiff_report


def _write_schema(tmp_path: Path, signals=None, devices=None) -> Path:
    path = tmp_path / "test.schema.yaml"
    path.write_text(
        yaml.dump(
            {
                "version": "1.0",
                "project": "TEST",
                "chip": "BCM68575",
                "signals": signals or [],
                "devices": devices or [],
                "paths": [],
                "clarification_requests": [],
                "dts_hints": [],
                "user_answers": {},
            },
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _prov() -> dict:
    return {
        "pdfs": ["board.pdf"],
        "pages": [1],
        "refs": ["U1"],
        "method": "net_trace",
        "confidence": 0.9,
    }


def test_triage_routes_i2c_candidates_to_lookup_when_u41_is_unresolved(tmp_path):
    schema_path = _write_schema(
        tmp_path,
        devices=[
            {
                "refdes": "U41",
                "part_number": "TCA9555PWR",
                "compatible": "nxp,pca9555",
                "bus": None,
                "address": "0x27",
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _prov(),
            },
        ],
    )
    validation_path = _write_json(
        tmp_path / "test.validation.json",
        {"issues": [{"message": "Device 'U41' is INCOMPLETE — DTS-relevant metadata still unresolved"}]},
    )
    unresolved_path = _write_json(
        tmp_path / "test.unresolved.json",
        {
            "items": [
                {
                    "refdes": "U41",
                    "name": "TCA9555PWR",
                    "role": "integrated-circuit",
                    "bucket": "lookup-gap",
                    "dts_relevant": True,
                }
            ]
        },
    )
    report = RefDiffReport(
        project="TEST",
        generated_dts_path="generated.dts",
        reference_dts_path="reference.dts",
        candidates=[
            RefDiffCandidate(
                id="cand-u41",
                candidate_type="missing_node",
                target="/&i2c0/gpio@27",
                project="TEST",
                summary="Reference DTS defines gpio@27 under i2c0.",
                route_hint="renderer",
                subsystem="i2c",
                dts_relevant=True,
                compiler_surface="_render_i2c",
            ),
        ],
    )

    triaged = triage_refdiff_report(report, schema_path, validation_path, unresolved_path)
    candidate = triaged.candidates[0]

    assert candidate.route_hint == "lookup"
    assert any(pointer.source == "schema" for pointer in candidate.evidence)
    assert any(pointer.source == "unresolved" for pointer in candidate.evidence)
    assert "lookup-gap" in candidate.reason
    assert "missing I2C bus metadata" in candidate.reason
    assert "address=0x27" in candidate.reason


def test_triage_keeps_extra_generated_nodes_on_reject_route(tmp_path):
    schema_path = _write_schema(tmp_path)
    report = RefDiffReport(
        project="TEST",
        generated_dts_path="generated.dts",
        reference_dts_path="reference.dts",
        candidates=[
            RefDiffCandidate(
                id="cand-extra",
                candidate_type="extra_generated_node",
                target="/&led_ctrl/led4",
                project="TEST",
                summary="Generated-only LED node.",
                route_hint="renderer",
                subsystem="led",
                dts_relevant=True,
            ),
        ],
    )

    triaged = triage_refdiff_report(report, schema_path)
    assert triaged.candidates[0].route_hint == "reject"


def test_triage_marks_candidates_without_surface_as_capability(tmp_path):
    schema_path = _write_schema(tmp_path)
    report = RefDiffReport(
        project="TEST",
        generated_dts_path="generated.dts",
        reference_dts_path="reference.dts",
        candidates=[
            RefDiffCandidate(
                id="cand-tod",
                candidate_type="unsupported_surface",
                target="/tod",
                project="TEST",
                summary="Reference-only tod node.",
                route_hint="renderer",
                subsystem="general",
                dts_relevant=True,
                compiler_surface=None,
            ),
        ],
    )

    triaged = triage_refdiff_report(report, schema_path)
    assert triaged.candidates[0].route_hint == "capability"
    assert "compiler surface" in triaged.candidates[0].reason


def test_triage_keeps_capability_route_even_with_unrelated_lookup_gap(tmp_path):
    schema_path = _write_schema(tmp_path)
    unresolved_path = _write_json(
        tmp_path / "test.unresolved.json",
        {
            "items": [
                {
                    "refdes": "U41",
                    "name": "TCA9555PWR",
                    "role": "integrated-circuit",
                    "bucket": "lookup-gap",
                    "dts_relevant": True,
                }
            ]
        },
    )
    report = RefDiffReport(
        project="TEST",
        generated_dts_path="generated.dts",
        reference_dts_path="reference.dts",
        candidates=[
            RefDiffCandidate(
                id="cand-tod",
                candidate_type="unsupported_surface",
                target="/tod",
                project="TEST",
                summary="Reference-only tod node.",
                route_hint="capability",
                subsystem="general",
                dts_relevant=True,
                compiler_surface=None,
            ),
        ],
    )

    triaged = triage_refdiff_report(report, schema_path, unresolved_path=unresolved_path)
    candidate = triaged.candidates[0]

    assert candidate.route_hint == "capability"
    assert "compiler surface" in candidate.reason


def test_triage_uses_validation_to_explain_renderer_route(tmp_path):
    schema_path = _write_schema(
        tmp_path,
        signals=[
            {
                "name": "SPIS_CLK",
                "soc_pin": "A1",
                "traced_path": "U1→R1",
                "role": "SPI",
                "status": "VERIFIED",
                "provenance": _prov(),
            },
        ],
    )
    validation_path = _write_json(
        tmp_path / "test.validation.json",
        {"issues": [{"message": "Verified signal 'SPIS_CLK' (role=SPI) not found in DTS"}]},
    )
    report = RefDiffReport(
        project="TEST",
        generated_dts_path="generated.dts",
        reference_dts_path="reference.dts",
        candidates=[
            RefDiffCandidate(
                id="cand-hsspi",
                candidate_type="missing_node",
                target="/&hsspi",
                project="TEST",
                summary="Reference enables hsspi.",
                route_hint="renderer",
                subsystem="spi",
                dts_relevant=True,
                compiler_surface="_render_hsspi",
            ),
        ],
    )

    triaged = triage_refdiff_report(report, schema_path, validation_path)
    candidate = triaged.candidates[0]

    assert candidate.route_hint == "renderer"
    assert any(pointer.source == "validation" for pointer in candidate.evidence)
    assert "coverage gap" in candidate.reason
