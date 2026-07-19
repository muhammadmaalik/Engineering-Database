"""Client API for the Motherbrain vault sync server (:8090)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from . import paths
from .discovery import validate_direct_url
from .peer_auth import (
    IdentityStore,
    TrustedPeer,
    create_join_request,
    decode_connection_key,
    sign_request,
    verify_response,
)

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BATCH_BYTES = 128 * 1024 * 1024


def file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def local_inventory(vault_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Build inventory for sync roots (path -> {mtime, size, sha256})."""
    root = vault_root or paths.VAULT_ROOT
    inv: dict[str, dict[str, Any]] = {}
    for rel_root in paths.SYNC_ROOTS:
        base = root / rel_root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            # Skip huge GGUFs even if somehow under a sync root.
            if p.suffix.lower() == ".gguf":
                continue
            rel = p.relative_to(root).as_posix()
            st = p.stat()
            inv[rel] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "sha256": file_sha256(p),
            }
    return inv


def _sync_state_path() -> Path:
    return paths.MOTHERBRAIN_DIR / "sync_state.json"


def _load_sync_state() -> dict[str, Any]:
    state_path = _sync_state_path()
    if not state_path.exists():
        return {"known_paths": [], "tombstones": {}}
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"known_paths": [], "tombstones": {}}
    except (OSError, json.JSONDecodeError):
        return {"known_paths": [], "tombstones": {}}


