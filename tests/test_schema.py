"""Tests for HardwareSchema Pydantic models and YAML/JSON roundtrip."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from dtsbuild.schema import (
    ClarificationRequest,
    Device,
    DtsHint,
    HardwareSchema,
    Provenance,
    Signal,
    TracedPath,
)
from dtsbuild.schema_io import load_schema, save_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prov(**overrides) -> Provenance:
    defaults = dict(
        pdfs=["mainboard.pdf"],
        pages=[3],
        refs=["U1E"],
        method="net_trace",
        confidence=0.95,
    )
    defaults.update(overrides)
    return Provenance(**defaults)


def _signal(name: str = "UART0_TX", status: str = "VERIFIED", **kw) -> Signal:
    defaults = dict(
        name=name,
        soc_pin="PIN45",
        traced_path="U1.Pin45 → R23(0R) → J1.Pin3",
        role="DEBUG_UART_TX",
        status=status,
        provenance=_prov(),
    )
    defaults.update(kw)
    return Signal(**defaults)


def _device(refdes: str = "U8", status: str = "VERIFIED", **kw) -> Device:
    defaults = dict(
        refdes=refdes,
        part_number="TCA9555PWR",
        status=status,
        provenance=_prov(),
    )
    defaults.update(kw)
    return Device(**defaults)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_provenance_creation(self) -> None:
        p = Provenance(
            pdfs=["mainboard.pdf"],
            pages=[3, 4],
            refs=["U1E", "R23"],
            method="net_trace",
            confidence=0.9,
            bom_line=42,
        )
        d = p.model_dump()
        assert d["pdfs"] == ["mainboard.pdf"]
        assert d["pages"] == [3, 4]
        assert d["refs"] == ["U1E", "R23"]
        assert d["method"] == "net_trace"
        assert d["confidence"] == 0.9
        assert d["bom_line"] == 42

    def test_provenance_cross_pdf(self) -> None:
        p = Provenance(
            pdfs=["mainboard.pdf", "daughter.pdf"],
            pages=[3, 7],
            refs=["U1E", "HN2436G"],
            method="differential_pair_trace",
            confidence=0.85,
        )
        assert len(p.pdfs) == 2
        assert p.bom_line is None


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

class TestSignal:
    def test_signal_verified(self) -> None:
        s = _signal()
        assert s.status == "VERIFIED"
        assert s.name == "UART0_TX"
        assert s.soc_pin == "PIN45"
        assert s.swap_detected is None
        assert s.swap_detail is None

    def test_signal_with_lane_swap(self) -> None:
        s = _signal(
            name="GPHY1_DP0",
            swap_detected=True,
            swap_detail="DP0↔DP1 at RJ45 connector J3",
        )
        assert s.swap_detected is True
        assert s.swap_detail == "DP0↔DP1 at RJ45 connector J3"

    def test_signal_without_swap(self) -> None:
        s = _signal()
        assert s.swap_detected is None
        assert s.swap_detail is None

    def test_signal_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            _signal(status="UNKNOWN")


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

class TestDevice:
    def test_device_creation(self) -> None:
        d = _device(bus="i2c0", address="0x27")
        assert d.bus == "i2c0"
        assert d.address == "0x27"
        assert d.dnp is False

    def test_device_dnp(self) -> None:
        d = _device(dnp=True)
        assert d.dnp is True


# ---------------------------------------------------------------------------
# TracedPath
# ---------------------------------------------------------------------------

class TestTracedPath:
    def test_traced_path_cross_pdf(self) -> None:
        tp = TracedPath(
            id="path-001",
            source="U1E.GPHY1_DP0_P",
            destination="J3.Pin4",
            segments=["U1E.Pin12", "R5(0R)", "J3.Pin4"],
            crosses_pdf=True,
            pdf_sequence=["mainboard.pdf", "daughter.pdf"],
            passive_components=["R5(0R)"],
            provenance=_prov(pdfs=["mainboard.pdf", "daughter.pdf"]),
        )
        assert tp.crosses_pdf is True
        assert len(tp.pdf_sequence) == 2


# ---------------------------------------------------------------------------
# ClarificationRequest
# ---------------------------------------------------------------------------

class TestClarificationRequest:
    def test_clarification_request_lifecycle(self) -> None:
        cr = ClarificationRequest(
            id="cr-wdt-001",
            blocking=True,
            domain="chip_features",
            question="Is WDT connected to GPIO7?",
            choices=["yes", "no"],
            evidence_context="GPIO table shows WDT on page 3",
            missing_evidence="No trace found to confirm",
            status="pending",
        )
        assert cr.status == "pending"
        assert cr.answer is None

        cr_answered = cr.model_copy(update={"status": "answered", "answer": "yes"})
        assert cr_answered.status == "answered"
        assert cr_answered.answer == "yes"


# ---------------------------------------------------------------------------
# DtsHint
# ---------------------------------------------------------------------------

class TestDtsHint:
    def test_dts_hint(self) -> None:
        h = DtsHint(
            target="&uart0",
            property="status",
            value="okay",
            reason="UART0 traced to debug header J1",
            provenance=_prov(),
        )
        assert h.target == "&uart0"
        assert h.property == "status"
        assert h.reason == "UART0 traced to debug header J1"


# ---------------------------------------------------------------------------
# HardwareSchema helpers
# ---------------------------------------------------------------------------

class TestHardwareSchemaHelpers:
    def test_hardware_schema_helpers(self) -> None:
        schema = HardwareSchema(
            project="BGW720",
            chip="BCM68575",
            signals=[
                _signal("UART0_TX", "VERIFIED"),
                _signal("SPI0_CLK", "INCOMPLETE"),
                _signal("GPHY1_DP0", "VERIFIED", swap_detected=True,
                        swap_detail="DP0↔DP1"),
            ],
            devices=[
                _device("U8", "VERIFIED"),
                _device("U9", "AMBIGUOUS"),
            ],
            clarification_requests=[
                ClarificationRequest(
                    id="cr-1", blocking=True, domain="gpio_assignment",
                    question="Which GPIO?", choices=["7", "8"],
                    evidence_context="ctx", missing_evidence="miss",
                    status="pending",
                ),
                ClarificationRequest(
                    id="cr-2", blocking=False, domain="led_polarity",
                    question="LED active high?", choices=["yes", "no"],
                    evidence_context="ctx", missing_evidence="miss",
                    status="answered", answer="yes",
                ),
            ],
            dts_hints=[
                DtsHint(target="&uart0", property="status", value="okay",
                        reason="traced", provenance=_prov()),
                DtsHint(target="ethphytop", property="lane-swap",
                        value="true", reason="swap", provenance=_prov()),
                DtsHint(target="&uart0", reason="pinctrl", provenance=_prov()),
            ],
        )
        assert len(schema.verified_signals()) == 2
        assert len(schema.verified_devices()) == 1
        assert len(schema.pending_clarifications()) == 1
        assert schema.has_lane_swap("GPHY1") is True
        assert schema.has_lane_swap("SPI") is False
        assert len(schema.get_dts_hints_for("&uart0")) == 2
        assert len(schema.get_dts_hints_for("ethphytop")) == 1


# ---------------------------------------------------------------------------
# Extra-field validation
# ---------------------------------------------------------------------------

class TestSchemaExtraForbid:
    def test_schema_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            Provenance(
                pdfs=["a.pdf"], pages=[1], refs=["U1"],
                method="m", confidence=0.5,
                bogus_field="nope",
            )
        with pytest.raises(ValidationError):
            HardwareSchema(
                project="X", chip="Y",
                not_a_field=123,
            )


# ---------------------------------------------------------------------------
# Roundtrip serialisation
# ---------------------------------------------------------------------------

class TestSchemaRoundtrip:
    def _minimal_schema(self) -> HardwareSchema:
        return HardwareSchema(
            project="BGW720",
            chip="BCM68575",
            signals=[_signal()],
            devices=[_device()],
        )

    def test_schema_yaml_roundtrip(self, tmp_path: Path) -> None:
        orig = self._minimal_schema()
        fpath = tmp_path / "hw.yaml"
        save_schema(orig, fpath, format="yaml")
        loaded = load_schema(fpath)
        assert loaded == orig

    def test_schema_json_roundtrip(self, tmp_path: Path) -> None:
        orig = self._minimal_schema()
        fpath = tmp_path / "hw.json"
        save_schema(orig, fpath, format="json")
        loaded = load_schema(fpath)
        assert loaded == orig

    def test_schema_complex_roundtrip(self, tmp_path: Path) -> None:
        schema = HardwareSchema(
            project="BGW720",
            chip="BCM68575",
            signals=[
                _signal("UART0_TX", "VERIFIED"),
                _signal("GPHY1_DP0", "VERIFIED",
                        swap_detected=True, swap_detail="DP0↔DP1"),
            ],
            devices=[
                _device("U8", "VERIFIED", bus="i2c0", address="0x27"),
            ],
            paths=[
                TracedPath(
                    id="path-001",
                    source="U1E.GPHY1_DP0_P",
                    destination="J3.Pin4",
                    segments=["U1E.Pin12", "R5(0R)", "J3.Pin4"],
                    crosses_pdf=True,
                    pdf_sequence=["mainboard.pdf", "daughter.pdf"],
                    passive_components=["R5(0R)"],
                    provenance=_prov(pdfs=["mainboard.pdf", "daughter.pdf"]),
                ),
            ],
            clarification_requests=[
                ClarificationRequest(
                    id="cr-1", blocking=True, domain="chip_features",
                    question="WDT on GPIO7?", choices=["yes", "no"],
                    evidence_context="page 3", missing_evidence="trace",
                    status="answered", answer="yes",
                    answer_provenance="user_input:2024-01-15",
                ),
            ],
            dts_hints=[
                DtsHint(target="&uart0", property="status", value="okay",
                        reason="traced to J1", provenance=_prov()),
            ],
            user_answers={"cr-1": "yes"},
        )
        fpath = tmp_path / "full.yaml"
        save_schema(schema, fpath, format="yaml")
        loaded = load_schema(fpath)
        assert loaded == schema
        assert loaded.user_answers == {"cr-1": "yes"}
        assert loaded.paths[0].crosses_pdf is True
