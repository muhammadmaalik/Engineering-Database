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
from core.sync import diff_inventories, file_sha256, local_inventory  # noqa: E402

HOST = "0.0.0.0"
PORT = 8090


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    cfg = paths.load_config()
    token = (cfg.get("sync") or {}).get("token") or ""
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
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def _safe_vault_path(rel: str) -> Path:
    return paths.vault_abs(rel)


def _conflict_path(rel: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    p = Path(rel)
    stem = p.with_suffix("")
    return f"{stem}.conflict-{ts}{p.suffix}".replace("\\", "/")


def build_manifest() -> dict[str, Any]:
    files = local_inventory(paths.VAULT_ROOT)
    return {
        "vault_root": str(paths.VAULT_ROOT),
        "roots": list(paths.SYNC_ROOTS),
        "count": len(files),
        "files": files,
    }


def write_vault_file(rel: str, data: bytes, mtime: float | None = None) -> dict[str, Any]:
    dest = _safe_vault_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
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

    for item in files:
        rel = item.get("path")
        if not rel:
            continue
        content_b64 = item.get("content_b64")
        if content_b64 is None:
            continue
        data = base64.b64decode(content_b64)
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
                    conflict_abs = _safe_vault_path(conflict_rel)
                    conflict_abs.parent.mkdir(parents=True, exist_ok=True)
                    conflict_abs.write_bytes(src.read_bytes())
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
    server_version = "MotherbrainSync/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[sync] {self.address_string()} - {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        if not _auth_ok(self):
            _json_response(self, 401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
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
            self.end_headers()
            self.wfile.write(data)
            return

        _json_response(self, 404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        if not _auth_ok(self):
            _json_response(self, 401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
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

        data = _read_body(self)
        mtime_hdr = self.headers.get("X-Vault-Mtime")
        mtime = float(mtime_hdr) if mtime_hdr else None
        try:
            info = write_vault_file(rel, data, mtime)
            _json_response(self, 200, {"ok": True, **info})
        except Exception as e:
            _json_response(self, 500, {"error": str(e)})

    def do_POST(self) -> None:  # noqa: N802
        if not _auth_ok(self):
            _json_response(self, 401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            raw = _read_body(self)
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, {"error": "invalid json"})
            return

        if route == "/sync/pull":
            # Client asks server for file contents to pull down.
            want = body.get("paths")
            if want is None:
                # Diff against client inventory if provided.
                client_inv = body.get("inventory") or {}
                server_inv = local_inventory()
                want = diff_inventories(client_inv, server_inv)["pull"]
            files = []
            for rel in want or []:
                try:
                    path = _safe_vault_path(rel)
                except ValueError:
                    continue
                if not path.is_file():
                    continue
                st = path.stat()
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
            result = handle_push_files(files)
            _json_response(self, 200, result)
            return

        _json_response(self, 404, {"error": "not found"})


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
