"""Vault paths and ~/.motherbrain/config.json helpers.

Vault and config always live under ``~/.motherbrain`` (``Path.home()``),
including when the app is frozen by PyInstaller (``sys.frozen`` /
``sys._MEIPASS``). Bundled assets may live under ``_MEIPASS``; user data
never does.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

HOME = Path.home()
MOTHERBRAIN_DIR = HOME / ".motherbrain"
CONFIG_PATH = MOTHERBRAIN_DIR / "config.json"
VAULT_ROOT = MOTHERBRAIN_DIR / "vault"
CERTS_DIR = MOTHERBRAIN_DIR / "certs"

PROJECTS_DIR = VAULT_ROOT / "projects"
CHATS_DIR = VAULT_ROOT / "chats"
MODELS_DIR = VAULT_ROOT / "shared" / "base_models"
ADAPTERS_DIR = VAULT_ROOT / "shared" / "adapters"
DATASETS_DIR = VAULT_ROOT / "shared" / "global_datasets"
EXPORTS_DIR = VAULT_ROOT / "shared" / "exports"
SCREENSHOTS_DIR = VAULT_ROOT / "shared" / "screenshots"
VAULT_DB = VAULT_ROOT / "vault_index.db"

# Windows builds ship llama-server.exe; POSIX builds use llama-server.
_LLAMA_BIN_DIR = HOME / "llama.cpp" / "build" / "bin"
LLAMA_SERVER_DEFAULT = (
    _LLAMA_BIN_DIR / "llama-server.exe"
    if (_LLAMA_BIN_DIR / "llama-server.exe").exists()
    else _LLAMA_BIN_DIR / "llama-server"
)


def resolve_llama_server() -> Path:
    """Locate llama-server binary (config override, then common install paths)."""
    cfg = load_config() if CONFIG_PATH.exists() else {}
    override = (cfg.get("inference") or {}).get("llama_bin") or ""
    if override:
        p = Path(override)
        if p.exists():
            return p
    candidates = [
        _LLAMA_BIN_DIR / "llama-server.exe",
        _LLAMA_BIN_DIR / "llama-server",
        _LLAMA_BIN_DIR / "Release" / "llama-server.exe",
        HOME / "llama.cpp" / "llama-server.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fall back to default path even if missing (caller raises FileNotFoundError).
    return LLAMA_SERVER_DEFAULT


DEFAULT_CONFIG: dict[str, Any] = {
    "inference": {
        "mode": "local",
        "url": "http://127.0.0.1:8081",
        "model": "qwen2.5-coder-32b-instruct-q3_k_m.gguf",
        # ~8GB laptop GPUs: partial CUDA offload; keep ctx/slots small for KV.
        "ngl": 28,
        "ctx": 2048,
        "parallel": 1,
        "timeout": 300,
    },
    "sync": {
        "server_url": "http://10.0.0.1:8090",
        "token": "",
    },
    "web": {
        "host": "10.0.0.1",
        "port": 8443,
        "token": "",
        "tls_cert": "",
        "tls_key": "",
        # Desktop + phone chat: tools off until inference is stable.
        "allow_tools": False,
    },
    # TCP JSON bridge to Isaac Sim (see isaac_sim/bridge_server.py).
    "isaac_sim": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8765,
        "timeout": 3.0,
        "transport": "tcp",
        "ros_domain_id": 0,
        "default_robot_prim": "/World/Robot",
    },
    "role": "laptop",
}

VAULT_SUBDIRS = [
    PROJECTS_DIR,
    CHATS_DIR,
    MODELS_DIR,
    ADAPTERS_DIR,
    DATASETS_DIR,
    EXPORTS_DIR,
    SCREENSHOTS_DIR,
]

# Relative vault roots included in sync inventory (not huge GGUFs by default).
SYNC_ROOTS = (
    "projects",
    "chats",
    "shared/global_datasets",
    "shared/adapters",
    "shared/exports",
)


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory for bundled read-only assets (templates, etc.).

    When frozen, this is ``sys._MEIPASS``. Otherwise the repo root.
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def ensure_dirs() -> None:
    """Create motherbrain home + vault subdirectory tree + certs dir."""
    MOTHERBRAIN_DIR.mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    for d in VAULT_SUBDIRS:
        d.mkdir(parents=True, exist_ok=True)


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)


def load_config() -> dict[str, Any]:
    """Load config, merging missing keys from defaults."""
    ensure_dirs()
    if not CONFIG_PATH.exists():
        return ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ensure_config(overwrite=False)
    if not isinstance(data, dict):
        return ensure_config()
    return _merge_defaults(data)


def save_config(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def ensure_config(overwrite: bool = False) -> dict[str, Any]:
    """First-run helper: create ~/.motherbrain/config.json with the plan schema.

    If the file already exists and overwrite is False, load/merge and rewrite
    only missing keys (preserves user values).
    """
    ensure_dirs()
    if CONFIG_PATH.exists() and not overwrite:
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        cfg = _merge_defaults(existing)
    else:
        cfg = default_config()
    save_config(cfg)
    return cfg


def _merge_defaults(data: dict[str, Any]) -> dict[str, Any]:
    cfg = default_config()
    for key, value in data.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            merged = dict(cfg[key])
            merged.update(value)
            cfg[key] = merged
        else:
            cfg[key] = value
    return cfg


def vault_rel(path: Path | str) -> str:
    """Return path relative to vault root using forward slashes."""
    p = Path(path)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    return p.resolve().relative_to(VAULT_ROOT.resolve()).as_posix()


def vault_abs(rel: str) -> Path:
    """Resolve a vault-relative path; rejects escape attempts."""
    rel_norm = rel.replace("\\", "/").lstrip("/")
    if ".." in Path(rel_norm).parts:
        raise ValueError(f"Path escapes vault: {rel}")
    full = (VAULT_ROOT / rel_norm).resolve()
    full.relative_to(VAULT_ROOT.resolve())
    return full


def inference_base_url(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return str(cfg.get("inference", {}).get("url", "http://127.0.0.1:8081")).rstrip("/")


def health_url(cfg: dict[str, Any] | None = None) -> str:
    """llama-server readiness endpoint (fast; does not run inference)."""
    return f"{inference_base_url(cfg)}/health"


def completion_url(cfg: dict[str, Any] | None = None) -> str:
    return f"{inference_base_url(cfg)}/completion"


def sync_server_url(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    return str(cfg.get("sync", {}).get("server_url", "http://10.0.0.1:8090")).rstrip("/")


def active_model_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    name = cfg.get("inference", {}).get("model", "")
    path = Path(name)
    if path.is_absolute():
        return path
    return MODELS_DIR / name
