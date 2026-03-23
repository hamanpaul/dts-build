"""Tests for dtsbuild.session — pipeline session persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from dtsbuild.session import (
    PHASES,
    SessionState,
    clear_session,
    create_session,
    load_session,
    save_session,
)


@pytest.fixture
def dirs(tmp_path: Path):
    """Return (project_dir, output_dir) under tmp_path."""
    proj = tmp_path / "dtsin_test"
    proj.mkdir()
    out = tmp_path / "dtsout_test"
    out.mkdir()
    return proj, out


# ── Basic lifecycle ──────────────────────────────────────────────────


def test_create_new_session(dirs):
    proj, out = dirs
    state = create_session(proj, out, interactive=True, session_id="test-001")

    assert state.session_id == "test-001"
    assert state.project_dir == str(proj)
    assert state.output_dir == str(out)
    assert state.interactive is True
    assert state.completed_phases == []
    assert state.current_phase is None
    assert state.error is None
    assert state.is_complete is False
    assert state.next_phase == "index"
    assert state.created_at != ""
    assert state.updated_at != ""


def test_save_and_load_session(dirs):
    proj, out = dirs
    state = SessionState(
        session_id="round-trip",
        project_dir=str(proj),
        output_dir=str(out),
        interactive=False,
    )
    state.schema_file = "test.schema.yaml"
    save_session(state)

    loaded = load_session(out)
    assert loaded is not None
    assert loaded.session_id == "round-trip"
    assert loaded.schema_file == "test.schema.yaml"
    assert loaded.project_dir == str(proj)


def test_resume_session(dirs):
    proj, out = dirs
    state = create_session(proj, out, session_id="resume-01")
    state.mark_phase_done("index")
    save_session(state)

    resumed = create_session(proj, out, session_id="should-be-ignored")
    assert resumed.session_id == "resume-01"
    assert "index" in resumed.completed_phases
    assert resumed.next_phase == "audit"


# ── Phase progression ────────────────────────────────────────────────


def test_next_phase(dirs):
    proj, out = dirs
    state = SessionState(
        session_id="phase-test",
        project_dir=str(proj),
        output_dir=str(out),
    )

    for i, phase in enumerate(PHASES):
        assert state.next_phase == phase
        state.mark_phase_done(phase)

    assert state.next_phase is None


def test_is_complete(dirs):
    proj, out = dirs
    state = SessionState(
        session_id="complete-test",
        project_dir=str(proj),
        output_dir=str(out),
    )
    assert state.is_complete is False

    for phase in PHASES:
        state.mark_phase_done(phase)

    assert state.is_complete is True


# ── Error handling ───────────────────────────────────────────────────


def test_mark_error(dirs):
    proj, out = dirs
    state = create_session(proj, out, session_id="err-01")
    state.mark_error("something broke")
    save_session(state)

    loaded = load_session(out)
    assert loaded is not None
    assert loaded.error == "something broke"


def test_create_session_after_error_creates_new(dirs):
    """A session with an error should NOT be resumed — a new one is created."""
    proj, out = dirs
    state = create_session(proj, out, session_id="err-old")
    state.mark_error("fatal")
    save_session(state)

    new_state = create_session(proj, out, session_id="err-new")
    assert new_state.session_id == "err-new"


# ── clear_session ────────────────────────────────────────────────────


def test_clear_session(dirs):
    proj, out = dirs
    create_session(proj, out, session_id="to-clear")
    assert (out / ".dts-session.json").exists()

    assert clear_session(out) is True
    assert not (out / ".dts-session.json").exists()
    assert clear_session(out) is False


# ── Resume existing incomplete session ───────────────────────────────


def test_create_session_resumes_existing(dirs):
    proj, out = dirs
    original = create_session(proj, out, session_id="orig-01")
    original.mark_phase_done("index")
    original.mark_phase_done("audit")
    save_session(original)

    resumed = create_session(proj, out, session_id="new-id-ignored")
    assert resumed.session_id == "orig-01"
    assert resumed.completed_phases == ["index", "audit"]
    assert resumed.next_phase == "resolve"


def test_load_session_missing_dir(tmp_path):
    assert load_session(tmp_path / "nonexistent") is None


def test_mark_phase_started(dirs):
    proj, out = dirs
    state = SessionState(
        session_id="start-test",
        project_dir=str(proj),
        output_dir=str(out),
    )
    state.mark_phase_started("index")
    assert state.current_phase == "index"
    state.mark_phase_done("index")
    assert state.current_phase is None
