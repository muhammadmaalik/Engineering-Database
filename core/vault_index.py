"""Ensure SQLite vault tables and index project manifests (no C++ reindex)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT,
    path TEXT NOT NULL,
    tags TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT,
    project_id TEXT,
    type TEXT,
    chip TEXT,
    protocol TEXT,
    capabilities TEXT,
    PRIMARY KEY (device_id, project_id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS models (
    model_id TEXT,
    project_id TEXT,
    base_model TEXT,
    role TEXT,
    lora_adapter_path TEXT,
    quantization TEXT,
    PRIMARY KEY (model_id, project_id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS datasets (
    name TEXT,
    project_id TEXT,
    source TEXT,
    format TEXT,
    path TEXT,
    size INTEGER,
    tags TEXT,
    PRIMARY KEY (name, project_id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT,
    engine TEXT,
    date TEXT,
    success_rate REAL,
    results_path TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    type INTEGER NOT NULL,
    type_name TEXT NOT NULL,
    payload TEXT,
    payload_size INTEGER,
    project_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_msg_log_timestamp ON message_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_log_type ON message_log(type);
CREATE INDEX IF NOT EXISTS idx_msg_log_project ON message_log(project_id);

CREATE TABLE IF NOT EXISTS model_registry (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT,
    file_path TEXT NOT NULL,
    quantization TEXT,
    size_bytes INTEGER,
    downloaded_at TEXT,
    base_model TEXT,
    role TEXT DEFAULT 'general',
    repo_id TEXT,
    revision TEXT,
    file_name TEXT,
    sha256 TEXT,
    license TEXT,
    publisher TEXT,
    provenance TEXT,
    verified INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ready',
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS curation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_log_id INTEGER UNIQUE,
    label TEXT,
    correction TEXT,
    curated_at TEXT,
    FOREIGN KEY (message_log_id) REFERENCES message_log(id)
);

CREATE TABLE IF NOT EXISTS conversation_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id INTEGER,
    response_id INTEGER,
    query_text TEXT,
    response_text TEXT,
    paired_at TEXT,
    curated_label TEXT,
    curated_correction TEXT,
    FOREIGN KEY (query_id) REFERENCES message_log(id),
    FOREIGN KEY (response_id) REFERENCES message_log(id)
);
"""


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.VAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), timeout=5)


