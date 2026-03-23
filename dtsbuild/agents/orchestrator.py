"""串接 Indexer → Auditor → Resolver → Compiler 的完整 pipeline

Pipeline flow:
  1. Index  — read .analysis/ files, build tag/refdes/connector indices
  2. Audit  — trace signals, detect lane swap, write schema
  3. Resolve — ask-me for ambiguities (if interactive)
  4. Compile — generate DTS from VERIFIED schema records
  5. Validate + Coverage — syntax check + coverage report
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .indexer import run_indexer
from .auditor import run_auditor
from .resolver import count_actionable_unresolved, run_resolver
from .compiler import run_compiler
from .tools.schema_ops import get_schema_summary
from .tools.compiler_tools import validate_dts_syntax, compute_coverage
from .issue_register import build_and_write_issue_register
from .validation import validate_dts_against_schema
from dtsbuild.schema_io import load_schema
from dtsbuild.session import create_session, save_session, SessionState

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────

def _elapsed(start: float) -> str:
    """Return a human-readable elapsed time string."""
    dt = time.monotonic() - start
    if dt < 1:
        return f"{dt * 1000:.0f}ms"
    return f"{dt:.1f}s"


def _log_schema_summary(schema_path: Path, phase: str) -> dict[str, Any]:
    """Log a schema summary after a phase and return the summary dict."""
    summary = get_schema_summary(schema_path=str(schema_path))
    sig = summary["signals"]
    dev = summary["devices"]
    cr = summary["clarifications"]
    logger.info(
        "[%s] Schema: %d signals (%d verified), %d devices, "
        "%d hints, %d clarifications (%d pending)",
        phase,
        sig["total"], sig["verified"],
        dev["total"],
        summary["dts_hints"],
        cr["total"], cr["pending"],
    )
    return summary


# ── Main async pipeline ─────────────────────────────────────────────

async def run_pipeline(
    project_dir: Path,
    output_dir: Path,
    interactive: bool = False,
    input_handler: Callable | None = None,
    resume: bool = True,
    session_id: str | None = None,
) -> Path:
    """
    執行完整的 4-agent DTS 生成 pipeline。

    Args:
        project_dir: dtsin_<project>/ 目錄
        output_dir: dtsout_<project>/ 目錄
        interactive: 是否啟用 ask-me 互動
        input_handler: CLI input handler（interactive=True 時必須提供）
        resume: 是否從上次中斷處續跑（default: True）
        session_id: 自訂 session ID

    Returns:
        產出的 DTS 檔案路徑

    Pipeline:
        1. Agent A (Indexer): 讀取 .analysis/ 建立索引
        2. Agent B (Auditor): 跟線追蹤，寫入 schema
        3. Agent C (Resolver): 處理歧義，ask-me（如果 interactive）
        4. Agent D (Compiler): 從 VERIFIED schema 產出 DTS
        5. Validate + Coverage: 語法驗證 + 覆蓋率報告
    """
    project_dir = Path(project_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    project_name = project_dir.name.replace("dtsin_", "")
    analysis_dir = project_dir / ".analysis"
    schema_path = output_dir / f"{project_name}.schema.yaml"

    pipeline_start = time.monotonic()
    reran_resolve = False
    reran_compile = False
    logger.info("=" * 60)
    logger.info("DTS Pipeline: %s", project_name)
    logger.info("  project_dir : %s", project_dir)
    logger.info("  output_dir  : %s", output_dir)
    logger.info("  interactive : %s", interactive)
    logger.info("=" * 60)

    # ── Session persistence ──────────────────────────────────────
    if resume:
        session = create_session(project_dir, output_dir, interactive, session_id)
    else:
        from dtsbuild.session import clear_session
        clear_session(output_dir)
        session = create_session(project_dir, output_dir, interactive, session_id)

    # ── Phase 1: Index ───────────────────────────────────────────
    phase_start = time.monotonic()
    if "index" not in session.completed_phases:
        session.mark_phase_started("index")
        save_session(session)
        logger.info("[1/5] Indexing .analysis/ files …")
        try:
            indices = await run_indexer(analysis_dir)
        except Exception as exc:
            session.mark_error(str(exc))
            save_session(session)
            logger.error("Phase 1 (Index) failed: %s", exc)
            raise RuntimeError(
                f"Indexing failed — check that {analysis_dir} exists and "
                f"contains *.txt files from PDF extraction."
            ) from exc

        n_pdfs = len(indices.get("page_indices", {}))
        n_tags = len(indices.get("tag_index", {}))
        n_refs = len(indices.get("refdes_index", {}))
        n_conns = len(indices.get("connector_index", {}))
        logger.info(
            "[1/5] Index done (%s): %d PDFs, %d tags, %d refdes, %d connectors",
            _elapsed(phase_start), n_pdfs, n_tags, n_refs, n_conns,
        )
        session.mark_phase_done("index")
        save_session(session)
    else:
        logger.info("[1/5] Index phase already completed — skipping")
        indices = await run_indexer(analysis_dir)

    # ── Phase 2: Audit ───────────────────────────────────────────
    phase_start = time.monotonic()
    if "audit" not in session.completed_phases:
        session.mark_phase_started("audit")
        save_session(session)
        logger.info("[2/5] Auditing signals (trace + lane-swap detection) …")
        gpio_table = project_dir / "tables" / "gpio_led.csv"
        try:
            await run_auditor(indices, gpio_table, schema_path)
        except Exception as exc:
            session.mark_error(str(exc))
            save_session(session)
            logger.error("Phase 2 (Audit) failed: %s", exc)
            raise RuntimeError(
                f"Audit failed — check that {gpio_table} exists and the "
                f"GPIO table CSV is well-formed."
            ) from exc

        audit_summary = _log_schema_summary(schema_path, "2/5 Audit")
        sig = audit_summary["signals"]
        logger.info(
            "[2/5] Audit done (%s): %d signals (%d verified), %d devices, %d hints",
            _elapsed(phase_start),
            sig["total"], sig["verified"],
            audit_summary["devices"]["total"],
            audit_summary["dts_hints"],
        )
        session.mark_phase_done("audit")
        session.schema_file = schema_path.name
        save_session(session)
    else:
        logger.info("[2/5] Audit phase already completed — skipping")

    # ── Phase 3: Resolve ─────────────────────────────────────────
    phase_start = time.monotonic()
    resolve_completed = "resolve" in session.completed_phases
    should_rerun_resolve = False
    if resolve_completed and schema_path.exists():
        should_rerun_resolve = count_actionable_unresolved(schema_path) > 0

    if not resolve_completed or should_rerun_resolve:
        session.mark_phase_started("resolve")
        save_session(session)
        if should_rerun_resolve:
            logger.info("[3/5] Resolving ambiguities … (actionable unresolved remain)")
        else:
            logger.info("[3/5] Resolving ambiguities …")
        try:
            if interactive and input_handler is None:
                raise ValueError("interactive=True but no input_handler provided")
            resolve_stats = await run_resolver(schema_path, input_handler)
        except Exception as exc:
            session.mark_error(str(exc))
            save_session(session)
            logger.error("Phase 3 (Resolve) failed: %s", exc)
            raise RuntimeError(
                f"Resolver failed — schema at {schema_path} may be corrupted."
            ) from exc

        logger.info(
            "[3/5] Resolve done (%s): %d clarifications asked, %d resolved",
            _elapsed(phase_start),
            resolve_stats.get("total", 0),
            resolve_stats.get("resolved", 0),
        )
        _log_schema_summary(schema_path, "3/5 Resolve")
        session.mark_phase_done("resolve")
        save_session(session)
        reran_resolve = True
    else:
        logger.info("[3/5] Resolve phase already completed — skipping")

    # ── Phase 4: Compile ─────────────────────────────────────────
    phase_start = time.monotonic()
    dts_path = output_dir / f"{project_name}.dts"
    compile_completed = "compile" in session.completed_phases
    if not compile_completed or reran_resolve:
        session.mark_phase_started("compile")
        save_session(session)
        if reran_resolve and compile_completed:
            logger.info("[4/5] Compiling DTS … (schema changed after resolve)")
        else:
            logger.info("[4/5] Compiling DTS …")
        ref_dts_dir = project_dir / "public_ref_dts"
        ref_dts_file = (
            next(ref_dts_dir.glob("*.dts"), None)
            if ref_dts_dir.exists() else None
        )
        try:
            dts_path = await run_compiler(schema_path, dts_path, ref_dts_file)
        except Exception as exc:
            session.mark_error(str(exc))
            save_session(session)
            logger.error("Phase 4 (Compile) failed: %s", exc)
            raise RuntimeError(
                f"DTS compilation failed — schema at {schema_path} may have "
                f"issues. Run resolver interactively to fix ambiguities."
            ) from exc

        dts_content = dts_path.read_text(encoding="utf-8")
        n_lines = dts_content.count("\n")
        logger.info(
            "[4/5] Compile done (%s): %d lines generated → %s",
            _elapsed(phase_start), n_lines, dts_path.name,
        )
        session.mark_phase_done("compile")
        session.dts_file = dts_path.name
        save_session(session)
        reran_compile = True
    else:
        logger.info("[4/5] Compile phase already completed — skipping")

    # ── Phase 5: Validate + Coverage ─────────────────────────────
    phase_start = time.monotonic()
    coverage_path = output_dir / f"{project_name}.coverage.json"
    val_report_path = output_dir / f"{project_name}.validation.json"
    validate_completed = "validate" in session.completed_phases
    if not validate_completed or reran_compile or reran_resolve:
        session.mark_phase_started("validate")
        save_session(session)
        if validate_completed:
            logger.info("[5/5] Validating DTS syntax and computing coverage … (refreshing reports)")
        else:
            logger.info("[5/5] Validating DTS syntax and computing coverage …")

        dts_content = dts_path.read_text(encoding="utf-8")
        validation = validate_dts_syntax(dts_content)
        if validation["valid"]:
            logger.info("[5/5] Syntax: OK")
        else:
            for err in validation.get("errors", []):
                logger.error("[5/5] Syntax error: %s", err)
        for warn in validation.get("warnings", []):
            logger.warning("[5/5] Syntax warning: %s", warn)

        schema = load_schema(schema_path)
        coverage = compute_coverage(schema, dts_content)
        coverage_pct = coverage.get("coverage_pct", 0.0)
        logger.info(
            "[5/5] Coverage: %.1f%% (%d/%d verified items covered)",
            coverage_pct,
            coverage.get("covered", 0),
            coverage.get("total_verified", 0),
        )

        # Write coverage report
        coverage_report = {
            "project": project_name,
            "dts_file": dts_path.name,
            "schema_file": schema_path.name,
            "validation": validation,
            "coverage": coverage,
        }
        coverage_path.write_text(
            json.dumps(coverage_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("[5/5] Coverage report → %s", coverage_path.name)

        # Structured validation report (schema vs DTS)
        val_report = validate_dts_against_schema(dts_path, schema_path)
        val_report_path.write_text(
            json.dumps(val_report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "[5/5] Validation: coverage=%.1f%%, issues=%d (%s)",
            val_report.coverage_pct, len(val_report.issues),
            "PASS" if val_report.passed else "FAIL",
        )
        logger.info("[5/5] Validation report → %s", val_report_path.name)

        logger.info(
            "[5/5] Validate + Coverage done (%s): syntax %s, %.1f%% coverage",
            _elapsed(phase_start),
            "OK" if validation["valid"] else "ERRORS",
            coverage_pct,
        )
        session.mark_phase_done("validate")
        session.coverage_file = coverage_path.name
        session.validation_file = val_report_path.name
        save_session(session)
    else:
        logger.info("[5/5] Validate + Coverage phase already completed — skipping")

    unresolved_path = output_dir / f"{project_name}.unresolved.json"
    issue_register = build_and_write_issue_register(
        schema_path=schema_path,
        output_path=unresolved_path,
        validation_path=val_report_path if val_report_path.exists() else None,
    )
    logger.info(
        "[5/5] Unresolved register → %s (trace-gap=%d, lookup-gap=%d, "
        "exclude-from-dts=%d, actionable=%d, informational=%d)",
        unresolved_path.name,
        issue_register.summary["by_bucket"]["trace-gap"],
        issue_register.summary["by_bucket"]["lookup-gap"],
        issue_register.summary["by_bucket"]["exclude-from-dts"],
        issue_register.summary["actionable_items"],
        issue_register.summary["informational_items"],
    )

    # ── Done ─────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info(
        "Pipeline complete (%s). Output files in %s:",
        _elapsed(pipeline_start), output_dir,
    )
    for f in sorted(output_dir.iterdir()):
        logger.info("  %s (%d bytes)", f.name, f.stat().st_size)
    logger.info("=" * 60)

    return dts_path


# ── Sync wrapper for CLI use ─────────────────────────────────────────

def run_pipeline_sync(
    project_dir: Path,
    output_dir: Path,
    interactive: bool = False,
    input_handler: Callable | None = None,
    resume: bool = True,
    session_id: str | None = None,
) -> Path:
    """Synchronous wrapper around :func:`run_pipeline` for CLI use.

    Args:
        project_dir: dtsin_<project>/ 目錄
        output_dir: dtsout_<project>/ 目錄
        interactive: 是否啟用 ask-me 互動
        input_handler: CLI input handler（interactive=True 時必須提供）
        resume: 是否從上次中斷處續跑（default: True）
        session_id: 自訂 session ID

    Returns:
        產出的 DTS 檔案路徑
    """
    return asyncio.run(
        run_pipeline(
            project_dir=project_dir,
            output_dir=output_dir,
            interactive=interactive,
            input_handler=input_handler,
            resume=resume,
            session_id=session_id,
        )
    )
