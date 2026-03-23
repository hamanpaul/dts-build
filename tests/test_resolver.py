"""Tests for dtsbuild.agents.resolver."""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from dtsbuild.agents.resolver import run_resolver
from dtsbuild.schema_io import load_schema


def _provenance(refs: list[str] | None = None, confidence: float = 0.85):
    return {
        "pdfs": ["board.pdf"],
        "pages": [5],
        "refs": refs or [],
        "method": "refdes_lookup",
        "confidence": confidence,
    }


def _make_schema(signals=None, devices=None, clarifications=None):
    return {
        "version": "1.0",
        "project": "TEST",
        "chip": "BCM68575",
        "signals": signals or [],
        "devices": devices or [],
        "paths": [],
        "clarification_requests": clarifications or [],
        "dts_hints": [],
        "user_answers": {},
    }


def _write_schema(tmp_path: Path, schema_dict: dict) -> Path:
    path = tmp_path / "test.schema.yaml"
    path.write_text(yaml.dump(schema_dict, default_flow_style=False), encoding="utf-8")
    return path


def test_resolver_suppresses_non_dts_relevant_questions(tmp_path):
    schema = _make_schema(
        signals=[
            {
                "name": "SW Boot Strap",
                "soc_pin": "GPIO_17",
                "traced_path": "(no trace found)",
                "role": "STRAP",
                "status": "INCOMPLETE",
                "provenance": _provenance(confidence=0.4),
            }
        ],
        devices=[
            {
                "refdes": "U41",
                "part_number": "TCA9555PWR",
                "compatible": "nxp,pca9555",
                "bus": None,
                "address": "0x27",
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U41"]),
            }
        ],
        clarifications=[
            {
                "id": "cr-strap-sw boot strap",
                "blocking": False,
                "domain": "gpio_assignment",
                "question": "信號 'SW Boot Strap' (role=STRAP) 狀態為 INCOMPLETE，是否需要納入 DTS？",
                "choices": ["是，納入 DTS", "否，不納入", "需要更多資訊"],
                "evidence_context": "無額外證據",
                "missing_evidence": "missing",
                "status": "pending",
                "answer": None,
                "answer_provenance": None,
            }
        ],
    )
    schema_path = _write_schema(tmp_path, schema)

    stats = asyncio.run(run_resolver(schema_path, None))
    updated = load_schema(schema_path)

    pending_ids = {
        cr.id for cr in updated.clarification_requests if cr.status == "pending"
    }
    skipped_ids = {
        cr.id for cr in updated.clarification_requests if cr.status == "skipped"
    }
    pending_cr = next(cr for cr in updated.clarification_requests if cr.id == "cr-dev-u41")

    assert stats["total"] == 1
    assert stats["suppressed"] == 1
    assert pending_ids == {"cr-dev-u41"}
    assert "cr-strap-sw boot strap" in skipped_ids
    assert pending_cr.question == (
        "TCA9555 I2C GPIO expander 的 I2C bus 是哪一條？"
        "請直接提供 bus 名稱（例如 i2c0 / i2c1）。"
    )
    assert pending_cr.choices == [
        "i2c0",
        "i2c1",
        "其他（請直接填 bus 名稱）",
        "需要更多資訊",
    ]
    assert pending_cr.evidence_context == (
        "來源 PDF: board.pdf; Compatible: nxp,pca9555 (已確認); "
        "Bus: 未確認; Address: 0x27 (已確認)"
    )
    assert pending_cr.missing_evidence == (
        "請確認 Device 'U41' (TCA9555PWR) 的 I2C bus 名稱；"
        "目前已知 compatible=nxp,pca9555、address=0x27"
    )


def test_resolver_reasks_skipped_relevant_clarification(tmp_path):
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
                "provenance": _provenance(refs=["U41"]),
            }
        ],
        clarifications=[
            {
                "id": "cr-dev-u41",
                "blocking": False,
                "domain": "gpio_assignment",
                "question": "TCA9555 I2C GPIO expander 的 I2C bus 是哪一條？請直接提供 bus 名稱（例如 i2c0 / i2c1）。",
                "choices": ["i2c0", "i2c1", "其他（請直接填 bus 名稱）", "需要更多資訊"],
                "evidence_context": "Bus: 未確認",
                "missing_evidence": "missing",
                "status": "skipped",
                "answer": "SKIPPED",
                "answer_provenance": "user_skip",
            }
        ],
    )
    schema_path = _write_schema(tmp_path, schema)

    asked: list[str] = []

    def _handler(request):
        asked.append(request["question"])
        return {
            "answer": "i2c0",
            "wasFreeform": False,
        }

    stats = asyncio.run(run_resolver(schema_path, _handler))
    updated = load_schema(schema_path)
    cr = next(cr for cr in updated.clarification_requests if cr.id == "cr-dev-u41")
    dev = next(dev for dev in updated.devices if dev.refdes == "U41")

    assert asked == ["TCA9555 I2C GPIO expander 的 I2C bus 是哪一條？請直接提供 bus 名稱（例如 i2c0 / i2c1）。"]
    assert stats["resolved"] == 1
    assert cr.status == "answered"
    assert cr.answer == "i2c0"
    assert dev.bus == "i2c0"
    assert dev.status == "VERIFIED"


def test_resolver_keeps_device_incomplete_when_more_info_is_requested(tmp_path):
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
                "provenance": _provenance(refs=["U41"]),
            }
        ],
    )
    schema_path = _write_schema(tmp_path, schema)

    def _handler(_request):
        return {
            "answer": "需要更多資訊",
            "wasFreeform": False,
        }

    asyncio.run(run_resolver(schema_path, _handler))
    updated = load_schema(schema_path)
    dev = next(dev for dev in updated.devices if dev.refdes == "U41")
    cr = next(cr for cr in updated.clarification_requests if cr.id == "cr-dev-u41")

    assert cr.answer == "需要更多資訊"
    assert dev.status == "INCOMPLETE"
    assert dev.bus is None
    assert dev.dnp is False


def test_resolver_parses_freeform_i2c_bus_and_address_answer(tmp_path):
    schema = _make_schema(
        devices=[
            {
                "refdes": "U41",
                "part_number": "TCA9555PWR",
                "compatible": "nxp,pca9555",
                "bus": None,
                "address": None,
                "status": "INCOMPLETE",
                "dnp": False,
                "provenance": _provenance(refs=["U41"]),
            }
        ],
    )
    schema_path = _write_schema(tmp_path, schema)

    def _handler(_request):
        return {
            "answer": "U41 = i2c0@0x27",
            "wasFreeform": True,
        }

    asyncio.run(run_resolver(schema_path, _handler))
    updated = load_schema(schema_path)
    dev = next(dev for dev in updated.devices if dev.refdes == "U41")
    cr = next(cr for cr in updated.clarification_requests if cr.id == "cr-dev-u41")

    assert cr.answer == "U41 = i2c0@0x27"
    assert cr.answer_provenance == "user_freeform"
    assert dev.bus == "i2c0"
    assert dev.address == "0x27"
    assert dev.status == "VERIFIED"
    assert dev.provenance.pdfs == ["board.pdf"]
    assert "refdes_lookup" in dev.provenance.method
    assert "user_answer" in dev.provenance.method
