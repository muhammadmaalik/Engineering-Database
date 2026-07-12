"""Model registry listing, disk GGUF discovery, active model + presets."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import paths
from . import vault_index

# Download / selection presets for Settings / Models UI (Phase 2).
PRESETS: dict[str, dict[str, str]] = {
    "gemma-9b": {
        "label": "Gemma 2 9B Instruct",
        "repo": "bartowski/gemma-2-9b-it-GGUF",
        "quant": "Q5_K_M",
        "filename": "gemma-2-9b-it-Q5_K_M.gguf",
    },
    "qwen-32b": {
        "label": "Qwen2.5-Coder 32B Instruct",
        "repo": "Qwen/Qwen2.5-Coder-32B-Instruct-GGUF",
        "quant": "Q4_K_M",
        "filename": "Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf",
    },
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
            SELECT id, name, source, file_path, quantization, size_bytes, downloaded_at, base_model, role
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
            }
        )
    return result


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
        "filename": Path(filename).name if filename else path.name,
        "path": str(path),
        "exists": path.exists(),
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
    """Set active GGUF filename (or absolute path) in config.json."""
    cfg = paths.load_config()
    inf = dict(cfg.get("inference") or {})
    p = Path(model)
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
    return set_active_model(PRESETS[preset_key]["filename"])
