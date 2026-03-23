"""Tests for dtsbuild.agents.validation module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dtsbuild.agents.validation import (
    ValidationIssue,
    ValidationReport,
    validate_dts_against_schema,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _provenance():
    return {
        "pdfs": ["board.pdf"],
        "pages": [1],
        "refs": ["U1"],
        "method": "net_trace",
        "confidence": 0.9,
    }


def _make_schema(signals=None, devices=None):
    """Return a minimal schema dict ready for YAML serialisation."""
    return {
        "version": "1.0",
        "project": "TEST",
        "chip": "BCM68575",
        "signals": signals or [],
        "devices": devices or [],
        "paths": [],
        "clarification_requests": [],
        "dts_hints": [],
        "user_answers": {},
    }


def _write_schema(tmp_path: Path, schema_dict: dict) -> Path:
    p = tmp_path / "test.schema.yaml"
    p.write_text(yaml.dump(schema_dict, default_flow_style=False))
    return p


def _write_dts(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.dts"
    p.write_text(content, encoding="utf-8")
    return p


# ── Tests ────────────────────────────────────────────────────────────

def test_validation_perfect_coverage(tmp_path):
    """Schema with 2 verified signals whose names appear in DTS → 100%."""
    schema = _make_schema(signals=[
        {
            "name": "UART0_TX",
            "soc_pin": "A1",
            "traced_path": "U1→R1→J1",
            "role": "DEBUG_UART_TX",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
        {
            "name": "RESET_BTN",
            "soc_pin": "B2",
            "traced_path": "U1→SW1",
            "role": "RESET_BUTTON",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
    ])
    dts = """\
/dts-v1/;

/ {
\tuart0_tx_node {
\t\tstatus = "okay";
\t};
\treset_btn_node {
\t\tstatus = "okay";
\t};
};
"""
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.coverage_pct == 100.0
    assert report.verified_signals == 2
    assert report.total_signals == 2
    assert report.incomplete_signals == 0
    # No error-level issues expected
    assert report.passed is True


def test_validation_missing_signal(tmp_path):
    """Verified signal not found in DTS → warning issue."""
    schema = _make_schema(signals=[
        {
            "name": "ZZUNKNOWN_SIG",
            "soc_pin": "Z99",
            "traced_path": "U1→nowhere",
            "role": "MYSTERY_ROLE_XYZ",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
    ])
    dts = "/dts-v1/;\n/ { };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.coverage_pct == 0.0
    assert report.verified_signals == 0
    missing = [i for i in report.issues if i.category == "missing_node"]
    assert len(missing) == 1
    assert missing[0].severity == "warning"
    assert "ZZUNKNOWN_SIG" in missing[0].message


def test_validation_todo_count(tmp_path):
    """DTS with 3 TODO comments → todo_count=3."""
    schema = _make_schema()
    dts = """\
/dts-v1/;

