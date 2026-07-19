#!/usr/bin/env python3
"""
Motherbrain vault sync server — port 8090 (stdlib HTTP, no Flask required).

Endpoints:
  GET  /health
  GET  /manifest
  GET  /file?path=...
  PUT  /file?path=...
  POST /sync/pull
  POST /sync/push

Run on the home PC:
  python3 sync_server.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Allow `python sync_server.py` from repo root.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import paths  # noqa: E402
from core.discovery import is_direct_address  # noqa: E402
from core.peer_auth import (  # noqa: E402
    NONCE_HEADER,
    IdentityStore,
    PairingSessionStore,
    PeerAuthError,
    encode_connection_key,
    sign_response,
    verify_request,
)
from core.sync import (  # noqa: E402
    MAX_BATCH_BYTES,
    MAX_FILE_BYTES,
    apply_tombstones,
    diff_inventories,
    file_sha256,
    local_inventory,
    tracked_local_state,
)

HOST = "0.0.0.0"
PORT = 8090
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, list[float]] = {}


def _rate_ok(address: str, *, limit: int = 120, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        recent = [seen for seen in _RATE_BUCKETS.get(address, []) if now - seen < window]
        if len(recent) >= limit:
            _RATE_BUCKETS[address] = recent
            return False
        recent.append(now)
        _RATE_BUCKETS[address] = recent
        return True


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    cfg = paths.load_config()
    sync_cfg = cfg.get("sync") or {}
    if not bool(sync_cfg.get("allow_legacy_token", False)):
        return False
    token = sync_cfg.get("token") or ""
    if not token:
        return True
    auth = handler.headers.get("Authorization", "")
    if auth == f"Bearer {token}":
        return True
    return handler.headers.get("X-Sync-Token", "") == token


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    _add_response_auth_headers(handler, code, body)
    handler.end_headers()
    handler.wfile.write(body)


def _add_response_auth_headers(handler: BaseHTTPRequestHandler, code: int, body: bytes) -> None:
    nonce = getattr(handler, "_request_nonce", None)
    identity = getattr(handler, "_server_identity", None)
    if nonce and identity:
        for key, value in sign_response(identity, code, body, nonce).items():
            handler.send_header(key, value)


def _read_body(handler: BaseHTTPRequestHandler, limit: int = MAX_BATCH_BYTES) -> bytes:
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError as exc:
        raise ValueError("invalid content length") from exc
    if length <= 0:
        return b""
    if length > limit:
        raise ValueError("request payload too large")
    return handler.rfile.read(length)


def _is_v2(path: str) -> bool:
    return path == "/v2" or path.startswith("/v2/")


def _legacy_or_reject(handler: BaseHTTPRequestHandler) -> bool:
    if _auth_ok(handler):
        return True
    _json_response(handler, 401, {"error": "legacy token sync is disabled or unauthorized"})
    return False


def _authorize_v2(
    handler: BaseHTTPRequestHandler,
    parsed: Any,
    body: bytes,
    required_scope: str,
) -> bool:
    if not is_direct_address(handler.client_address[0]):
        _json_response(handler, 403, {"error": "direct LAN/WireGuard peers only"})
        return False
    try:
        store = IdentityStore()
        identity = store.load_or_create_identity()
        verify_request(
            store,
            handler.command,
            parsed.path,
            parsed.query,
            body,
            handler.headers,
            required_scope,
        )
        handler._request_nonce = handler.headers[NONCE_HEADER]
        handler._server_identity = identity
        return True
    except PeerAuthError as exc:
        _json_response(handler, 401, {"error": str(exc)})
        return False


def _safe_vault_path(rel: str) -> Path:
    return paths.vault_abs(rel)


def _conflict_path(rel: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    p = Path(rel)
    stem = p.with_suffix("")
    return f"{stem}.conflict-{ts}{p.suffix}".replace("\\", "/")


def build_manifest() -> dict[str, Any]:
    files, tombstones = tracked_local_state(paths.VAULT_ROOT)
    return {
        "vault_root": str(paths.VAULT_ROOT),
        "roots": list(paths.SYNC_ROOTS),
        "count": len(files),
        "files": files,
        "tombstones": tombstones,
    }


def write_vault_file(rel: str, data: bytes, mtime: float | None = None) -> dict[str, Any]:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file exceeds maximum sync size")
    dest = _safe_vault_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, dest)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    if mtime is not None:
        try:
            os.utime(dest, (mtime, mtime))
        except OSError:
            pass
    st = dest.stat()
    return {
        "path": rel,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "sha256": file_sha256(dest),
    }


def handle_push_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply inbound files; newer wins; same-mtime different hash -> .conflict-<ts>."""
    local = local_inventory()
    pushed: list[str] = []
    conflicts: list[dict[str, str]] = []
    skipped: list[str] = []
    ts = int(time.time())
    total_bytes = 0

    for item in files:
        rel = item.get("path")
        if not rel:
            continue
        content_b64 = item.get("content_b64")
        if content_b64 is None:
            continue
        try:
            data = base64.b64decode(content_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid base64 file content") from exc
        total_bytes += len(data)
        if len(data) > MAX_FILE_BYTES or total_bytes > MAX_BATCH_BYTES:
            raise ValueError("sync payload exceeds size limit")
        incoming_mtime = float(item.get("mtime") or time.time())
        incoming_hash = item.get("sha256") or hashlib.sha256(data).hexdigest()

        existing = local.get(rel)
        if existing:
            if existing.get("sha256") == incoming_hash:
                skipped.append(rel)
                continue
            local_mtime = float(existing.get("mtime", 0))
            if abs(local_mtime - incoming_mtime) < 1e-3:
                # Conflict: keep local as .conflict, write incoming as primary.
                conflict_rel = _conflict_path(rel, ts)
                src = _safe_vault_path(rel)
                if src.exists():
                    write_vault_file(conflict_rel, src.read_bytes(), src.stat().st_mtime)
                    conflicts.append({"path": rel, "saved_as": conflict_rel})
            elif local_mtime > incoming_mtime:
                # Local newer — keep local, stash incoming as conflict copy.
                conflict_rel = _conflict_path(rel, ts)
                write_vault_file(conflict_rel, data, incoming_mtime)
                conflicts.append({"path": rel, "incoming_saved_as": conflict_rel})
                skipped.append(rel)
                continue

        write_vault_file(rel, data, incoming_mtime)
        pushed.append(rel)

    return {
        "ok": True,
        "pushed": pushed,
        "count": len(pushed),
        "skipped": skipped,
        "conflicts": conflicts,
    }


class SyncHandler(BaseHTTPRequestHandler):
    server_version = "MotherbrainSync/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[sync] {self.address_string()} - {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        if not _rate_ok(self.client_address[0]):
            _json_response(self, 429, {"error": "rate limit exceeded"})
            return
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") == "/health":
            _json_response(
                self,
                200,
                {"ok": True, "service": "occhialini-sync", "protocol": "v2"},
            )
            return
        v2 = _is_v2(parsed.path)
        if v2:
            if not _authorize_v2(self, parsed, b"", "sync:read"):
                return
            route = parsed.path[3:].rstrip("/") or "/"
        else:
            if not _legacy_or_reject(self):
                return
            route = parsed.path.rstrip("/") or "/"

        if route == "/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "motherbrain-sync",
                    "vault": str(paths.VAULT_ROOT),
                    "role": paths.load_config().get("role"),
                },
            )
            return

        if route == "/manifest":
            _json_response(self, 200, build_manifest())
            return

        if route == "/file":
            qs = parse_qs(parsed.query)
            rel = (qs.get("path") or [None])[0]
            if not rel:
                _json_response(self, 400, {"error": "missing path"})
                return
            try:
                path = _safe_vault_path(rel)
            except ValueError as e:
                _json_response(self, 400, {"error": str(e)})
                return
            if not path.is_file():
                _json_response(self, 404, {"error": "not found", "path": rel})
                return
            data = path.read_bytes()
            st = path.stat()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Vault-Path", rel)
            self.send_header("X-Vault-Mtime", str(st.st_mtime))
            self.send_header("X-Vault-Size", str(st.st_size))
            self.send_header("X-Vault-Sha256", file_sha256(path))
            _add_response_auth_headers(self, 200, data)
            self.end_headers()
            self.wfile.write(data)
            return

        _json_response(self, 404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        if not _rate_ok(self.client_address[0]):
            _json_response(self, 429, {"error": "rate limit exceeded"})
            return
        parsed = urlparse(self.path)
        try:
            data = _read_body(self, MAX_FILE_BYTES)
        except ValueError as exc:
            _json_response(self, 413, {"error": str(exc)})
            return
        if _is_v2(parsed.path):
            if not _authorize_v2(self, parsed, data, "sync:write"):
                return
            route = parsed.path[3:].rstrip("/") or "/"
        else:
            if not _legacy_or_reject(self):
                return
            route = parsed.path.rstrip("/") or "/"
        if route != "/file":
            _json_response(self, 404, {"error": "not found"})
            return

        qs = parse_qs(parsed.query)
        rel = (qs.get("path") or [None])[0] or self.headers.get("X-Vault-Path")
        if not rel:
            _json_response(self, 400, {"error": "missing path"})
            return
        try:
            _safe_vault_path(rel)
        except ValueError as e:
            _json_response(self, 400, {"error": str(e)})
            return

        mtime_hdr = self.headers.get("X-Vault-Mtime")
        try:
            mtime = float(mtime_hdr) if mtime_hdr else None
            info = write_vault_file(rel, data, mtime)
            _json_response(self, 200, {"ok": True, **info})
        except (OSError, ValueError) as e:
            _json_response(self, 500, {"error": str(e)})

    def do_POST(self) -> None:  # noqa: N802
        if not _rate_ok(
            self.client_address[0],
            limit=20 if self.path.startswith("/v2/pair/") else 120,
        ):
            _json_response(self, 429, {"error": "rate limit exceeded"})
            return
        parsed = urlparse(self.path)
        try:
            raw = _read_body(self)
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            code = 413 if "large" in str(exc) else 400
            _json_response(self, code, {"error": str(exc) or "invalid json"})
            return
        if parsed.path.startswith("/v2/pair/"):
            self._handle_pairing(parsed.path, body)
            return
        if _is_v2(parsed.path):
            scope = (
                "sync:write"
                if parsed.path.endswith(("/sync/push", "/sync/tombstones"))
                else "sync:read"
            )
            if not _authorize_v2(self, parsed, raw, scope):
                return
            route = parsed.path[3:].rstrip("/") or "/"
        else:
            if not _legacy_or_reject(self):
                return
            route = parsed.path.rstrip("/") or "/"

        if route == "/sync/pull":
            # Client asks server for file contents to pull down.
            want = body.get("paths")
            if want is None:
                # Diff against client inventory if provided.
                client_inv = body.get("inventory") or {}
                server_inv = local_inventory()
                want = diff_inventories(client_inv, server_inv)["pull"]
            files = []
            total_bytes = 0
            for rel in want or []:
                try:
                    path = _safe_vault_path(rel)
                except ValueError:
                    continue
                if not path.is_file():
                    continue
                st = path.stat()
                if st.st_size > MAX_FILE_BYTES or total_bytes + st.st_size > MAX_BATCH_BYTES:
                    _json_response(self, 413, {"error": "requested files exceed sync size limit"})
                    return
                total_bytes += st.st_size
                files.append(
                    {
                        "path": rel,
                        "mtime": st.st_mtime,
                        "size": st.st_size,
                        "sha256": file_sha256(path),
                        "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                )
            _json_response(self, 200, {"ok": True, "files": files, "count": len(files)})
            return

        if route == "/sync/push":
            files = body.get("files") or []
            if not isinstance(files, list):
                _json_response(self, 400, {"error": "files must be a list"})
                return
            try:
                result = handle_push_files(files)
                _json_response(self, 200, result)
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
            return

        if route == "/sync/tombstones":
            tombstones = body.get("tombstones") or {}
            if not isinstance(tombstones, dict) or len(tombstones) > 10000:
                _json_response(self, 400, {"error": "invalid tombstones"})
                return
            try:
                deleted = apply_tombstones(
                    {str(rel): float(timestamp) for rel, timestamp in tombstones.items()},
                    paths.VAULT_ROOT,
                )
                _json_response(self, 200, {"ok": True, "deleted": deleted, "count": len(deleted)})
            except (TypeError, ValueError, OSError) as exc:
                _json_response(self, 400, {"error": str(exc)})
            return

        _json_response(self, 404, {"error": "not found"})

    def _handle_pairing(self, route: str, body: dict[str, Any]) -> None:
        """Unauthenticated bootstrap protected by a short-lived signed secret."""
        try:
            sessions = PairingSessionStore()
            if route == "/v2/pair/open":
                if self.client_address[0] not in {"127.0.0.1", "::1"}:
                    raise PeerAuthError("pairing windows can only be opened locally")
                identity = IdentityStore().load_or_create_identity()
                configured = paths.load_config().get("sync") or {}
                server_url = str(body.get("server_url") or configured.get("public_url") or "")
                if not server_url:
                    raise PeerAuthError("server_url is required for a connection key")
                key, invitation = encode_connection_key(identity, server_url)
                sessions.open(invitation)
                _json_response(
                    self,
                    200,
                    {
                        "connection_key": key,
                        "session_id": invitation["session_id"],
                        "secret": invitation["secret"],
                        "expires_at": invitation["expires_at"],
                    },
                )
                return
            if route == "/v2/pair/join":
                status = sessions.join(body)
                _json_response(self, 200, status)
                return
            if route == "/v2/pair/status":
                status = sessions.public_status(str(body.get("session_id", "")), str(body.get("secret", "")))
                _json_response(self, 200, status)
                return
            if route == "/v2/pair/confirm":
                side = str(body.get("side", ""))
                if side == "host" and self.client_address[0] not in {"127.0.0.1", "::1"}:
                    raise PeerAuthError("host confirmation must happen on the host")
                session_id = str(body.get("session_id", ""))
                secret = str(body.get("secret", ""))
                status = sessions.confirm(
                    session_id,
                    secret,
                    side,
                    str(body.get("sas", "")),
                )
                guest = sessions.completed_guest(session_id, secret)
                if guest:
                    IdentityStore().trust_peer(
                        str(guest["guest_device_id"]),
                        str(guest["guest_public_key"]),
                        str(guest.get("guest_name") or ""),
                    )
                _json_response(self, 200, status)
                return
            _json_response(self, 404, {"error": "pairing endpoint not found"})
        except PeerAuthError as exc:
            _json_response(self, 400, {"error": str(exc)})


def main() -> None:
    paths.ensure_config()
    paths.ensure_dirs()
    addr = (HOST, PORT)
    httpd = ThreadingHTTPServer(addr, SyncHandler)
    print(f"[sync] Motherbrain sync server on http://{HOST}:{PORT}")
    print(f"[sync] Vault: {paths.VAULT_ROOT}")
    print(f"[sync] Roots: {', '.join(paths.SYNC_ROOTS)}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[sync] Shutting down.")
        httpd.server_close()


if __name__ == "__main__":
    main()
