"""Model registry listing, disk GGUF discovery, active model + presets."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import model_catalog
from . import paths
from . import vault_index

# Backward-compatible preset view; model_catalog is the source of truth.
PRESETS: dict[str, dict[str, str]] = {
    entry.id: {
        "label": entry.label,
        "repo": entry.repo_id or "",
        "quant": entry.quantization or "",
        "filename": entry.filename or "",
    }
    for entry in model_catalog.CURATED_CATALOG
    if entry.available
}


def list_disk_models(models_dir: Path | None = None) -> list[dict[str, Any]]:
    """List GGUF files present under shared/base_models."""
    root = models_dir or paths.MODELS_DIR
    root.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.gguf")):
        out.append(
            {
                "id": p.stem,
                "filename": p.name,
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "source": "disk",
            }
        )
    return out


def list_registry() -> list[dict[str, Any]]:
    """List models from SQLite model_registry (empty if table missing)."""
    vault_index.ensure_tables()
    db = sqlite3.connect(str(paths.VAULT_DB), timeout=5)
    try:
        rows = db.execute(
            """
            SELECT id, name, source, file_path, quantization, size_bytes,
                   downloaded_at, base_model, role, repo_id, revision,
                   file_name, sha256, license, publisher, provenance,
                   verified, status, metadata_json
            FROM model_registry
            ORDER BY downloaded_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return []
    db.close()
    result = []
    for r in rows:
        result.append(
            {
                "id": r[0],
                "name": r[1],
                "source": r[2],
                "file_path": r[3],
                "quantization": r[4],
                "size_bytes": r[5],
                "downloaded_at": r[6],
                "base_model": r[7],
                "role": r[8],
                "repo_id": r[9],
                "revision": r[10],
                "file_name": r[11],
                "sha256": r[12],
                "license": r[13],
                "publisher": r[14],
                "provenance": r[15],
                "verified": bool(r[16]),
                "status": r[17] or "ready",
                "metadata": _json_object(r[18]),
            }
        )
    return result


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _model_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:24]


