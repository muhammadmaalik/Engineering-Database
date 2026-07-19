from __future__ import annotations

import os
import threading
from http.server import ThreadingHTTPServer

import requests

import sync_server
from core import paths
from core.peer_auth import IdentityStore
from core import sync
from core.sync import SyncClient


def _start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), sync_server.SyncHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_signed_v2_health_and_dynamic_legacy_switch(tmp_path, monkeypatch):
    server_home = tmp_path / "server-home"
    vault = tmp_path / "server-vault"
    monkeypatch.setattr(paths, "MOTHERBRAIN_DIR", server_home)
    monkeypatch.setattr(paths, "VAULT_ROOT", vault)
    monkeypatch.setattr(paths, "SYNC_ROOTS", ("projects",))
    config = {"sync": {"token": "legacy-secret", "allow_legacy_token": False}, "role": "test"}
    monkeypatch.setattr(paths, "load_config", lambda: config)

    server_store = IdentityStore(server_home / "peer_auth")
    server_identity = server_store.load_or_create_identity("server")
    client_store = IdentityStore(tmp_path / "client-auth")
    client_identity = client_store.load_or_create_identity("client")
    server_store.trust_peer(client_identity.device_id, client_identity.public_key_b64)
    client_store.trust_peer(server_identity.device_id, server_identity.public_key_b64)

    server, thread = _start_server()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        client = SyncClient(
            url,
            identity_store=client_store,
            peer_id=server_identity.device_id,
            secure=True,
        )
        assert client.health()["service"] == "motherbrain-sync"

        public_health = requests.get(f"{url}/health", timeout=2)
        assert public_health.status_code == 200
        denied = requests.get(f"{url}/manifest", headers={"Authorization": "Bearer legacy-secret"}, timeout=2)
        assert denied.status_code == 401
        config["sync"]["allow_legacy_token"] = True
        allowed = requests.get(f"{url}/manifest", headers={"Authorization": "Bearer legacy-secret"}, timeout=2)
        assert allowed.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_direct_url_rejects_public_relay():
    try:
        SyncClient("http://8.8.8.8:8090")
    except ValueError as exc:
        assert "private" in str(exc) or "LAN" in str(exc)
    else:
        raise AssertionError("public peer address was accepted")


def test_deletion_tombstones_are_detected_and_applied(tmp_path, monkeypatch):
    state_home = tmp_path / "state"
    source = tmp_path / "source"
    remote = tmp_path / "remote"
    monkeypatch.setattr(paths, "MOTHERBRAIN_DIR", state_home)
    monkeypatch.setattr(paths, "VAULT_ROOT", source)
    monkeypatch.setattr(paths, "SYNC_ROOTS", ("projects",))
    local_file = source / "projects" / "demo" / "manifest.json"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("{}", encoding="utf-8")
    sync.tracked_local_state(source, now=100)
    local_file.unlink()
    _, tombstones = sync.tracked_local_state(source, now=200)
    assert tombstones["projects/demo/manifest.json"] == 200

    remote_file = remote / "projects" / "demo" / "manifest.json"
    remote_file.parent.mkdir(parents=True)
    remote_file.write_text("{}", encoding="utf-8")
    os.utime(remote_file, (100, 100))
    deleted = sync.apply_tombstones(tombstones, remote)
    assert deleted == ["projects/demo/manifest.json"]
    assert not remote_file.exists()


def test_sync_rate_limit_rejects_excess_requests(monkeypatch):
    ticks = iter((0.0, 0.1, 0.2))
    monkeypatch.setattr(sync_server.time, "monotonic", lambda: next(ticks))
    sync_server._RATE_BUCKETS.clear()
    assert sync_server._rate_ok("127.0.0.1", limit=2, window=60)
    assert sync_server._rate_ok("127.0.0.1", limit=2, window=60)
    assert not sync_server._rate_ok("127.0.0.1", limit=2, window=60)
