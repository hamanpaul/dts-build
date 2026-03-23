"""Tests for dtsbuild.agents.issue_register."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from dtsbuild.agents.issue_register import (
    build_issue_register,
    write_issue_register,
)


def _provenance(
    refs: list[str] | None = None,
    method: str = "refdes_lookup",
    confidence: float = 0.3,
):
    return {
        "pdfs": ["board.pdf"],
        "pages": [1],
        "refs": refs or [],
        "method": method,
        "confidence": confidence,
    }


def _make_schema(signals=None, devices=None):
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
    path = tmp_path / "test.schema.yaml"
    path.write_text(yaml.dump(schema_dict, default_flow_style=False), encoding="utf-8")
    return path


def _write_validation(tmp_path: Path, issues: list[dict]) -> Path:
    path = tmp_path / "test.validation.json"
    path.write_text(json.dumps({"issues": issues}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _build_fixture_register(tmp_path: Path):
    schema = _make_schema(
        signals=[
            {
                "name": "ROGUE_ONU_IN1",
                "soc_pin": "A1",
                "traced_path": "U1→?",
                "role": "GENERAL_GPIO",
                "status": "INCOMPLETE",
                "provenance": _provenance(method="net_trace", confidence=0.4),
            },
            {
                "name": "RESET_OUT_L",
                "soc_pin": "A2",
                "traced_path": "U1→?",
                "role": "RESET",
                "status": "INCOMPLETE",
                "provenance": _provenance(method="net_trace", confidence=0.4),
            },
            {
                "name": "SW Boot Strap",
                "soc_pin": "A3",
                "traced_path": "U1→?",
                "role": "STRAP",
                "status": "INCOMPLETE",
                "provenance": _provenance(method="net_trace", confidence=0.4),
            },
        ],
        devices=[
            {
                "refdes": "TP72",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["TP72"]),
            },
            {
                "refdes": "U1D",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U1D"]),
            },
            {
                "refdes": "U20A",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U20A"]),
            },
            {
                "refdes": "J4",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["J4"]),
            },
            {
                "refdes": "T17",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["T17"]),
            },
            {
                "refdes": "U41",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U41"]),
            },
        ],
    )
    validation = [
        {
            "message": "Signal 'ROGUE_ONU_IN1' is INCOMPLETE — not fully resolved",
            "signal_name": "ROGUE_ONU_IN1",
            "device_name": None,
        },
        {
            "message": "Signal 'RESET_OUT_L' is INCOMPLETE — not fully resolved",
            "signal_name": "RESET_OUT_L",
            "device_name": None,
        },
        {
            "message": "Signal 'SW Boot Strap' is INCOMPLETE — not fully resolved",
            "signal_name": "SW Boot Strap",
            "device_name": None,
        },
        {
            "message": "Device 'TP72' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "TP72",
        },
        {
            "message": "Device 'U1D' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "U1D",
        },
        {
            "message": "Device 'U20A' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "U20A",
        },
        {
            "message": "Device 'J4' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "J4",
        },
        {
            "message": "Device 'T17' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "T17",
        },
        {
            "message": "Device 'U41' is INCOMPLETE — not fully resolved",
            "signal_name": None,
            "device_name": "U41",
        },
    ]

    schema_path = _write_schema(tmp_path, schema)
    validation_path = _write_validation(tmp_path, validation)
    register = build_issue_register(schema_path, validation_path)
    return register, schema_path, validation_path


def test_issue_register_buckets_known_examples(tmp_path):
    register, _, _ = _build_fixture_register(tmp_path)
    items = {
        item.name or item.refdes: item
        for item in register.items
    }

    assert items["TP72"].bucket == "exclude-from-dts"
    assert items["TP72"].dts_relevant is False

    assert items["U1D"].bucket == "lookup-gap"
    assert items["U1D"].dts_relevant is False
    assert items["U20A"].bucket == "lookup-gap"
    assert items["U20A"].dts_relevant is False

    assert items["ROGUE_ONU_IN1"].bucket == "trace-gap"
    assert items["ROGUE_ONU_IN1"].dts_relevant is True
    assert items["RESET_OUT_L"].bucket == "trace-gap"
    assert items["RESET_OUT_L"].dts_relevant is True

    assert items["SW Boot Strap"].bucket == "trace-gap"
    assert items["SW Boot Strap"].dts_relevant is False


def test_issue_register_summary_counts(tmp_path):
    register, _, _ = _build_fixture_register(tmp_path)

    assert register.summary["total_items"] == 9
    assert register.summary["actionable_items"] == 4
    assert register.summary["informational_items"] == 5
    assert register.summary["by_bucket"] == {
        "trace-gap": 3,
        "lookup-gap": 4,
        "exclude-from-dts": 2,
    }
    assert register.summary["by_kind"] == {
        "signal": 3,
        "device": 6,
    }
    assert register.summary["dts_relevant"] == {
        "true": 4,
        "false": 5,
    }


def test_issue_register_explains_missing_i2c_bus_for_known_device(tmp_path):
    schema = _make_schema(
        devices=[
            {
                "refdes": "U41",
                "part_number": "TCA9555PWR",
                "compatible": "nxp,pca9555",
                "bus": None,
                "address": "0x27",
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U41"], confidence=0.85),
            }
        ]
    )
    schema_path = _write_schema(tmp_path, schema)

    register = build_issue_register(schema_path)
    item = register.items[0]

    assert item.bucket == "lookup-gap"
    assert item.dts_relevant is True
    assert "missing I2C bus metadata" in item.reason
    assert "address=0x27 already known" in item.reason


def test_issue_register_json_round_trip(tmp_path):
    register, _, _ = _build_fixture_register(tmp_path)
    output_path = tmp_path / "test.unresolved.json"
    write_issue_register(register, output_path)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    as_dict = register.to_dict()

    assert json.loads(json.dumps(as_dict, ensure_ascii=False)) == as_dict
    assert written == as_dict
    assert written["items"][0]["provenance_summary"]


def test_issue_register_deduplicates_same_identity(tmp_path):
    schema = _make_schema(
        signals=[
            {
                "name": "ROGUE_ONU_IN1",
                "soc_pin": "A1",
                "traced_path": "U1→?",
                "role": "GENERAL_GPIO",
                "status": "INCOMPLETE",
                "provenance": _provenance(method="net_trace", confidence=0.4),
            },
            {
                "name": "ROGUE_ONU_IN1",
                "soc_pin": "A1",
                "traced_path": "U1→?",
                "role": "GENERAL_GPIO",
                "status": "INCOMPLETE",
                "provenance": _provenance(method="net_trace", confidence=0.4),
            },
        ],
        devices=[
            {
                "refdes": "TP72",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["TP72"]),
            },
            {
                "refdes": "TP72",
                "part_number": "UNKNOWN",
                "compatible": None,
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["TP72"]),
            },
        ],
    )
    schema_path = _write_schema(tmp_path, schema)
    register = build_issue_register(schema_path)

    assert register.summary["total_items"] == 2
    assert register.summary["by_kind"] == {
        "signal": 1,
        "device": 1,
    }


def test_issue_register_keeps_connector_with_compatible(tmp_path):
    schema = _make_schema(
        devices=[
            {
                "refdes": "J4",
                "part_number": "TCA9555PWR",
                "compatible": "nxp,pca9555",
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["J4"]),
            }
        ],
    )
    schema_path = _write_schema(tmp_path, schema)
    register = build_issue_register(schema_path)

    assert register.summary["total_items"] == 1
    item = register.items[0]
    assert item.refdes == "J4"
    assert item.bucket == "lookup-gap"
    assert item.dts_relevant is True
