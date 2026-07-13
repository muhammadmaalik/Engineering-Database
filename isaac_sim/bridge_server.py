"""Isaac Sim ↔ Motherbrain TCP bridge (run inside Isaac Sim's Python).

Usage (from an Isaac Sim Script Editor / standalone workflow)::

    # After SimulationApp is created and the stage is loaded:
    exec(open(r"C:\\path\\to\\motherbrain\\isaac_sim\\bridge_server.py").read())
    # or:
    #   from isaac_sim.bridge_server import BridgeServer
    #   BridgeServer(host="127.0.0.1", port=8765).serve_forever_background()

Motherbrain connects as a TCP client (see ``core/isaac_sim.py``). Each request
is one JSON object per line; each response is one JSON object per line.

This module degrades gracefully when Omniverse APIs are missing so you can
smoke-test the protocol with a plain ``python isaac_sim/bridge_server.py``.
"""

from __future__ import annotations

import json
import socket
import threading
import traceback
from typing import Any, Callable

PROTOCOL = "motherbrain.isaac.v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
BRIDGE_VERSION = "1.0.0"


def _try_import_omni():
    try:
        import omni.usd  # type: ignore
        import omni.timeline  # type: ignore

        return omni.usd, omni.timeline
    except Exception:
        return None, None


class BridgeServer:
    """JSON-line TCP server that maps methods onto Isaac Sim (or a mock)."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "ping": self._ping,
            "get_scene": self._get_scene,
            "list_prims": self._list_prims,
            "set_joint_targets": self._set_joint_targets,
            "play": self._play,
            "pause": self._pause,
            "reset": self._reset,
        }
        # Mock state when Omniverse is not present
        self._mock_playing = False
        self._mock_joints: dict[str, float] = {}
        self._mock_prims = ["/World", "/World/Robot", "/World/Ground"]

    def register(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._handlers[method] = handler

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("v") and request["v"] != PROTOCOL:
            return {
                "ok": False,
                "error": f"Unsupported protocol {request.get('v')}; want {PROTOCOL}",
            }
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return {"ok": False, "error": "params must be an object"}
        handler = self._handlers.get(method)
        if not handler:
            return {
                "ok": False,
                "error": f"Unknown method '{method}'. "
                f"Known: {', '.join(sorted(self._handlers))}",
            }
        try:
            result = handler(params)
            return {"ok": True, "method": method, "result": result}
        except Exception as e:
            return {
                "ok": False,
                "method": method,
                "error": str(e),
                "trace": traceback.format_exc()[-800:],
            }

    # ── handlers ──────────────────────────────────────────────

    def _ping(self, _params: dict[str, Any]) -> dict[str, Any]:
        omni_usd, omni_timeline = _try_import_omni()
        stage_path = ""
        sim_running = self._mock_playing
        if omni_usd is not None:
            ctx = omni_usd.get_context()
            stage = ctx.get_stage() if ctx else None
            if stage is not None:
                stage_path = stage.GetRootLayer().identifier
            if omni_timeline is not None:
                tl = omni_timeline.get_timeline_interface()
                sim_running = bool(tl.is_playing())
        return {
            "bridge_version": BRIDGE_VERSION,
            "sim_running": sim_running,
            "stage_path": stage_path or "(mock)",
            "backend": "omni" if omni_usd is not None else "mock",
        }

    def _get_scene(self, _params: dict[str, Any]) -> dict[str, Any]:
        ping = self._ping({})
        omni_usd, _ = _try_import_omni()
        prim_count = 0
        if omni_usd is not None:
            ctx = omni_usd.get_context()
            stage = ctx.get_stage() if ctx else None
            if stage is not None:
                prim_count = sum(1 for _ in stage.Traverse())
        else:
            prim_count = len(self._mock_prims)
        return {
            **ping,
            "prim_count": prim_count,
            "joint_targets": dict(self._mock_joints),
        }

    def _list_prims(self, params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or "/World")
        omni_usd, _ = _try_import_omni()
        names: list[str] = []
        if omni_usd is not None:
            from pxr import Usd  # type: ignore

            ctx = omni_usd.get_context()
            stage = ctx.get_stage() if ctx else None
            if stage is None:
                raise RuntimeError("No USD stage open in Isaac Sim")
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                raise RuntimeError(f"Prim not found: {path}")
            for child in prim.GetChildren():
                names.append(str(child.GetPath()))
        else:
            prefix = path.rstrip("/")
            names = [
                p
                for p in self._mock_prims
                if p.startswith(prefix + "/") and p.count("/") == prefix.count("/") + 1
            ] or [p for p in self._mock_prims if p.startswith(prefix)]
        return {"path": path, "children": names}

    def _set_joint_targets(self, params: dict[str, Any]) -> dict[str, Any]:
        robot_prim = str(params.get("robot_prim") or "/World/Robot")
        targets = params.get("targets") or {}
        if not isinstance(targets, dict) or not targets:
            raise ValueError("targets must be a non-empty {joint_name: angle} object")

        # Always record for status / mock mode.
        for k, v in targets.items():
            self._mock_joints[str(k)] = float(v)

        omni_usd, _ = _try_import_omni()
        applied = "mock"
        if omni_usd is not None:
            # Prefer Isaac articulation APIs when available; fall back to USD attrs.
            try:
                from isaacsim.core.prims import SingleArticulation  # type: ignore

                art = SingleArticulation(prim_path=robot_prim)
                art.initialize()
                names = list(art.dof_names)
                positions = list(art.get_joint_positions())
                for joint, value in targets.items():
                    if joint in names:
                        positions[names.index(joint)] = float(value)
                art.set_joint_positions(positions)
                applied = "articulation"
            except Exception:
                # Soft-fail: still return ok with mock-recorded targets so the
                # Motherbrain side can develop against a live Sim without
                # requiring a full robot articulation setup.
                applied = "recorded_only"
        return {
            "robot_prim": robot_prim,
            "targets": dict(self._mock_joints),
            "applied": applied,
        }

    def _play(self, _params: dict[str, Any]) -> dict[str, Any]:
        _, omni_timeline = _try_import_omni()
        if omni_timeline is not None:
            omni_timeline.get_timeline_interface().play()
        self._mock_playing = True
        return {"sim_running": True}

    def _pause(self, _params: dict[str, Any]) -> dict[str, Any]:
        _, omni_timeline = _try_import_omni()
        if omni_timeline is not None:
            omni_timeline.get_timeline_interface().pause()
        self._mock_playing = False
        return {"sim_running": False}

    def _reset(self, _params: dict[str, Any]) -> dict[str, Any]:
        _, omni_timeline = _try_import_omni()
        if omni_timeline is not None:
            tl = omni_timeline.get_timeline_interface()
            tl.stop()
            tl.set_current_time(0.0)
        self._mock_playing = False
        self._mock_joints.clear()
        return {"sim_running": False, "reset": True}

    # ── networking ────────────────────────────────────────────

    def _serve_client(self, conn: socket.socket, addr: tuple) -> None:
        with conn:
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    try:
                        req = json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        resp = {"ok": False, "error": f"bad json: {e}"}
                    else:
                        if not isinstance(req, dict):
                            resp = {"ok": False, "error": "request must be an object"}
                        else:
                            resp = self.handle(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def serve_forever(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        srv.settimeout(1.0)
        print(f"[motherbrain-isaac] listening on {self.host}:{self.port} ({PROTOCOL})")
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._serve_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
        finally:
            srv.close()

    def serve_forever_background(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Motherbrain ↔ Isaac Sim bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    BridgeServer(host=args.host, port=args.port).serve_forever()


if __name__ == "__main__":
    main()