def register_model(
    *,
    file_path: Path | str,
    name: str | None = None,
    source: str = "local",
    quantization: str | None = None,
    size_bytes: int | None = None,
    base_model: str | None = None,
    role: str = "general",
    repo_id: str | None = None,
    revision: str | None = None,
    file_name: str | None = None,
    sha256: str | None = None,
    license_name: str | None = None,
    publisher: str | None = None,
    provenance: str | None = None,
    verified: bool = False,
    status: str = "ready",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if path.suffix.lower() != ".gguf":
        raise ValueError("Only GGUF files can be registered")
    if not path.is_file():
        raise FileNotFoundError(path)
    vault_index.ensure_tables()
    model_id = _model_id(path)
    db = vault_index.get_db()
    try:
        db.execute(
            """
            INSERT INTO model_registry (
                id, name, source, file_path, quantization, size_bytes,
                downloaded_at, base_model, role, repo_id, revision, file_name,
                sha256, license, publisher, provenance, verified, status,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, source=excluded.source,
                file_path=excluded.file_path, quantization=excluded.quantization,
                size_bytes=excluded.size_bytes, downloaded_at=excluded.downloaded_at,
                base_model=excluded.base_model, role=excluded.role,
                repo_id=excluded.repo_id, revision=excluded.revision,
                file_name=excluded.file_name, sha256=excluded.sha256,
                license=excluded.license, publisher=excluded.publisher,
                provenance=excluded.provenance, verified=excluded.verified,
                status=excluded.status, metadata_json=excluded.metadata_json
            """,
            (
                model_id,
                name or path.name,
                source,
                str(path),
                quantization or model_catalog.infer_quantization(path.name),
                int(size_bytes if size_bytes is not None else path.stat().st_size),
                datetime.now(timezone.utc).isoformat(),
                base_model or "",
                role,
                repo_id,
                revision,
                file_name or path.name,
                sha256,
                license_name,
                publisher,
                provenance or source,
                int(bool(verified)),
                status,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        db.commit()
    finally:
        db.close()
    return get_model(model_id) or {"id": model_id, "file_path": str(path)}


def get_model(model_id: str) -> dict[str, Any] | None:
    for record in list_registry():
        if record["id"] == model_id:
            return record
    return None


def update_model_status(model_id: str, status: str) -> None:
    vault_index.ensure_tables()
    db = vault_index.get_db()
    try:
        db.execute("UPDATE model_registry SET status = ? WHERE id = ?", (status, model_id))
        db.commit()
    finally:
        db.close()


def remove_model(model_id: str) -> None:
    vault_index.ensure_tables()
    db = vault_index.get_db()
    try:
        db.execute("DELETE FROM model_registry WHERE id = ?", (model_id,))
        db.commit()
    finally:
        db.close()


def list_all_models() -> list[dict[str, Any]]:
    """Union of registry + disk GGUFs (disk entries fill gaps)."""
    by_name: dict[str, dict[str, Any]] = {}
    for m in list_registry():
        key = Path(m.get("file_path") or m["id"]).name
        by_name[key] = {**m, "filename": key}
    for m in list_disk_models():
        if m["filename"] not in by_name:
            by_name[m["filename"]] = m
    return list(by_name.values())


def get_active_model(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or paths.load_config()
    filename = cfg.get("inference", {}).get("model", "")
    path = paths.active_model_path(cfg)
    return {
        "filename": Path(filename).name if filename else "",
        "path": str(path),
        "exists": bool(filename) and path.is_file(),
        "mode": cfg.get("inference", {}).get("mode", "local"),
        "url": paths.inference_base_url(cfg),
        "ngl": cfg.get("inference", {}).get("ngl", 99),
        "ctx": cfg.get("inference", {}).get("ctx", 8192),
    }


def set_active_model(
    model: str,
    *,
    mode: str | None = None,
    url: str | None = None,
    ngl: int | None = None,
    ctx: int | None = None,
) -> dict[str, Any]:
    """Select an installed GGUF. Restart is deliberately handled by inference."""
    cfg = paths.load_config()
    inf = dict(cfg.get("inference") or {})
    p = Path(model)
    resolved = p if p.is_absolute() else paths.MODELS_DIR / p.name
    if not resolved.is_file() or resolved.suffix.lower() != ".gguf":
        raise FileNotFoundError(f"Installed GGUF not found: {resolved}")
    inf["model"] = p.name if not p.is_absolute() else str(p)
    if mode is not None:
        inf["mode"] = mode
    if url is not None:
        inf["url"] = url.rstrip("/")
    if ngl is not None:
        inf["ngl"] = int(ngl)
    if ctx is not None:
        inf["ctx"] = int(ctx)
    cfg["inference"] = inf
    paths.save_config(cfg)
    return get_active_model(cfg)


def apply_preset(preset_key: str) -> dict[str, Any]:
    """Set active model filename from a known preset (does not download)."""
    if preset_key not in PRESETS:
        raise KeyError(f"Unknown preset: {preset_key}. Known: {', '.join(PRESETS)}")
    preset = model_catalog.get_curated(preset_key)
    return set_active_model(
        preset.filename or "",
        ngl=preset.gpu_layers,
        ctx=preset.context,
    )


def hardware_fit(
    *,
    size_bytes: int,
    system_ram_gb: float | None = None,
    vram_gb: float | None = None,
) -> dict[str, Any]:
    """Conservative fit guidance; it is advice, not a compatibility guarantee."""
    size_gb = size_bytes / (1024**3)
    required_ram = max(4.0, size_gb * 1.25 + 2.0)
    if system_ram_gb is None:
        level, message = "unknown", f"Allow at least {required_ram:.0f} GB system RAM."
    elif system_ram_gb < required_ram:
        level, message = "poor", f"Likely too large; about {required_ram:.0f} GB RAM is recommended."
    elif vram_gb is not None and vram_gb >= size_gb * 0.8:
        level, message = "excellent", "Most layers should fit on the GPU."
    elif vram_gb:
        level, message = "partial", "Use partial GPU offload; generation will also use system RAM."
    else:
        level, message = "cpu", "CPU inference should work if enough system RAM is available."
    return {
        "level": level,
        "message": message,
        "model_size_gb": round(size_gb, 2),
        "recommended_ram_gb": round(required_ram, 1),
    }
