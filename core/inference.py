"""llama.cpp /completion client with local or remote URL + start/stop helpers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from . import paths

_server_process: subprocess.Popen | None = None


def get_completion_url(cfg: dict[str, Any] | None = None) -> str:
    return paths.completion_url(cfg)


def is_ready(cfg: dict[str, Any] | None = None, timeout: float = 3.0) -> bool:
    url = get_completion_url(cfg)
    try:
        r = requests.post(
            url,
            json={"prompt": "test", "n_predict": 1, "temperature": 0.0},
            timeout=timeout,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def complete(
    prompt: str,
    *,
    n_predict: int = 2048,
    temperature: float = 0.7,
    cfg: dict[str, Any] | None = None,
    timeout: float = 180.0,
    extra: dict[str, Any] | None = None,
) -> str:
    """POST to llama.cpp /completion and return content text."""
    cfg = cfg or paths.load_config()
    url = get_completion_url(cfg)
    body: dict[str, Any] = {
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": temperature,
    }
    if extra:
        body.update(extra)
    resp = requests.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("content", "")
    if isinstance(content, str):
        text = content.strip()
    else:
        text = str(content).strip()
    # Strip accidental role echoes from completion-style models.
    if text.startswith("User:") or text.startswith("Assistant:"):
        text = text.split("\n", 1)[-1].strip() if "\n" in text else text
    return text


def start_server(
    *,
    model: Path | str | None = None,
    cfg: dict[str, Any] | None = None,
    llama_bin: Path | str | None = None,
    wait_seconds: int = 90,
) -> bool:
    """Start local llama-server from config (no-op if inference.mode == remote)."""
    global _server_process
    cfg = cfg or paths.load_config()
    inf = cfg.get("inference") or {}
    if str(inf.get("mode", "local")).lower() == "remote":
        return is_ready(cfg)

    # Already serving (e.g. started outside this process).
    if is_ready(cfg, timeout=2.0):
        return True

    model_path = Path(model) if model else paths.active_model_path(cfg)
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    bin_path = Path(llama_bin) if llama_bin else paths.resolve_llama_server()
    if not bin_path.exists():
        raise FileNotFoundError(
            f"llama-server not found: {bin_path}. "
            "Install CUDA builds to ~/llama.cpp/build/bin/ "
            "(e.g. ggml-org/llama.cpp win-cuda release)."
        )

    # Parse host/port from configured URL (default 127.0.0.1:8081).
    base = paths.inference_base_url(cfg)
    host, port = "127.0.0.1", "8081"
    if "://" in base:
        rest = base.split("://", 1)[1]
        if ":" in rest:
            host, port = rest.split(":", 1)
            port = port.split("/")[0]
        else:
            host = rest.split("/")[0]

    ngl = str(inf.get("ngl", 99))
    ctx = str(inf.get("ctx", 8192))

    stop_server()
    _server_process = subprocess.Popen(
        [
            str(bin_path),
            "-m",
            str(model_path),
            "--host",
            host,
            "--port",
            str(port),
            "-ngl",
            ngl,
            "-c",
            ctx,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(max(1, wait_seconds)):
        if is_ready(cfg):
            return True
        time.sleep(1)
    return False


def stop_server() -> None:
    """Terminate the locally spawned llama-server process, if any."""
    global _server_process
    if _server_process is not None:
        try:
            _server_process.terminate()
            _server_process.wait(timeout=5)
        except Exception:
            try:
                _server_process.kill()
            except Exception:
                pass
        _server_process = None


def server_process() -> subprocess.Popen | None:
    return _server_process
