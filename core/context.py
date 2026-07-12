"""Build companion system prompts from active project manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import devices as devices_mod
from . import paths

CAD_EXTENSIONS = {".step", ".stp", ".stl", ".obj", ".iges", ".igs", ".dxf", ".dwg", ".3mf"}
KEY_FILE_LIMIT = 40


def load_manifest(project_id: str | None) -> dict[str, Any] | None:
    if not project_id:
        return None
    mpath = paths.PROJECTS_DIR / project_id / "manifest.json"
    if not mpath.exists():
        return None
    try:
        return json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def project_dir(project_id: str) -> Path:
    return paths.PROJECTS_DIR / project_id


def _list_key_files(proj: Path) -> list[str]:
    """CAD and other notable files under the project directory."""
    found: list[str] = []
    if not proj.is_dir():
        return found
    for p in sorted(proj.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(proj).as_posix()
        if p.suffix.lower() in CAD_EXTENSIONS or rel.startswith(("cad/", "firmware/", "sim/")):
            found.append(rel)
        if len(found) >= KEY_FILE_LIMIT:
            break
    return found


def build_prompt(project_id: str | None = None, *, include_tools: bool = True) -> str:
    """Build the companion system prompt for the active project.

    When a project is selected, injects name/description/status/tags, devices,
    bound models/adapters, dataset paths, key CAD/file listing, and the
    standing engineering-companion instruction.
    """
    lines = [
        "You are the user's engineering companion for Motherbrain.",
        "Use tools to inspect the machine when needed.",
        "Prefer concrete, actionable answers about hardware, firmware, CAD, and datasets.",
    ]

    if include_tools:
        from .tools import TOOL_HINT

        lines.append("")
        lines.append(TOOL_HINT.strip())

    manifest = load_manifest(project_id)
    if not manifest:
        lines.append("")
        lines.append("No project is currently selected.")
        return "\n".join(lines)

    proj = manifest.get("project") or {}
    name = proj.get("name") or project_id
    desc = proj.get("description") or ""
    status = proj.get("status") or "unknown"
    tags = proj.get("tags") or []
    tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

    lines.extend(
        [
            "",
            f"Active project: {name} ({project_id})",
            f"Status: {status}",
            f"Description: {desc or '(none)'}",
            f"Tags: {tags_str or '(none)'}",
            f"Standing instruction: you are the user's engineering companion for this project; "
            f"use tools to inspect the machine when needed.",
        ]
    )

    # Devices
    hw_devices = (manifest.get("hardware") or {}).get("devices") or []
    lines.append("")
    lines.append("Devices:")
    if hw_devices:
        for d in hw_devices:
            lines.append(f"  - {devices_mod.describe_device(d)}")
    else:
        lines.append("  (none bound)")

    # Bound AI models / adapters
    ai = manifest.get("ai") or {}
    models = ai.get("models") or []
    lines.append("")
    lines.append("Bound AI models / adapters:")
    if models:
        for m in models:
            mid = m.get("model_id") or m.get("id") or m.get("name") or "?"
            role = m.get("role") or "general"
            base = m.get("base_model") or ""
            adapter = m.get("lora_adapter_path") or m.get("adapter") or ""
            extra = []
            if base:
                extra.append(f"base={base}")
            if adapter:
                extra.append(f"adapter={adapter}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  - {mid} [{role}]{suffix}")
    else:
        lines.append("  (none)")

    # Dataset paths
    collections = (manifest.get("datasets") or {}).get("collections") or []
    lines.append("")
    lines.append("Datasets:")
    if collections:
        for c in collections:
            cname = c.get("name") or "?"
            cpath = c.get("path") or ""
            fmt = c.get("format") or ""
            lines.append(f"  - {cname}: {cpath}" + (f" ({fmt})" if fmt else ""))
    else:
        lines.append("  (none)")

    # Key files
    pdir = project_dir(str(project_id))
    key_files = _list_key_files(pdir)
    lines.append("")
    lines.append(f"Project directory: {pdir}")
    lines.append("Key files:")
    if key_files:
        for rel in key_files:
            lines.append(f"  - {rel}")
    else:
        lines.append("  (none indexed)")

    return "\n".join(lines)


def build_chat_prompt(
    user_text: str,
    *,
    project_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    history_limit: int = 6,
    media_note: str = "",
    include_tools: bool = True,
) -> str:
    """Assemble system + recent turns + user message for /completion."""
    system = build_prompt(project_id, include_tools=include_tools)
    parts = [system, ""]
    for turn in (history or [])[-history_limit:]:
        parts.append(f"User: {turn.get('user', '')}")
        parts.append(f"Assistant: {turn.get('ai', '')}")
    if media_note:
        parts.append(media_note.rstrip())
    parts.append(f"User: {user_text}")
    parts.append("Assistant:")
    return "\n".join(parts)
