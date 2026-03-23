from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Manifest, load_manifest
from .tables import read_table_rows


REQUIRED_MANIFEST_FIELDS = ("project", "family", "model", "output_dts")
RECOMMENDED_MANIFEST_FIELDS = ("profile", "refboard", "base_include", "compatible")


@dataclass
class InspectionResult:
    manifest: Manifest
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifact_status: dict[str, str] = field(default_factory=dict)
    table_row_counts: dict[str, int] = field(default_factory=dict)


def inspect_folder(folder: Path) -> InspectionResult:
    manifest = load_manifest(folder)
    result = InspectionResult(manifest=manifest)

    for field_name in REQUIRED_MANIFEST_FIELDS:
        value = getattr(manifest, field_name)
        if not value:
            result.errors.append(f"missing required manifest field: {field_name}")

    for field_name in RECOMMENDED_MANIFEST_FIELDS:
        value = getattr(manifest, field_name)
        if not value:
            result.warnings.append(f"missing recommended manifest field: {field_name}")

    for artifact_name, artifact_paths in manifest.resolve_artifacts(folder).items():
        existing_paths = [path for path in artifact_paths if path.exists()]
        result.artifact_status[artifact_name] = f"{len(existing_paths)}/{len(artifact_paths)} found"

        for artifact_path in artifact_paths:
            if artifact_path.exists():
                if artifact_path.suffix.lower() in {".csv", ".xlsx", ".xlsm"}:
                    try:
                        result.table_row_counts[artifact_name] = len(read_table_rows(artifact_path))
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append(f"failed to parse {artifact_name}: {exc}")
            else:
                result.warnings.append(f"artifact missing: {artifact_name} -> {artifact_path.name}")

    return result


def format_inspection(result: InspectionResult) -> str:
    lines = [
        f"Project: {result.manifest.project}",
        f"Family: {result.manifest.family}",
        f"Profile: {result.manifest.profile or '<missing>'}",
        f"Refboard: {result.manifest.refboard or '<missing>'}",
        "",
        "Artifacts:",
    ]

    for artifact_name, status in sorted(result.artifact_status.items()):
        row_count = result.table_row_counts.get(artifact_name)
        suffix = f" ({row_count} rows)" if row_count is not None else ""
        lines.append(f"  - {artifact_name}: {status}{suffix}")

    if result.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend([f"  - {warning}" for warning in result.warnings])

    if result.errors:
        lines.extend(["", "Errors:"])
        lines.extend([f"  - {error}" for error in result.errors])
    else:
        lines.extend(["", "Status: OK"])

    return "\n".join(lines)
