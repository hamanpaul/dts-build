from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


FAMILY_DEFAULTS: dict[str, dict[str, str]] = {
    "bcm68575": {
        "base_include": "inc/68375.dtsi",
        "compatible": "brcm,bcm968375",
    }
}


@dataclass
class Manifest:
    project: str
    family: str
    profile: str
    refboard: str
    model: str
    output_dts: str
    output_dir: str
    base_include: str
    compatible: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Manifest":
        family = str(raw.get("family", "bcm68575")).strip()
        defaults = FAMILY_DEFAULTS.get(family, {})
        project = str(raw.get("project", "")).strip()
        if not project:
            raise ValueError("manifest field 'project' is required")

        model = str(raw.get("model", project)).strip() or project
        output_dts = str(raw.get("output_dts", f"{project}.dts")).strip() or f"{project}.dts"
        output_dir = str(raw.get("output_dir", f"dtsout_{project}")).strip() or f"dtsout_{project}"
        base_include = str(raw.get("base_include", defaults.get("base_include", ""))).strip()
        compatible = str(raw.get("compatible", defaults.get("compatible", ""))).strip()
        artifacts = raw.get("artifacts") or {}
        if not isinstance(artifacts, dict):
            raise ValueError("manifest field 'artifacts' must be a mapping")

        notes = raw.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list):
            raise ValueError("manifest field 'notes' must be a list")

        return cls(
            project=project,
            family=family,
            profile=str(raw.get("profile", "")).strip(),
            refboard=str(raw.get("refboard", "")).strip(),
            model=model,
            output_dts=output_dts,
            output_dir=output_dir,
            base_include=base_include,
            compatible=compatible,
            artifacts={
                str(k): [str(item) for item in v] if isinstance(v, list) else str(v)
                for k, v in artifacts.items()
            },
            notes=[str(note) for note in notes],
        )

    def resolve_artifacts(self, folder: Path) -> dict[str, list[Path]]:
        resolved: dict[str, list[Path]] = {}
        for name, rel_path in self.artifacts.items():
            if isinstance(rel_path, list):
                resolved[name] = [(folder / item).resolve() for item in rel_path]
            else:
                resolved[name] = [(folder / rel_path).resolve()]
        return resolved

    def resolve_output_dir(self, folder: Path) -> Path:
        path = Path(self.output_dir)
        if path.is_absolute():
            return path
        return (folder.parent / path).resolve()


def load_manifest(folder: Path) -> Manifest:
    manifest_path = folder / "manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError("manifest root must be a mapping")

    return Manifest.from_dict(raw)


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=False, sort_keys=False)
