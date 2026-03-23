"""Serialisation helpers for :class:`HardwareSchema`.

Supports YAML (.yaml / .yml) and JSON (.json) formats.
Format is auto-detected from the file extension on load;
on save it defaults to YAML but can be overridden.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from dtsbuild.schema import HardwareSchema

_YAML_EXTS = {".yaml", ".yml"}
_JSON_EXTS = {".json"}


def save_schema(
    schema: HardwareSchema,
    path: Path,
    format: str = "yaml",
) -> None:
    """Persist a *HardwareSchema* to disk.

    Parameters
    ----------
    schema:
        The schema instance to serialise.
    path:
        Destination file path.
    format:
        ``"yaml"`` (default) or ``"json"``.  When ``"yaml"``, the file is
        written with PyYAML; when ``"json"``, with the stdlib ``json`` module.
    """
    path = Path(path)
    data = schema.model_dump(mode="python")

    if format == "json":
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    elif format == "yaml":
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        )
    else:
        raise ValueError(f"Unsupported format {format!r}; use 'yaml' or 'json'")


def load_schema(path: Path) -> HardwareSchema:
    """Load a *HardwareSchema* from a YAML or JSON file.

    The format is detected from the file extension (``.yaml`` / ``.yml`` →
    YAML, ``.json`` → JSON).
    """
    path = Path(path)
    ext = path.suffix.lower()
    text = path.read_text()

    if ext in _YAML_EXTS:
        data = yaml.safe_load(text)
    elif ext in _JSON_EXTS:
        data = json.loads(text)
    else:
        raise ValueError(
            f"Cannot detect format from extension {ext!r}; "
            "use .yaml, .yml, or .json"
        )

    return HardwareSchema.model_validate(data)
