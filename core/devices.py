"""Hardware device helpers for companion context (MQTT/serial stub)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths
from . import vault_index


def list_bound_devices(project_id: str | None) -> list[dict[str, Any]]:
    """Return devices bound to a project from manifest (preferred) or SQLite."""
    if not project_id:
        return []

    mpath = paths.PROJECTS_DIR / project_id / "manifest.json"
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            devices = (manifest.get("hardware") or {}).get("devices") or []
            if isinstance(devices, list):
                return [d for d in devices if isinstance(d, dict)]
        except (json.JSONDecodeError, OSError):
            pass

    vault_index.ensure_tables()
    db = vault_index.get_db()
    try:
        rows = db.execute(
            """
            SELECT device_id, project_id, type, chip, protocol, capabilities
            FROM devices WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
    except Exception:
        db.close()
        return []
    db.close()
    out = []
    for device_id, pid, dtype, chip, protocol, caps in rows:
        try:
            caps_val = json.loads(caps) if caps and caps.startswith("[") else caps
        except (json.JSONDecodeError, TypeError):
            caps_val = caps
        out.append(
            {
                "device_id": device_id,
                "project_id": pid,
                "type": dtype,
                "chip": chip,
                "protocol": protocol,
                "capabilities": caps_val,
            }
        )
    return out


def describe_device(device: dict[str, Any]) -> str:
    """Human-readable one-liner for companion prompt injection."""
    device_id = device.get("device_id") or device.get("id") or device.get("name") or "device"
    dtype = device.get("type") or "unknown"
    chip = device.get("chip") or "?"
    protocol = device.get("protocol") or "?"
    caps = device.get("capabilities")
    if isinstance(caps, list):
        caps_str = ", ".join(str(c) for c in caps)
    elif caps:
        caps_str = str(caps)
    else:
        caps_str = "none"
    return f"{device_id}: type={dtype}, chip={chip}, protocol={protocol}, capabilities=[{caps_str}]"


def send_command(device_id: str, payload: Any) -> None:
    """Placeholder for future MQTT/serial transport.

    Raises NotImplementedError until firmware glue is wired.
    """
    raise NotImplementedError(
        f"send_command({device_id!r}, ...) is not implemented yet — "
        "wire MQTT/serial here when adding robot/glasses firmware."
    )