/ {
\t/* TODO fill in address */
\t/* TODO check polarity */
\t// TODO verify pin number
};
"""
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.todo_count == 3
    todo_issues = [i for i in report.issues if i.category == "todo_remaining"]
    assert len(todo_issues) == 1
    assert "3" in todo_issues[0].message


def test_validation_syntax_error(tmp_path):
    """DTS missing /dts-v1/; header → error issue."""
    schema = _make_schema()
    dts = "/ {\n\tstatus = \"okay\";\n};\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.passed is False
    syntax_errors = [
        i for i in report.issues
        if i.category == "syntax" and i.severity == "error"
    ]
    assert any("header" in e.message.lower() for e in syntax_errors)


def test_validation_summary_text(tmp_path):
    """summary_text() returns a non-empty, multi-line human-readable string."""
    schema = _make_schema(signals=[
        {
            "name": "LED0",
            "soc_pin": "C3",
            "traced_path": "U1→D1",
            "role": "LED_CONTROL",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
    ])
    dts = "/dts-v1/;\n/ { led0 { status = \"okay\"; }; };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)
    text = report.summary_text()

    assert isinstance(text, str)
    assert len(text) > 20
    assert "Coverage" in text
    assert "PASS" in text or "FAIL" in text


def test_validation_passed_property(tmp_path):
    """No errors → passed=True; with error → passed=False."""
    # ── passed = True (valid DTS, no missing items)
    schema = _make_schema()
    dts_ok = "/dts-v1/;\n/ { };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts_ok)

    report_ok = validate_dts_against_schema(dts_path, schema_path)
    assert report_ok.passed is True

    # ── passed = False (unbalanced braces)
    dts_bad = "/dts-v1/;\n/ {\n"
    dts_path.write_text(dts_bad)

    report_bad = validate_dts_against_schema(dts_path, schema_path)
    assert report_bad.passed is False


def test_validation_to_dict(tmp_path):
    """to_dict() returns a JSON-serializable dict."""
    schema = _make_schema()
    dts = "/dts-v1/;\n/ { };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)
    d = report.to_dict()

    # Must be JSON-round-trippable
    json_str = json.dumps(d, ensure_ascii=False)
    assert json.loads(json_str) == d
    assert "passed" in d
    assert "coverage_pct" in d
    assert "summary" in d
    assert isinstance(d["issues"], list)


def test_validation_unbalanced_braces(tmp_path):
    """Unbalanced braces → error issue."""
    schema = _make_schema()
    dts = "/dts-v1/;\n/ {\n\tnode {\n};\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.passed is False
    brace_err = [
        i for i in report.issues
        if i.category == "syntax" and "brace" in i.message.lower()
    ]
    assert len(brace_err) == 1


def test_validation_incomplete_signals(tmp_path):
    """DTS-relevant INCOMPLETE signals stay actionable warnings."""
    schema = _make_schema(signals=[
        {
            "name": "SIG_OK",
            "soc_pin": "A1",
            "traced_path": "U1→J1",
            "role": "UART",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
        {
            "name": "SIG_INC",
            "soc_pin": "A2",
            "traced_path": "U1→?",
            "role": "UNKNOWN",
            "status": "INCOMPLETE",
            "provenance": _provenance(),
        },
    ])
    dts = "/dts-v1/;\n/ { uart { status = \"okay\"; }; };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.incomplete_signals == 1
    assert report.total_signals == 2
    unresolved = [i for i in report.issues if i.category == "unresolved_signal"]
    assert len(unresolved) == 1
    assert unresolved[0].severity == "warning"
    assert unresolved[0].dts_relevant is True
    assert "SIG_INC" in unresolved[0].message
    assert report.build_summary()["unresolved"]["actionable"] == 1


def test_validation_demotes_non_dts_relevant_signal_to_info(tmp_path):
    schema = _make_schema(signals=[
        {
            "name": "SW Boot Strap",
            "soc_pin": "A3",
            "traced_path": "U1→?",
            "role": "STRAP",
            "status": "INCOMPLETE",
            "provenance": {
                **_provenance(),
                "confidence": 0.4,
            },
        },
    ])
    dts = "/dts-v1/;\n/ { };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    info_issues = [
        i for i in report.issues
        if i.category == "informational_unresolved_signal"
    ]
    assert len(info_issues) == 1
    assert info_issues[0].severity == "info"
    assert info_issues[0].dts_relevant is False
    assert info_issues[0].bucket == "trace-gap"
    summary = report.build_summary()
    assert summary["unresolved"]["actionable"] == 0
    assert summary["unresolved"]["informational"] == 1


def test_validation_device_coverage(tmp_path):
    """Verified device with compatible string found in DTS."""
    schema = _make_schema(devices=[
        {
            "refdes": "U8",
            "part_number": "TCA9555PWR",
            "compatible": "nxp,pca9555",
            "bus": "i2c0",
            "address": "0x27",
            "status": "VERIFIED",
            "provenance": _provenance(),
        },
    ])
    dts = '/dts-v1/;\n/ { i2c0 { gpio@27 { compatible = "nxp,pca9555"; }; }; };\n'
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    assert report.verified_devices == 1
    assert report.total_devices == 1
    assert report.coverage_pct == 100.0


def test_validation_keeps_dts_relevant_device_actionable(tmp_path):
    schema = _make_schema(devices=[
        {
            "refdes": "U41",
            "part_number": "TCA9555PWR",
            "compatible": "nxp,pca9555",
            "bus": None,
            "address": "0x27",
            "status": "INCOMPLETE",
            "dnp": False,
            "provenance": {
                **_provenance(),
                "refs": ["U41"],
                "confidence": 0.85,
            },
        }
    ])
    dts = "/dts-v1/;\n/ { };\n"
    schema_path = _write_schema(tmp_path, schema)
    dts_path = _write_dts(tmp_path, dts)

    report = validate_dts_against_schema(dts_path, schema_path)

    unresolved = [i for i in report.issues if i.category == "unresolved_device"]
    assert len(unresolved) == 1
    assert unresolved[0].severity == "warning"
    assert unresolved[0].dts_relevant is True
    assert report.build_summary()["unresolved"]["by_kind"]["device"]["actionable"] == 1
