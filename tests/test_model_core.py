from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from core import inference, model_catalog, model_download, models, paths, vault_index


def _temp_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "state"
    vault = home / "vault"
    model_dir = vault / "shared" / "base_models"
    monkeypatch.setattr(paths, "MOTHERBRAIN_DIR", home)
    monkeypatch.setattr(paths, "CONFIG_PATH", home / "config.json")
    monkeypatch.setattr(paths, "CONFIG_LOCK_PATH", home / "config.lock")
    monkeypatch.setattr(paths, "VAULT_ROOT", vault)
    monkeypatch.setattr(paths, "VAULT_DB", vault / "vault_index.db")
    monkeypatch.setattr(paths, "MODELS_DIR", model_dir)
    monkeypatch.setattr(paths, "PEER_STATE_DIR", home / "peers")
    monkeypatch.setattr(paths, "VAULT_SUBDIRS", [model_dir])


def test_catalog_has_disabled_custom_and_reviewed_presets() -> None:
    entries = model_catalog.list_curated()
    custom = [item for item in entries if item["provenance"] == "first_party"]
    reviewed = [item for item in entries if item["provenance"] == "curated"]
    assert len(custom) == 2
    assert all(item["status"] == "coming_soon" and not item["available"] for item in custom)
    assert reviewed and all(item["repo_id"] and item["filename"] for item in reviewed)


def test_registry_migrates_old_database(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE model_registry (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "source TEXT, file_path TEXT NOT NULL, quantization TEXT, size_bytes INTEGER, "
        "downloaded_at TEXT, base_model TEXT, role TEXT DEFAULT 'general')"
    )
    db.commit()
    db.close()
    vault_index.ensure_tables(db_path)
    db = sqlite3.connect(db_path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(model_registry)")}
    db.close()
    assert {"repo_id", "revision", "sha256", "status", "metadata_json"} <= columns


class _Response:
    def __init__(self, body: bytes, status: int = 206, extra: bytes = b""):
        self.body = body
        self.extra = extra
        self.status_code = status
        self.headers = {"Content-Length": str(len(body) + len(extra))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size: int):
        yield self.body
        if self.extra:
            yield self.extra


def test_resumable_download_verifies_and_registers(monkeypatch, tmp_path: Path) -> None:
    _temp_state(monkeypatch, tmp_path)
    destination = paths.MODELS_DIR
    destination.mkdir(parents=True)
    partial = destination / ".model-Q4_K_M.gguf.part"
    partial.write_bytes(b"abc")
    monkeypatch.setattr(model_download, "_download_url", lambda *args: "https://example.invalid/model")
    monkeypatch.setattr(model_download.requests, "get", lambda *args, **kwargs: _Response(b"def"))
    payload = b"abcdef"
    target = model_download.download_gguf(
        repo_id="publisher/repo",
        filename="folder/model-Q4_K_M.gguf",
        revision="0123456789abcdef",
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        destination_dir=destination,
    )
    assert target.read_bytes() == payload
    assert not partial.exists()
    assert models.list_registry()[0]["verified"] is True


def test_cancel_keeps_partial_for_retry(monkeypatch, tmp_path: Path) -> None:
    _temp_state(monkeypatch, tmp_path)
    event = threading.Event()
    monkeypatch.setattr(model_download, "_download_url", lambda *args: "https://example.invalid/model")
    monkeypatch.setattr(
        model_download.requests,
        "get",
        lambda *args, **kwargs: _Response(b"chunk", 200, b"later"),
    )

    def progress(done: int, total: int | None) -> None:
        if done:
            event.set()

    with pytest.raises(model_download.DownloadCancelled):
        model_download.download_gguf(
            repo_id="publisher/repo",
            filename="model.gguf",
            revision="0123456789abcdef",
            destination_dir=paths.MODELS_DIR,
            cancel_event=event,
            progress=progress,
        )
    assert (paths.MODELS_DIR / ".model.gguf.part").exists()
    assert not (paths.MODELS_DIR / "model.gguf").exists()


def test_activation_restarts_instead_of_leaving_old_model(monkeypatch, tmp_path: Path) -> None:
    model = tmp_path / "selected.gguf"
    model.write_bytes(b"gguf")
    calls: list[str] = []
    monkeypatch.setattr(inference, "is_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(inference, "stop_server", lambda: calls.append("stop"))
    monkeypatch.setattr(
        models,
        "set_active_model",
        lambda *args, **kwargs: {"filename": model.name},
    )
    monkeypatch.setattr(
        inference,
        "start_server",
        lambda *args, **kwargs: calls.append("start") or True,
    )
    result = inference.activate_model(model)
    assert calls == ["stop", "start"]
    assert result["server_ready"] is True
