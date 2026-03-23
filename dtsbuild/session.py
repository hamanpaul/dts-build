"""Pipeline session persistence.

Saves and restores pipeline state so users can interrupt and resume.
Each session tracks:
- Which phase completed (index, audit, resolve, compile, validate)
- Schema snapshot after each phase
- Answer log from interactive resolver
- Timestamps and metadata
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PHASES = ["index", "audit", "resolve", "compile", "validate"]

SESSION_FILENAME = ".dts-session.json"


@dataclass
class SessionState:
    """Persistent state for a dts-build pipeline session."""

    session_id: str
    project_dir: str
    output_dir: str
    created_at: str = ""
    updated_at: str = ""
    completed_phases: list[str] = field(default_factory=list)
    current_phase: Optional[str] = None
    interactive: bool = False
    error: Optional[str] = None

    # Artifact paths (relative to output_dir)
    schema_file: Optional[str] = None
    dts_file: Optional[str] = None
    answer_log_file: Optional[str] = None
    coverage_file: Optional[str] = None
    validation_file: Optional[str] = None

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    @property
    def next_phase(self) -> Optional[str]:
        """Return the next phase to execute, or None if all done."""
        for phase in PHASES:
            if phase not in self.completed_phases:
                return phase
        return None

    @property
    def is_complete(self) -> bool:
        return all(p in self.completed_phases for p in PHASES)

    def mark_phase_done(self, phase: str):
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)
        self.current_phase = None
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_phase_started(self, phase: str):
        self.current_phase = phase
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_error(self, error: str):
        self.error = error
        self.updated_at = datetime.now(timezone.utc).isoformat()


def _session_path(output_dir: Path) -> Path:
    return output_dir / SESSION_FILENAME


def save_session(state: SessionState) -> Path:
    """Save session state to output directory."""
    out = Path(state.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = _session_path(out)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(asdict(state), indent=2, ensure_ascii=False))
    logger.debug(
        "Session saved: %s (phases: %s)", state.session_id, state.completed_phases
    )
    return path


def load_session(output_dir: Path) -> Optional[SessionState]:
    """Load session state from output directory, or None if not found."""
    path = _session_path(output_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return SessionState(**data)
    except Exception as e:
        logger.warning("Failed to load session from %s: %s", path, e)
        return None


def create_session(
    project_dir: Path,
    output_dir: Path,
    interactive: bool = False,
    session_id: str | None = None,
) -> SessionState:
    """Create a new session (or return existing if resumable)."""
    existing = load_session(output_dir)
    if existing and not existing.is_complete and not existing.error:
        logger.info(
            "Resuming session %s (completed: %s, next: %s)",
            existing.session_id,
            existing.completed_phases,
            existing.next_phase,
        )
        return existing

    sid = session_id or f"dts-{uuid.uuid4().hex[:8]}"
    state = SessionState(
        session_id=sid,
        project_dir=str(project_dir),
        output_dir=str(output_dir),
        interactive=interactive,
    )
    save_session(state)
    logger.info("New session created: %s", sid)
    return state


def clear_session(output_dir: Path) -> bool:
    """Remove session state file."""
    path = _session_path(output_dir)
    if path.exists():
        path.unlink()
        return True
    return False
