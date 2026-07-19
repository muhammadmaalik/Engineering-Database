"""In-process lifecycle for the stdlib sync server used by frozen apps."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from typing import Any

_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def start(host: str = "0.0.0.0", port: int = 8090) -> dict[str, Any]:
    global _server, _thread
    with _lock:
        if _server and _thread and _thread.is_alive():
            return {"ok": True, "host": host, "port": port, "already_running": True}
        from sync_server import SyncHandler

        _server = ThreadingHTTPServer((host, int(port)), SyncHandler)
        _thread = threading.Thread(
            target=_server.serve_forever,
            name="occhialini-sync-server",
            daemon=True,
        )
        _thread.start()
        return {
            "ok": True,
            "host": _server.server_address[0],
            "port": _server.server_address[1],
            "already_running": False,
        }


def stop() -> None:
    global _server, _thread
    with _lock:
        if _server:
            _server.shutdown()
            _server.server_close()
        _server = None
        _thread = None


def status() -> dict[str, Any]:
    running = bool(_server and _thread and _thread.is_alive())
    return {
        "running": running,
        "address": _server.server_address if running and _server else None,
    }
