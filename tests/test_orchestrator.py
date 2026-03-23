"""Tests for dtsbuild.agents.orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from dtsbuild.agents import orchestrator
from dtsbuild.schema import HardwareSchema
from dtsbuild.schema_io import save_schema


@dataclass
class _FakeValidationReport:
    coverage_pct: float = 100.0
    issues: list = None
    passed: bool = True

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []

    def to_dict(self) -> dict:
        return {
            "coverage_pct": self.coverage_pct,
            "issues": self.issues,
            "passed": self.passed,
        }


@dataclass
class _FakeIssueRegister:
    summary: dict


def test_run_pipeline_uses_noninteractive_handler_for_resolver(tmp_path, monkeypatch):
    project_dir = tmp_path / "dtsin_TEST"
    output_dir = tmp_path / "dtsout_TEST"
    (project_dir / ".analysis").mkdir(parents=True)
    (project_dir / "tables").mkdir(parents=True)
    output_dir.mkdir()

    captured_handlers = []

    async def _run_indexer(_analysis_dir: Path):
        return {"page_indices": {}, "tag_index": {}, "refdes_index": {}, "connector_index": {}}

    async def _run_auditor(_indices, _gpio_table: Path, schema_path: Path):
        save_schema(HardwareSchema(project="TEST", chip="BCM68575"), schema_path)

    async def _run_resolver(_schema_path: Path, input_handler):
        captured_handlers.append(input_handler)
        return {"total": 1, "resolved": 1, "pending": 0, "suppressed": 0}

    async def _run_compiler(_schema_path: Path, output_path: Path, _ref_dts_path=None):
        output_path.write_text('/dts-v1/;\n/ { };\n', encoding="utf-8")
        return output_path

    monkeypatch.setattr(orchestrator, "run_indexer", _run_indexer)
    monkeypatch.setattr(orchestrator, "run_auditor", _run_auditor)
    monkeypatch.setattr(orchestrator, "run_resolver", _run_resolver)
    monkeypatch.setattr(orchestrator, "run_compiler", _run_compiler)
    monkeypatch.setattr(
        orchestrator,
        "validate_dts_syntax",
        lambda _dts: {"valid": True, "warnings": [], "errors": []},
    )
    monkeypatch.setattr(
        orchestrator,
        "compute_coverage",
        lambda _schema, _dts: {
            "coverage_pct": 100.0,
            "covered": 0,
            "total_verified": 0,
            "uncovered": [],
            "incomplete_not_in_dts": 0,
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "validate_dts_against_schema",
        lambda _dts_path, _schema_path: _FakeValidationReport(),
    )
    monkeypatch.setattr(
        orchestrator,
        "build_and_write_issue_register",
        lambda **_kwargs: _FakeIssueRegister(
            summary={
                "by_bucket": {"trace-gap": 0, "lookup-gap": 0, "exclude-from-dts": 0},
                "actionable_items": 0,
                "informational_items": 0,
            }
        ),
    )

    handler = lambda request: {"answer": "i2c0", "wasFreeform": False}
    asyncio.run(
        orchestrator.run_pipeline(
            project_dir=project_dir,
            output_dir=output_dir,
            interactive=False,
            input_handler=handler,
            resume=False,
        )
    )

    assert captured_handlers == [handler]
