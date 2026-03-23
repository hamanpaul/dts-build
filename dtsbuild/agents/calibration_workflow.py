"""Non-destructive calibration workflow entrypoints."""

from __future__ import annotations

from pathlib import Path

from dtsbuild.manifest import load_manifest

from .calibration import CalibrationLog, write_calibration_log
from .calibration_triage import triage_refdiff_report
from .refdiff import build_refdiff_report, write_refdiff_report


def run_calibration_workflow(
    project_dir: Path,
    reference_dts: Path | None = None,
    refdiff_output: Path | None = None,
    calibration_output: Path | None = None,
) -> dict[str, Path]:
    """Build calibration sidecars from existing generated artifacts.

    This workflow is intentionally non-destructive: it does not patch DTS output
    or rerun generation. It prepares the review artifacts that a later apply
    phase can consume.
    """
    project_dir = Path(project_dir).resolve()
    manifest = load_manifest(project_dir)
    output_dir = manifest.resolve_output_dir(project_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated_dts = output_dir / manifest.output_dts
    schema_path = output_dir / f"{manifest.project}.schema.yaml"
    validation_path = output_dir / f"{manifest.project}.validation.json"
    unresolved_path = output_dir / f"{manifest.project}.unresolved.json"
    reference_path = _resolve_reference_path(
        project_dir=project_dir,
        output_dir=output_dir,
        generated_dts=generated_dts,
        reference_dts=reference_dts,
    )

    for required in (generated_dts, schema_path, validation_path, unresolved_path, reference_path):
        if not required.exists():
            raise FileNotFoundError(f"required calibration artifact not found: {required}")

    refdiff_path = (
        Path(refdiff_output).resolve()
        if refdiff_output
        else output_dir / f"{manifest.project}.refdiff.json"
    )
    calibration_path = (
        Path(calibration_output).resolve()
        if calibration_output
        else output_dir / f"{manifest.project}.calibration.json"
    )

    report = build_refdiff_report(
        project=manifest.project,
        generated_dts_path=generated_dts,
        reference_dts_path=reference_path,
        schema_path=schema_path,
        validation_path=validation_path,
        unresolved_path=unresolved_path,
    )
    triaged = triage_refdiff_report(
        report,
        schema_path=schema_path,
        validation_path=validation_path,
        unresolved_path=unresolved_path,
    )
    write_refdiff_report(triaged, refdiff_path)

    log = CalibrationLog(
        project=manifest.project,
        refdiff_path=str(refdiff_path),
        schema_path=str(schema_path),
    )
    write_calibration_log(log, calibration_path)

    return {
        "output_dir": output_dir,
        "generated_dts": generated_dts,
        "reference_dts": reference_path,
        "schema": schema_path,
        "validation": validation_path,
        "unresolved": unresolved_path,
        "refdiff": refdiff_path,
        "calibration": calibration_path,
    }


def _resolve_reference_path(
    project_dir: Path,
    output_dir: Path,
    generated_dts: Path,
    reference_dts: Path | None,
) -> Path:
    if reference_dts:
        return Path(reference_dts).resolve()

    output_candidates = sorted(
        candidate.resolve()
        for candidate in output_dir.glob("*.dts")
        if candidate.resolve() != generated_dts.resolve()
    )
    if len(output_candidates) == 1:
        return output_candidates[0]
    if len(output_candidates) > 1:
        raise FileNotFoundError(
            "multiple reference DTS candidates found in output directory; pass --reference explicitly"
        )

    ref_dir = project_dir / "public_ref_dts"
    ref_candidates = sorted(candidate.resolve() for candidate in ref_dir.glob("*.dts"))
    if len(ref_candidates) == 1:
        return ref_candidates[0]
    if len(ref_candidates) > 1:
        raise FileNotFoundError(
            "multiple public_ref_dts candidates found; pass --reference explicitly"
        )

    raise FileNotFoundError(
        "no reference DTS found; pass --reference or place exactly one non-generated *.dts artifact in the output directory"
    )
