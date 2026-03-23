from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .bootstrap_tables import bootstrap_tables
from .discovery import bootstrap_manifest, discover_folder, format_discovery
from .generator import generate_dts
from .inspector import format_inspection, inspect_folder
from .manifest import load_manifest
from .scaffold import init_folder
from .spec import extract_board_spec
from .sufficiency import build_sufficiency_report


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging for CLI output."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(message)s" if not verbose else "%(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    _setup_logging(verbose=getattr(args, "verbose", False))

    if args.command == "init-folder":
        destination_root = Path(args.dest).resolve() if args.dest else Path.cwd()
        folder = init_folder(
            root=destination_root,
            project=args.project,
            profile=args.profile,
            refboard=args.refboard,
            family=args.family,
            model=args.model,
            base_include=args.base_include,
        )
        print(f"Created sample folder: {folder}")
        return 0

    if args.command == "inspect-folder":
        folder = Path(args.folder).resolve()
        try:
            result = inspect_folder(folder)
            print(format_inspection(result))
            return 1 if result.errors else 0
        except FileNotFoundError:
            discovery = discover_folder(folder)
            print(format_discovery(discovery))
            return 1

    if args.command == "bootstrap-manifest":
        manifest_path = bootstrap_manifest(Path(args.folder).resolve(), force=args.force)
        print(f"Bootstrapped manifest: {manifest_path}")
        return 0

    if args.command == "bootstrap-tables":
        result = bootstrap_tables(Path(args.folder).resolve(), force=args.force)
        generated = ", ".join(path.relative_to(result.folder).as_posix() for path in result.generated_tables.values())
        print(f"Bootstrapped evidence tables: {generated}")
        return 0

    if args.command == "extract-spec":
        folder = Path(args.folder).resolve()
        manifest = load_manifest(folder)
        spec = extract_board_spec(
            folder,
            manifest,
            backend=args.backend,
            model=args.model,
            cli_url=args.cli_url,
        )
        out_dir = manifest.resolve_output_dir(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        output = Path(args.output).resolve() if args.output else out_dir / f"{manifest.project}.spec.json"
        output.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Extracted normalized spec: {output}")
        return 0

    if args.command == "scan-sufficiency":
        folder = Path(args.folder).resolve()
        manifest = load_manifest(folder)
        spec = extract_board_spec(
            folder,
            manifest,
            backend=args.backend,
            model=args.model,
            cli_url=args.cli_url,
        )
        report = build_sufficiency_report(folder, manifest, spec)
        out_dir = manifest.resolve_output_dir(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        output = Path(args.output).resolve() if args.output else out_dir / f"{manifest.project}.sufficiency.json"
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote sufficiency report: {output}")
        return 0 if report.get("ready_to_generate") else 1

    if args.command == "generate-dts":
        if args.pipeline == "agents":
            return _run_agents_pipeline(args)

        # legacy pipeline
        output_path = Path(args.output).resolve() if args.output else None
        generated = generate_dts(
            Path(args.folder).resolve(),
            output_path=output_path,
            backend=args.backend,
            model=args.model,
            cli_url=args.cli_url,
        )
        print(f"Generated DTS draft: {generated}")
        return 0

    if args.command == "audit-schema":
        return _run_audit_schema(args)

    if args.command == "calibrate-dts":
        return _run_calibration_workflow(args)

    if args.command == "clear-session":
        from .session import clear_session
        folder = Path(args.folder).resolve()
        # Accept either dtsin_<project> (resolve to output) or dtsout_<project> directly
        manifest_path = folder / "manifest.yaml"
        if manifest_path.exists():
            manifest = load_manifest(folder)
            output_dir = manifest.resolve_output_dir(folder)
        else:
            output_dir = folder
        if clear_session(output_dir):
            print(f"Session state cleared: {output_dir}")
        else:
            print(f"No session state found in: {output_dir}")
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


def _run_agents_pipeline(args: argparse.Namespace) -> int:
    """Run the 4-agent pipeline (Indexer → Auditor → Resolver → Compiler)."""
    from .agents.orchestrator import run_pipeline
    from .askme import create_cli_handler, create_non_interactive_handler

    folder = Path(args.folder).resolve()
    manifest = load_manifest(folder)
    output_dir = manifest.resolve_output_dir(folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    interactive = args.interactive
    handler = None
    log_path = (
        Path(args.answer_log).resolve()
        if args.answer_log
        else output_dir / f"{manifest.project}.answers.json"
    )

    if interactive:
        handler, _answer_log = create_cli_handler(log_path=log_path)
    else:
        handler, _answer_log = create_non_interactive_handler(log_path=log_path)

    dts_path = asyncio.run(
        run_pipeline(
            project_dir=folder,
            output_dir=output_dir,
            interactive=interactive,
            input_handler=handler,
            resume=getattr(args, "resume", True),
            session_id=getattr(args, "session_id", None),
        )
    )
    print(f"Generated DTS (agents pipeline): {dts_path}")
    return 0


def _run_audit_schema(args: argparse.Namespace) -> int:
    """Run Indexer + Auditor only — inspect schema without full compilation."""
    from .agents.indexer import run_indexer_sync
    from .agents.auditor import run_auditor

    folder = Path(args.folder).resolve()
    analysis_dir = folder / ".analysis"

    indices = run_indexer_sync(analysis_dir)

    project_name = folder.name.replace("dtsin_", "")
    schema_path = (
        Path(args.output).resolve()
        if args.output
        else folder / f"{project_name}.schema.yaml"
    )

    gpio_table = folder / "tables" / "gpio_led.csv"
    asyncio.run(run_auditor(indices, gpio_table, schema_path))

    print(f"Audit schema written: {schema_path}")
    return 0


def _run_calibration_workflow(args: argparse.Namespace) -> int:
    """Build refdiff + calibration sidecars from generated DTS artifacts."""
    from .agents.calibration_workflow import run_calibration_workflow

    paths = run_calibration_workflow(
        project_dir=Path(args.folder).resolve(),
        reference_dts=Path(args.reference).resolve() if args.reference else None,
        refdiff_output=Path(args.refdiff_output).resolve() if args.refdiff_output else None,
        calibration_output=(
            Path(args.calibration_output).resolve()
            if args.calibration_output else None
        ),
    )
    print(f"Calibration refdiff: {paths['refdiff']}")
    print(f"Calibration log   : {paths['calibration']}")
    print(f"Reference DTS     : {paths['reference_dts']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dtsbuild",
        description="Generate DTS from hardware evidence. For local development, prefer `python -m dtsbuild ...`.",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="enable debug-level logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_cmd = subparsers.add_parser("init-folder", help="create a sample input folder")
    init_cmd.add_argument("project", help="project label used in dtsin_<project>")
    init_cmd.add_argument("--dest", help="root directory where the folder will be created")
    init_cmd.add_argument("--profile", default="", help="profile string stored in manifest.yaml")
    init_cmd.add_argument("--refboard", default="", help="reference board stored in manifest.yaml")
    init_cmd.add_argument("--family", default="bcm68575", help="chip family")
    init_cmd.add_argument("--model", default=None, help="DTS model string")
    init_cmd.add_argument("--base-include", default=None, help="include line for the generated DTS")

    inspect_cmd = subparsers.add_parser("inspect-folder", help="inspect a diff-folder")
    inspect_cmd.add_argument("folder", help="path to dtsin_<project>")

    bootstrap_cmd = subparsers.add_parser("bootstrap-manifest", help="create a manifest from a raw folder")
    bootstrap_cmd.add_argument("folder", help="path to dtsin_<project>")
    bootstrap_cmd.add_argument("--force", action="store_true", help="overwrite an existing manifest")

    bootstrap_tables_cmd = subparsers.add_parser("bootstrap-tables", help="generate normalized tables from raw PDFs/XLSX")
    bootstrap_tables_cmd.add_argument("folder", help="path to dtsin_<project>")
    bootstrap_tables_cmd.add_argument("--force", action="store_true", help="overwrite generated tables if they already exist")

    extract_cmd = subparsers.add_parser("extract-spec", help="extract normalized DTS spec from a diff-folder")
    extract_cmd.add_argument("folder", help="path to dtsin_<project>")
    extract_cmd.add_argument("--output", help="optional output path for the normalized spec JSON")
    extract_cmd.add_argument("--backend", default="auto", choices=["auto", "agent", "manual"], help="spec extraction backend")
    extract_cmd.add_argument("--model", default="gpt-4.1", help="Copilot model for agent backend")
    extract_cmd.add_argument("--cli-url", help="connect to an existing Copilot CLI server")

    sufficiency_cmd = subparsers.add_parser("scan-sufficiency", help="scan whether current evidence is sufficient for DTS generation")
    sufficiency_cmd.add_argument("folder", help="path to dtsin_<project>")
    sufficiency_cmd.add_argument("--output", help="optional output path for the sufficiency report JSON")
    sufficiency_cmd.add_argument("--backend", default="auto", choices=["auto", "agent", "manual"], help="spec extraction backend")
    sufficiency_cmd.add_argument("--model", default="gpt-4.1", help="Copilot model for agent backend")
    sufficiency_cmd.add_argument("--cli-url", help="connect to an existing Copilot CLI server")

    generate_cmd = subparsers.add_parser(
        "generate-dts",
        help="generate DTS plus schema/validation/coverage/unresolved artifacts",
        description=(
            "Generate a DTS draft. With --pipeline agents, the pipeline also writes "
            "<project>.schema.yaml, <project>.validation.json, <project>.coverage.json, "
            "and <project>.unresolved.json into dtsout_<project> for final review."
        ),
    )
    generate_cmd.add_argument("folder", help="path to dtsin_<project>")
    generate_cmd.add_argument("--output", help="optional output path for the DTS draft")
    generate_cmd.add_argument("--backend", default="auto", choices=["auto", "agent", "manual"], help="spec extraction backend")
    generate_cmd.add_argument("--model", default="gpt-4.1", help="Copilot model for agent backend")
    generate_cmd.add_argument("--cli-url", help="connect to an existing Copilot CLI server")
    generate_cmd.add_argument(
        "--pipeline", default="legacy", choices=["legacy", "agents"],
        help="DTS 生成 pipeline (default: legacy)",
    )
    generate_cmd.add_argument(
        "--interactive", action="store_true",
        help="啟用 ask-me 互動模式（Agent C 會向使用者提問）",
    )
    generate_cmd.add_argument(
        "--answer-log", metavar="PATH",
        help="使用者回答記錄檔路徑 (default: <output_dir>/<project>.answers.json)",
    )
    generate_cmd.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True,
        help="resume from last interrupted session (default: True; use --no-resume for a fresh rerun, often after clear-session)",
    )
    generate_cmd.add_argument(
        "--session-id", metavar="ID",
        help="custom session identifier",
    )

    audit_cmd = subparsers.add_parser("audit-schema", help="run indexer + auditor to inspect schema without compilation")
    audit_cmd.add_argument("folder", help="path to dtsin_<project>")
    audit_cmd.add_argument("--output", help="output path for the schema YAML")

    calibrate_cmd = subparsers.add_parser(
        "calibrate-dts",
        help="build refdiff + calibration sidecars from generated artifacts",
        description=(
            "Compare generated DTS against a reference DTS without mutating the "
            "generated output. Writes <project>.refdiff.json and "
            "<project>.calibration.json for evidence-gated calibration review."
        ),
    )
    calibrate_cmd.add_argument("folder", help="path to dtsin_<project>")
    calibrate_cmd.add_argument(
        "--reference",
        help="explicit path to the reference DTS; if omitted, auto-discover from output_dir/public_ref_dts",
    )
    calibrate_cmd.add_argument(
        "--refdiff-output",
        help="optional output path for the refdiff JSON artifact",
    )
    calibrate_cmd.add_argument(
        "--calibration-output",
        help="optional output path for the calibration decision log JSON artifact",
    )

    clear_session_cmd = subparsers.add_parser(
        "clear-session",
        help="remove saved session state for dtsin_<project> or dtsout_<project>",
        description=(
            "Remove saved session state before a fresh rerun. Accepts either "
            "dtsin_<project> (resolved via manifest.yaml) or dtsout_<project> directly."
        ),
    )
    clear_session_cmd.add_argument("folder", help="path to dtsin_<project> or dtsout_<project>")

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
