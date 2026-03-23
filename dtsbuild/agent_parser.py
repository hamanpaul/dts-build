from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .manifest import Manifest
from .tables import read_table_rows


class AgentExtractionError(RuntimeError):
    """Raised when agent-backed extraction cannot complete."""


def extract_spec_with_agent(
    folder: Path,
    manifest: Manifest,
    *,
    model: str = "gpt-4.1",
    cli_url: str | None = None,
) -> dict[str, Any]:
    try:
        return asyncio.run(_extract_spec_with_agent_async(folder, manifest, model=model, cli_url=cli_url))
    except AgentExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AgentExtractionError(f"agent extraction failed: {exc}") from exc


async def _extract_spec_with_agent_async(
    folder: Path,
    manifest: Manifest,
    *,
    model: str,
    cli_url: str | None,
) -> dict[str, Any]:
    try:
        from copilot import CopilotClient, PermissionHandler
    except ImportError as exc:
        raise AgentExtractionError(
            "github-copilot-sdk is not installed. Install it with `python -m pip install -r requirements-agent.txt`."
        ) from exc

    client_options: dict[str, Any] = {}
    if cli_url:
        client_options["cli_url"] = cli_url

    client = CopilotClient(client_options or None)
    try:
        await client.start()
        session = await client.create_session(
            {
                "model": model,
                "streaming": False,
                "on_permission_request": PermissionHandler.approve_all,
                "system_message": {
                    "content": _build_system_message(),
                },
                "custom_agents": [
                    {
                        "name": "dts-spec-extractor",
                        "display_name": "DTS Spec Extractor",
                        "description": "Converts board delta artifacts into a canonical DTS spec",
                        "prompt": _build_system_message(),
                    }
                ],
            }
        )
        message = {
            "prompt": _build_prompt(folder, manifest),
            "attachments": _build_attachments(folder, manifest),
        }
        response = await session.send_and_wait(message)
    finally:
        await client.stop()

    content = getattr(getattr(response, "data", None), "content", None)
    if not content:
        raise AgentExtractionError("agent returned an empty response")

    return _parse_agent_json(content)


def _build_system_message() -> str:
    return (
        "You extract canonical Broadcom DTS input specs from spreadsheets and schematics. "
        "Do not invent exact hardware values. When information is missing, leave the field empty and "
        "record it under missing_fields. Return JSON only."
    )


def _build_prompt(folder: Path, manifest: Manifest) -> str:
    artifact_context = _build_artifact_context(folder, manifest)
    return f"""
Task:
Analyze the provided project artifacts for a Broadcom DTS generation workflow and convert them into a canonical JSON spec.

Project metadata:
- project: {manifest.project}
- family: {manifest.family}
- profile: {manifest.profile or "unknownprofile"}
- refboard: {manifest.refboard or "unknownrefboard"}
- model: {manifest.model}
- base_include: {manifest.base_include or "inc/68375.dtsi"}
- compatible: {manifest.compatible or "unknown"}

Important rules:
- Return JSON only. No markdown fences.
- If a value is not present in the artifacts, use an empty string or empty list.
- Do not guess `profile`, `refboard`, or exact memcfg macros.
- If `public_ref_dts` is present, treat it as a public pattern source only, never as a board-DTS answer key.
- Preserve source wording from the artifacts when useful in `notes`.
- Put unresolved questions into `missing_fields`.

Required JSON schema:
{{
  "meta": {{
    "project": "{manifest.project}",
    "family": "{manifest.family}",
    "profile": "{manifest.profile or "unknownprofile"}",
    "refboard": "{manifest.refboard or "unknownrefboard"}"
  }},
  "public_reference": {{
    "path": "",
    "exists": false,
    "model": "",
    "memcfg_macro": "",
    "patterns": [],
    "compatibles": [],
    "notes": []
  }},
  "memory": {{
    "memcfg_macro": "",
    "notes": []
  }},
  "network": {{
    "rows": [
      {{
        "name": "",
        "present": "",
        "role": "",
        "source": "",
        "phy_handle": "",
        "phy_mode": "",
        "notes": ""
      }}
    ]
  }},
  "gpio": {{
    "rows": [
      {{
        "category": "",
        "name": "",
        "signal": "",
        "pin_or_gpio": "",
        "polarity": "",
        "io": "",
        "notes": ""
      }}
    ]
  }},
  "missing_fields": [],
  "assumptions": []
}}

Artifact inventory and raw previews:
{artifact_context}
""".strip()


def _build_attachments(folder: Path, manifest: Manifest) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for artifact_paths in manifest.resolve_artifacts(folder).values():
        for path in artifact_paths:
            if path.exists():
                attachments.append(
                    {
                        "type": "file",
                        "path": str(path),
                        "displayName": path.name,
                    }
                )
    return attachments


def _build_artifact_context(folder: Path, manifest: Manifest) -> str:
    sections: list[str] = []
    for artifact_name, paths in manifest.resolve_artifacts(folder).items():
        for path in paths:
            sections.append(f"- artifact: {artifact_name} -> {path.name}")
            if not path.exists():
                sections.append("  missing: true")
                continue
            if path.suffix.lower() in {".csv", ".xlsx", ".xlsm"}:
                preview = _table_preview(path)
                sections.append("  preview:")
                for line in preview.splitlines():
                    sections.append(f"    {line}")
            elif path.suffix.lower() == ".dts":
                sections.append("  preview:")
                for line in _text_preview(path).splitlines():
                    sections.append(f"    {line}")
    return "\n".join(sections)


def _table_preview(path: Path, max_rows: int = 25) -> str:
    rows = read_table_rows(path)
    if not rows:
        return "<empty table>"
    lines: list[str] = []
    for row in rows[:max_rows]:
        parts = [f"{key}={value}" for key, value in row.items() if value]
        if parts:
            lines.append("; ".join(parts))
    if len(rows) > max_rows:
        lines.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join(lines) if lines else "<no populated rows>"


def _text_preview(path: Path, max_lines: int = 25) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    preview = lines[:max_lines]
    if len(lines) > max_lines:
        preview.append(f"... {len(lines) - max_lines} more lines")
    return "\n".join(preview) if preview else "<empty file>"


def _parse_agent_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AgentExtractionError(f"agent did not return valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise AgentExtractionError("agent response JSON root must be an object")

    parsed.setdefault("meta", {})
    parsed.setdefault("public_reference", {})
    parsed.setdefault("memory", {})
    parsed.setdefault("network", {"rows": []})
    parsed.setdefault("gpio", {"rows": []})
    parsed.setdefault("missing_fields", [])
    parsed.setdefault("assumptions", [])
    parsed["meta"]["backend"] = "agent"
    return parsed