def ensure_tables(db_path: Path | None = None) -> None:
    """Create vault tables if missing (matches C++ vault_manager schema)."""
    db = get_db(db_path)
    try:
        db.executescript(SCHEMA_SQL)
        # FTS5 may be unavailable on some builds — soft-fail.
        try:
            db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
                    id, name, description, tags, content='projects', content_rowid='rowid'
                )
                """
            )
        except sqlite3.OperationalError:
            pass
        _migrate_model_registry(db)
        db.commit()
    finally:
        db.close()


def _migrate_model_registry(db: sqlite3.Connection) -> None:
    """Add provenance/verification columns to registries created by older builds."""
    existing = {
        str(row[1])
        for row in db.execute("PRAGMA table_info(model_registry)").fetchall()
    }
    additions = {
        "repo_id": "TEXT",
        "revision": "TEXT",
        "file_name": "TEXT",
        "sha256": "TEXT",
        "license": "TEXT",
        "publisher": "TEXT",
        "provenance": "TEXT",
        "verified": "INTEGER DEFAULT 0",
        "status": "TEXT DEFAULT 'ready'",
        "metadata_json": "TEXT DEFAULT '{}'",
    }
    for column, declaration in additions.items():
        if column not in existing:
            db.execute(f"ALTER TABLE model_registry ADD COLUMN {column} {declaration}")


def _tags_str(tags: Any) -> str:
    if tags is None:
        return ""
    if isinstance(tags, list):
        return ",".join(str(t) for t in tags)
    return str(tags)


def upsert_project_from_manifest(
    manifest: dict[str, Any],
    *,
    project_path: Path | str | None = None,
    db_path: Path | None = None,
) -> str:
    """Insert/replace a project (+ devices/models/datasets) from a manifest dict."""
    ensure_tables(db_path)
    proj = manifest.get("project") or {}
    project_id = proj.get("id")
    if not project_id:
        raise ValueError("manifest.project.id is required")

    name = proj.get("name") or project_id
    description = proj.get("description") or ""
    status = proj.get("status") or "design"
    created = proj.get("created") or ""
    updated = proj.get("updated") or ""
    tags = _tags_str(proj.get("tags"))
    if project_path is None:
        project_path = paths.PROJECTS_DIR / project_id
    project_path = Path(project_path)

    db = get_db(db_path)
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO projects
                (id, name, description, status, path, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, description, status, str(project_path), tags, created, updated),
        )

        # Refresh child rows for this project.
        db.execute("DELETE FROM devices WHERE project_id = ?", (project_id,))
        db.execute("DELETE FROM models WHERE project_id = ?", (project_id,))
        db.execute("DELETE FROM datasets WHERE project_id = ?", (project_id,))

        for d in (manifest.get("hardware") or {}).get("devices") or []:
            device_id = d.get("device_id") or d.get("id") or d.get("name") or "device"
            caps = d.get("capabilities")
            if isinstance(caps, list):
                caps = json.dumps(caps)
            db.execute(
                """
                INSERT OR REPLACE INTO devices
                    (device_id, project_id, type, chip, protocol, capabilities)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    project_id,
                    d.get("type") or "",
                    d.get("chip") or "",
                    d.get("protocol") or "",
                    caps or "",
                ),
            )

        for m in (manifest.get("ai") or {}).get("models") or []:
            model_id = m.get("model_id") or m.get("id") or m.get("name") or "model"
            db.execute(
                """
                INSERT OR REPLACE INTO models
                    (model_id, project_id, base_model, role, lora_adapter_path, quantization)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    project_id,
                    m.get("base_model") or "",
                    m.get("role") or "general",
                    m.get("lora_adapter_path") or m.get("adapter") or "",
                    m.get("quantization") or "",
                ),
            )

        for c in (manifest.get("datasets") or {}).get("collections") or []:
            cname = c.get("name") or "dataset"
            db.execute(
                """
                INSERT OR REPLACE INTO datasets
                    (name, project_id, source, format, path, size, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cname,
                    project_id,
                    c.get("source") or "",
                    c.get("format") or "",
                    c.get("path") or "",
                    int(c.get("size") or 0),
                    _tags_str(c.get("tags")),
                ),
            )

        # FTS refresh (best-effort; skip if FTS missing or DB corrupted)
        try:
            db.execute("DELETE FROM projects_fts WHERE id = ?", (project_id,))
            db.execute(
                """
                INSERT INTO projects_fts (rowid, id, name, description, tags)
                VALUES ((SELECT rowid FROM projects WHERE id = ?), ?, ?, ?, ?)
                """,
                (project_id, project_id, name, description, tags),
            )
        except sqlite3.Error:
            pass

        db.commit()
    finally:
        db.close()
    return project_id


def delete_project(project_id: str, *, remove_files: bool = True, db_path: Path | None = None) -> None:
    """Remove a project from SQLite (and optional FTS) and optionally delete its folder."""
    if not project_id:
        raise ValueError("project_id required")
    ensure_tables(db_path)
    db = get_db(db_path)
    try:
        db.execute("DELETE FROM devices WHERE project_id = ?", (project_id,))
        db.execute("DELETE FROM models WHERE project_id = ?", (project_id,))
        db.execute("DELETE FROM datasets WHERE project_id = ?", (project_id,))
        try:
            db.execute("DELETE FROM projects_fts WHERE id = ?", (project_id,))
        except sqlite3.Error:
            pass
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()
    finally:
        db.close()
    if remove_files:
        import shutil

        pdir = paths.PROJECTS_DIR / project_id
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)


def index_manifest_file(manifest_path: Path | str, db_path: Path | None = None) -> str | None:
    """Parse a manifest.json on disk and upsert into SQLite."""
    mpath = Path(manifest_path)
    if not mpath.exists():
        return None
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return upsert_project_from_manifest(manifest, project_path=mpath.parent, db_path=db_path)


def index_project(project_id: str, db_path: Path | None = None) -> str | None:
    return index_manifest_file(paths.PROJECTS_DIR / project_id / "manifest.json", db_path=db_path)


def index_all_projects(db_path: Path | None = None) -> list[str]:
    """Scan projects/*/manifest.json and upsert each (no C++ reindex needed)."""
    ensure_tables(db_path)
    paths.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    indexed: list[str] = []
    for entry in sorted(paths.PROJECTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        pid = index_manifest_file(entry / "manifest.json", db_path=db_path)
        if pid:
            indexed.append(pid)
    return indexed
