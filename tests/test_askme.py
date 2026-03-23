"""Tests for the Ask-Me CLI handler (askme module)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dtsbuild.askme import AnswerLog, create_cli_handler, create_non_interactive_handler


# ---------------------------------------------------------------------------
# AnswerLog
# ---------------------------------------------------------------------------

class TestAnswerLog:
    def test_answer_log_basic(self) -> None:
        log = AnswerLog()
        log.record(question="Which GPIO?", answer="7", was_freeform=False, choices=["7", "8"])
        assert len(log.answers) == 1
        entry = log.answers[0]
        assert entry["question"] == "Which GPIO?"
        assert entry["answer"] == "7"
        assert entry["was_freeform"] is False
        assert entry["choices"] == ["7", "8"]
        assert "timestamp" in entry

    def test_answer_log_persistence(self, tmp_path: Path) -> None:
        log_file = tmp_path / "answers.json"
        log1 = AnswerLog(log_path=log_file)
        log1.record(question="Q1", answer="A1", was_freeform=True)
        log1.record(question="Q2", answer="A2", was_freeform=False, choices=["A2", "A3"])

        # Reload from file
        log2 = AnswerLog(log_path=log_file)
        assert len(log2.answers) == 2
        assert log2.answers[0]["answer"] == "A1"
        assert log2.answers[1]["answer"] == "A2"

        # Verify raw JSON on disk
        raw = json.loads(log_file.read_text(encoding="utf-8"))
        assert len(raw) == 2

    def test_answer_log_get_answer_for(self) -> None:
        log = AnswerLog()
        log.record(question="Is WDT on GPIO7?", answer="yes", was_freeform=False)
        log.record(question="LED active high?", answer="no", was_freeform=False)

        assert log.get_answer_for("WDT") == "yes"
        assert log.get_answer_for("LED") == "no"
        assert log.get_answer_for("nonexistent") is None


# ---------------------------------------------------------------------------
# create_cli_handler
# ---------------------------------------------------------------------------

class TestCreateCliHandler:
    def test_create_cli_handler_returns_tuple(self) -> None:
        handler, log = create_cli_handler()
        assert callable(handler)
        assert isinstance(log, AnswerLog)

    def test_cli_handler_auto_answers(self) -> None:
        handler, log = create_cli_handler(
            auto_answers={"GPIO": "7", "LED": "active-high"},
        )
        resp = handler({"question": "Which GPIO for WDT?", "choices": ["7", "8"]})
        assert resp["answer"] == "7"
        assert resp["wasFreeform"] is False

        resp2 = handler({"question": "LED polarity?", "choices": []})
        assert resp2["answer"] == "active-high"

        assert len(log.answers) == 2
        assert log.get_answer_for("GPIO") == "7"


# ---------------------------------------------------------------------------
# create_non_interactive_handler
# ---------------------------------------------------------------------------

class TestNonInteractiveHandler:
    def test_non_interactive_handler_skip(self) -> None:
        handler, log = create_non_interactive_handler(default_action="skip")
        resp = handler({"question": "Some question?", "choices": ["a", "b"]})
        assert resp["answer"] == "SKIPPED"
        assert resp["wasFreeform"] is True
        assert len(log.answers) == 1

    def test_non_interactive_handler_fail(self) -> None:
        handler, _log = create_non_interactive_handler(default_action="fail")
        with pytest.raises(RuntimeError):
            handler({"question": "Should fail", "choices": []})

    def test_non_interactive_handler_replays_existing_answer_log(self, tmp_path: Path) -> None:
        log_path = tmp_path / "answers.json"
        seeded = AnswerLog(log_path=log_path)
        seeded.record(
            question="TCA9555 I2C GPIO expander 的 I2C bus 是哪一條？請直接提供 bus 名稱（例如 i2c0 / i2c1）。",
            answer="i2c0@0x27",
            was_freeform=True,
            choices=["i2c0", "i2c1"],
        )

        handler, replay_log = create_non_interactive_handler(log_path=log_path)
        resp = handler(
            {
                "question": "TCA9555 I2C GPIO expander 的 I2C bus 是哪一條？\n請直接提供 bus 名稱（例如 i2c0 / i2c1）。",
                "choices": ["i2c0", "i2c1"],
            }
        )

        assert resp["answer"] == "i2c0@0x27"
        assert resp["wasFreeform"] is True
        assert len(replay_log.answers) == 1