def _save_sync_state(state: dict[str, Any]) -> None:
    _atomic_write_file(
        _sync_state_path(),
        (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def tracked_local_state(
    vault_root: Path | None = None,
    *,
    now: float | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Inventory files and turn deletions since the previous scan into tombstones."""
    current = local_inventory(vault_root)
    state = _load_sync_state()
    previous = {str(item) for item in state.get("known_paths", [])}
    tombstones = {
        str(rel): float(timestamp)
        for rel, timestamp in (state.get("tombstones") or {}).items()
    }
    timestamp = float(time.time() if now is None else now)
    for rel in previous - set(current):
        tombstones.setdefault(rel, timestamp)
    for rel in current:
        tombstones.pop(rel, None)
    _save_sync_state({"known_paths": sorted(current), "tombstones": tombstones})
    return current, tombstones


def apply_tombstones(
    incoming: dict[str, float],
    vault_root: Path | None = None,
) -> list[str]:
    """Apply newer remote deletions safely and merge them into local state."""
    root = Path(vault_root or paths.VAULT_ROOT).resolve()
    state = _load_sync_state()
    local_tombstones = {
        str(rel): float(timestamp)
        for rel, timestamp in (state.get("tombstones") or {}).items()
    }
    deleted: list[str] = []
    for rel, raw_timestamp in incoming.items():
        timestamp = float(raw_timestamp)
        normalized = rel.replace("\\", "/").lstrip("/")
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file() and target.stat().st_mtime > timestamp:
            continue
        if target.is_file():
            target.unlink()
            deleted.append(normalized)
        local_tombstones[normalized] = max(timestamp, local_tombstones.get(normalized, 0.0))
    current = local_inventory(root)
    _save_sync_state({"known_paths": sorted(current), "tombstones": local_tombstones})
    return deleted


def diff_inventories(
    local: dict[str, dict[str, Any]],
    remote: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Compute pull/push/conflict sets. Newer mtime wins; same mtime+hash = skip."""
    pull: list[str] = []
    push: list[str] = []
    conflicts: list[str] = []
    identical: list[str] = []

    all_paths = set(local) | set(remote)
    for rel in sorted(all_paths):
        L = local.get(rel)
        R = remote.get(rel)
        if L and not R:
            push.append(rel)
        elif R and not L:
            pull.append(rel)
        elif L and R:
            if L.get("sha256") == R.get("sha256"):
                identical.append(rel)
                continue
            lm, rm = float(L.get("mtime", 0)), float(R.get("mtime", 0))
            # Treat near-equal mtimes with different hashes as conflicts.
            if abs(lm - rm) < 1e-3:
                conflicts.append(rel)
            elif rm > lm:
                pull.append(rel)
            elif lm > rm:
                push.append(rel)
            else:
                conflicts.append(rel)
    return {
        "pull": pull,
        "push": push,
        "conflicts": conflicts,
        "identical": identical,
    }


class SyncClient:
    """HTTP client for sync_server.py."""

    def __init__(
        self,
        server_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        *,
        identity_store: IdentityStore | None = None,
        peer_id: str | None = None,
        secure: bool | None = None,
    ):
        cfg = paths.load_config()
        sync_cfg = cfg.get("sync") or {}
        self.server_url = validate_direct_url(
            (server_url or sync_cfg.get("server_url") or "http://10.0.0.1:8090").rstrip("/")
        )
        self.token = token if token is not None else (sync_cfg.get("token") or "")
        self.timeout = timeout
        self.identity_store = identity_store or IdentityStore()
        configured_peer = peer_id or sync_cfg.get("peer_id")
        peers = self.identity_store.list_trusted_peers()
        if configured_peer is None and len(peers) == 1:
            configured_peer = next(iter(peers))
        self.peer: TrustedPeer | None = peers.get(str(configured_peer)) if configured_peer else None
        self.secure = bool(self.peer) if secure is None else secure
        if self.secure and self.peer is None:
            raise ValueError("secure sync requires a trusted peer_id")
        self.identity = self.identity_store.load_or_create_identity() if self.secure else None

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(
        self,
        method: str,
        route: str,
        *,
        params: dict[str, Any] | None = None,
        body: bytes = b"",
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        target_route = f"/v2{route}" if self.secure else route
        request = requests.Request(method, f"{self.server_url}{target_route}", params=params, data=body)
        prepared = request.prepare()
        split = urlsplit(prepared.url)
        headers = {"Accept": "application/json"} if self.secure else self._headers()
        headers.update(extra_headers or {})
        request_nonce: str | None = None
        if self.secure:
            if self.identity is None:
                raise ValueError("secure sync has no local identity")
            auth_headers = sign_request(
                self.identity,
                method,
                split.path,
                split.query,
                body,
            )
            request_nonce = auth_headers["X-MB-Nonce"]
            headers.update(auth_headers)
        response = requests.request(
            method,
            prepared.url,
            data=body,
            headers=headers,
            timeout=timeout or self.timeout,
        )
        if self.secure and self.peer is not None and request_nonce is not None:
            verify_response(self.peer, response.status_code, response.content, request_nonce, response.headers)
        return response

    def _json_request(
        self,
        method: str,
        route: str,
        payload: Any,
        *,
        timeout: float | None = None,
    ) -> requests.Response:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_BATCH_BYTES:
            raise ValueError("sync payload exceeds maximum batch size")
        return self._request(
            method,
            route,
            body=body,
            timeout=timeout,
            extra_headers={"Content-Type": "application/json"},
        )

    def health(self) -> dict[str, Any]:
        r = self._request("GET", "/health")
        r.raise_for_status()
        return r.json()

    def remote_manifest(self) -> dict[str, dict[str, Any]]:
        return self.remote_state()["files"]

    def remote_state(self) -> dict[str, Any]:
        r = self._request("GET", "/manifest")
        r.raise_for_status()
        data = r.json()
        files = data.get("files", data)
        if not isinstance(files, dict):
            raise ValueError("Invalid manifest response")
        tombstones = data.get("tombstones", {}) if isinstance(data, dict) else {}
        return {"files": files, "tombstones": tombstones if isinstance(tombstones, dict) else {}}

    def get_file(self, rel: str, dest: Path | None = None) -> Path:
        r = self._request(
            "GET",
            "/file",
            params={"path": rel},
        )
        r.raise_for_status()
        if len(r.content) > MAX_FILE_BYTES:
            raise ValueError("remote file exceeds maximum size")
        out = dest or paths.vault_abs(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_file(out, r.content)
        # Best-effort mtime restore from header.
        mtime = r.headers.get("X-Vault-Mtime")
        if mtime:
            try:
                ts = float(mtime)
                import os

                os.utime(out, (ts, ts))
            except (TypeError, ValueError, OSError):
                pass
        return out

    def put_file(self, rel: str, src: Path | None = None) -> dict[str, Any]:
        path = src or paths.vault_abs(rel)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file exceeds maximum sync size")
        headers: dict[str, str] = {}
        headers["X-Vault-Path"] = rel
        headers["X-Vault-Mtime"] = str(path.stat().st_mtime)
        r = self._request(
            "PUT",
            "/file",
            params={"path": rel},
            body=path.read_bytes(),
            extra_headers=headers,
        )
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": True, "path": rel}

    def pull(self, paths_list: list[str] | None = None) -> dict[str, Any]:
        """Batch pull. If paths_list is None, pull by inventory diff."""
        if paths_list is None:
            local = local_inventory()
            remote = self.remote_manifest()
            paths_list = diff_inventories(local, remote)["pull"]
        body = {"paths": paths_list}
        r = self._json_request(
            "POST",
            "/sync/pull",
            body,
            timeout=max(self.timeout, 120.0),
        )
        r.raise_for_status()
        data = r.json()
        written = []
        for item in data.get("files", []):
            rel = item["path"]
            content_b64 = item.get("content_b64")
            if content_b64 is None and "content" in item:
                # Server may return raw text for small files.
                dest = paths.vault_abs(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                raw_content = item["content"].encode("utf-8") if isinstance(item["content"], str) else item["content"]
                if len(raw_content) > MAX_FILE_BYTES:
                    raise ValueError("remote file exceeds maximum size")
                _atomic_write_file(dest, raw_content)
            else:
                import base64

                dest = paths.vault_abs(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                decoded = base64.b64decode(content_b64 or b"", validate=True)
                if len(decoded) > MAX_FILE_BYTES:
                    raise ValueError("remote file exceeds maximum size")
                _atomic_write_file(dest, decoded)
            mtime = item.get("mtime")
            if mtime is not None:
                try:
                    import os

                    os.utime(dest, (float(mtime), float(mtime)))
                except (TypeError, ValueError, OSError):
                    pass
            written.append(rel)
        return {"pulled": written, "count": len(written)}

    def push(self, paths_list: list[str] | None = None) -> dict[str, Any]:
        """Batch push. If paths_list is None, push by inventory diff."""
        import base64

        if paths_list is None:
            local = local_inventory()
            remote = self.remote_manifest()
            paths_list = diff_inventories(local, remote)["push"]

        files = []
        for rel in paths_list:
            path = paths.vault_abs(rel)
            if not path.is_file():
                continue
            st = path.stat()
            if st.st_size > MAX_FILE_BYTES:
                raise ValueError(f"file exceeds maximum sync size: {rel}")
            files.append(
                {
                    "path": rel,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "sha256": file_sha256(path),
                    "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            )
        r = self._json_request(
            "POST",
            "/sync/push",
            {"files": files},
            timeout=max(self.timeout, 120.0),
        )
        r.raise_for_status()
        return r.json()

    def sync_all(self) -> dict[str, Any]:
        """Pull then push using inventory diff (newer wins; conflicts keep both)."""
        local, local_tombstones = tracked_local_state()
        remote_state = self.remote_state()
        remote = remote_state["files"]
        deleted = apply_tombstones(remote_state.get("tombstones") or {})
        if deleted:
            local, local_tombstones = tracked_local_state()
        diff = diff_inventories(local, remote)

        # Conflicts: pull remote into .conflict-<ts>, then push local as primary.
        conflict_results = []
        ts = int(time.time())
        for rel in diff["conflicts"]:
            conflict_rel = _conflict_name(rel, ts)
            try:
                self.get_file(rel, dest=paths.vault_abs(conflict_rel))
                conflict_results.append({"path": rel, "saved_as": conflict_rel})
            except Exception as e:
                conflict_results.append({"path": rel, "error": str(e)})

        pull_result = self.pull(diff["pull"]) if diff["pull"] else {"pulled": [], "count": 0}
        # After conflicts, push our local version as the canonical path.
        push_paths = list(diff["push"]) + list(diff["conflicts"])
        push_result = self.push(push_paths) if push_paths else {"pushed": [], "count": 0}
        tombstone_result: dict[str, Any] = {"deleted": [], "count": 0}
        if local_tombstones:
            response = self._json_request(
                "POST",
                "/sync/tombstones",
                {"tombstones": local_tombstones},
            )
            response.raise_for_status()
            tombstone_result = response.json()

        return {
            "diff": {k: v for k, v in diff.items() if k != "identical"},
            "identical_count": len(diff["identical"]),
            "pull": pull_result,
            "push": push_result,
            "deleted_local": deleted,
            "tombstones": tombstone_result,
            "conflicts": conflict_results,
        }


def _conflict_name(rel: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    p = Path(rel)
    return f"{p.with_suffix('')}.conflict-{ts}{p.suffix}".replace("\\", "/")


def _atomic_write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


# Module-level convenience wrappers
def sync_now(**kwargs: Any) -> dict[str, Any]:
    return SyncClient(**kwargs).sync_all()


def open_pairing_window(
    advertised_url: str,
    *,
    local_server_url: str = "http://127.0.0.1:8090",
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Ask the local host server to create a two-minute connection key."""
    advertised_url = validate_direct_url(advertised_url)
    local_server_url = validate_direct_url(local_server_url)
    response = requests.post(
        f"{local_server_url}/v2/pair/open",
        json={"server_url": advertised_url},
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    result["server_url"] = local_server_url
    result["side"] = "host"
    return result


def join_pairing_window(
    connection_key: str,
    *,
    identity_store: IdentityStore | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Join an invitation without saving trust until both users confirm."""
    store = identity_store or IdentityStore()
    invitation = decode_connection_key(connection_key)
    server_url = validate_direct_url(str(invitation["server_url"]))
    identity = store.load_or_create_identity()
    request = create_join_request(identity, invitation)
    response = requests.post(f"{server_url}/v2/pair/join", json=request, timeout=timeout)
    response.raise_for_status()
    return {
        **response.json(),
        "server_url": server_url,
        "session_id": invitation["session_id"],
        "secret": invitation["secret"],
        "side": "guest",
        "invitation": invitation,
    }


def pairing_status(
    context: dict[str, Any],
    *,
    identity_store: IdentityStore | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    response = requests.post(
        f"{validate_direct_url(str(context['server_url']))}/v2/pair/status",
        json={"session_id": context["session_id"], "secret": context["secret"]},
        timeout=timeout,
    )
    response.raise_for_status()
    status = {**context, **response.json()}
    if status.get("complete") and context.get("side") == "guest":
        _persist_host_trust(status, identity_store)
    return status


def confirm_pairing_window(
    context: dict[str, Any],
    sas: str,
    *,
    identity_store: IdentityStore | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Confirm the matching 8-digit code; persist guest trust only after both confirm."""
    response = requests.post(
        f"{validate_direct_url(str(context['server_url']))}/v2/pair/confirm",
        json={
            "session_id": context["session_id"],
            "secret": context["secret"],
            "side": context["side"],
            "sas": sas,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    status = {**context, **response.json()}
    if status.get("complete") and context.get("side") == "guest":
        _persist_host_trust(status, identity_store)
    return status


def _persist_host_trust(
    status: dict[str, Any],
    identity_store: IdentityStore | None = None,
) -> None:
    host = status["host"]
    store = identity_store or IdentityStore()
    store.trust_peer(host["device_id"], host["public_key"], host.get("name") or "")
    cfg = paths.load_config()
    cfg.setdefault("sync", {})["peer_id"] = host["device_id"]
    cfg["sync"]["server_url"] = status["server_url"]
    paths.save_config(cfg)
