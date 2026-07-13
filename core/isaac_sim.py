"""Isaac Sim bridge client for Motherbrain.

Architecture
------------
Motherbrain (this process) speaks a small JSON-line protocol over TCP to an
optional bridge that runs *inside* Isaac Sim (see ``isaac_sim/bridge_server.py``).

    ┌─────────────────┐  TCP :8765   ┌──────────────────────────┐
    │  Motherbrain    │─────────────▶│  Isaac Sim bridge script │
    │  workstation /  │  JSON lines  │  (runs in Isaac Python)  │
    │  companion AI   │◀─────────────│  → scene / articulations │
    └─────────────────┘              └──────────────────────────┘

Optional ROS 2 path (same config flag): set ``transport`` to ``ros2`` and run
Isaac's ``isaacsim.ros2.bridge`` with matching ``ROS_DOMAIN_ID``. Motherbrain
still uses this TCP control plane for status/commands; ROS is for high-rate
sensor/actuator topics you wire in Sim.

The bridge never embeds Isaac Sim — it only connects when Sim (or a mock) is up.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from typing import Any

from . import paths

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 3.0

# Protocol version spoken by both sides.
PROTOCOL = "motherbrain.isaac.v1"


@dataclass
class IsaacStatus:
    connected: bool
    host: str
    port: int
    sim_running: bool = False
    stage_path: str = ""
    bridge_version: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "sim_running": self.sim_running,
            "stage_path": self.stage_path,
            "bridge_version": self.bridge_version,
            "detail": self.detail,
        }


_lock = threading.Lock()


def load_isaac_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or paths.load_config()
    section = cfg.get("isaac_sim") or {}
    return {
        "enabled": bool(section.get("enabled", False)),
        "host": str(section.get("host") or DEFAULT_HOST),
        "port": int(section.get("port") or DEFAULT_PORT),
        "timeout": float(section.get("timeout") or DEFAULT_TIMEOUT),
        "transport": str(section.get("transport") or "tcp"),  # tcp | ros2
        "ros_domain_id": int(section.get("ros_domain_id") or 0),
        "default_robot_prim": str(section.get("default_robot_prim") or "/World/Robot"),
    }


def _request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send one JSON-RPC-ish request; return parsed response dict."""
    isaac = load_isaac_config(cfg)
    if not isaac["enabled"]:
        return {"ok": False, "error": "isaac_sim.enabled is false in config"}

    host, port = isaac["host"], isaac["port"]
    timeout = isaac["timeout"]
    payload = {
        "v": PROTOCOL,
        "method": method,
        "params": params or {},
    }
    line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    with _lock:
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(line)
                buf = b""
                while b"\n" not in buf:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            return {
                "ok": False,
                "error": f"Cannot reach Isaac bridge at {host}:{port}: {e}",
            }

    if not buf.strip():
        return {"ok": False, "error": "Empty response from Isaac bridge"}

    try:
        data = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Bad JSON from bridge: {e}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "Bridge response was not an object"}
    return data


def ping(cfg: dict[str, Any] | None = None) -> IsaacStatus:
    """Probe the bridge; never raises."""
    isaac = load_isaac_config(cfg)
    host, port = isaac["host"], isaac["port"]
    if not isaac["enabled"]:
        return IsaacStatus(
            connected=False,
            host=host,
            port=port,
            detail="Isaac Sim bridge disabled in config",
        )

    resp = _request("ping", cfg=cfg)
    if not resp.get("ok"):
        return IsaacStatus(
            connected=False,
            host=host,
            port=port,
            detail=str(resp.get("error") or "ping failed"),
        )
    result = resp.get("result") or {}
    return IsaacStatus(
        connected=True,
        host=host,
        port=port,
        sim_running=bool(result.get("sim_running", True)),
        stage_path=str(result.get("stage_path") or ""),
        bridge_version=str(result.get("bridge_version") or ""),
        detail="online",
    )


def get_scene_summary(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request("get_scene", cfg=cfg)


def list_prims(path: str = "/World", cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request("list_prims", {"path": path}, cfg=cfg)


def set_joint_targets(
    targets: dict[str, float],
    *,
    robot_prim: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    isaac = load_isaac_config(cfg)
    prim = robot_prim or isaac["default_robot_prim"]
    return _request(
        "set_joint_targets",
        {"robot_prim": prim, "targets": targets},
        cfg=cfg,
    )


def play(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request("play", cfg=cfg)


def pause(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request("pause", cfg=cfg)


def reset(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return _request("reset", cfg=cfg)


def call(
    method: str,
    params: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generic escape hatch for custom bridge methods."""
    return _request(method, params, cfg=cfg)


def describe_for_prompt(cfg: dict[str, Any] | None = None) -> str:
    """One-liner block for companion system prompts."""
    isaac = load_isaac_config(cfg)
    if not isaac["enabled"]:
        return "Isaac Sim: disabled"
    status = ping(cfg)
    if not status.connected:
        return (
            f"Isaac Sim: enabled but offline "
            f"({status.host}:{status.port}) — {status.detail}"
        )
    stage = status.stage_path or "(unknown stage)"
    return (
        f"Isaac Sim: online via {isaac['transport']} "
        f"({status.host}:{status.port}), stage={stage}, "
        f"sim_running={status.sim_running}"
    )
