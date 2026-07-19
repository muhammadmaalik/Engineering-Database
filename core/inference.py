"""llama.cpp /completion client with local or remote URL + start/stop helpers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from . import paths

_server_process: subprocess.Popen | None = None
_server_model_path: Path | None = None
_server_log_handle: Any | None = None

# Chat-friendly defaults for ~8GB VRAM (partial offload on 32B).
DEFAULT_N_PREDICT = 512
DEFAULT_TIMEOUT = 300.0


def get_completion_url(cfg: dict[str, Any] | None = None) -> str:
    return paths.completion_url(cfg)


def get_health_url(cfg: dict[str, Any] | None = None) -> str:
    return paths.health_url(cfg)


def is_ready(cfg: dict[str, Any] | None = None, timeout: float = 3.0) -> bool:
    """True when llama-server answers GET /health (not a slow /completion probe).

    Using /completion previously timed out on large models (e.g. Qwen 32B) even
    when the server was already up, so the GUI stayed offline / \"No Model\".
    """
    url = get_health_url(cfg)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return False
        # Prefer JSON {"status":"ok"} when present; accept plain 200 otherwise.
        try:
            data = r.json()
            if isinstance(data, dict) and "status" in data:
                return str(data.get("status", "")).lower() in ("ok", "healthy", "ready")
        except ValueError:
            pass
        return True
    except requests.RequestException:
        return False


def served_model_path(cfg: dict[str, Any] | None = None) -> Path | None:
    """Best-effort model path from llama-server's metadata endpoint."""
    try:
        response = requests.get(f"{paths.inference_base_url(cfg)}/props", timeout=2.0)
        response.raise_for_status()
        data = response.json()
        value = data.get("model_path") or data.get("model")
        if not value and isinstance(data.get("default_generation_settings"), dict):
            value = data["default_generation_settings"].get("model")
        return Path(value).resolve() if value else None
    except (requests.RequestException, ValueError, TypeError, OSError):
        return None


def complete(
    prompt: str,
    *,
    n_predict: int = DEFAULT_N_PREDICT,
    temperature: float = 0.7,
    cfg: dict[str, Any] | None = None,
    timeout: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """POST to llama.cpp /completion and return content text."""
    cfg = cfg or paths.load_config()
    inf = cfg.get("inference") or {}
    if timeout is None:
        try:
            timeout = float(inf.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
    url = get_completion_url(cfg)
    body: dict[str, Any] = {
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": temperature,
    }
    if extra:
        body.update(extra)
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.ReadTimeout as e:
        raise TimeoutError(
            f"Model busy/slow: no token within {timeout:.0f}s. "
            "Wait for other requests to finish, lower ctx/n_predict, "
            "disable tools (web.allow_tools), or use a smaller GGUF."
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            f"Cannot reach llama-server at {url}. Is Start AI running?"
        ) from e
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
    force: bool = False,
) -> bool:
    """Start local llama-server from config (no-op if inference.mode == remote).

    When ``force`` is True, stop any locally tracked process and start fresh
    even if /health already answers (used after config changes).
    """
    global _server_process, _server_model_path, _server_log_handle
    cfg = cfg or paths.load_config()
    inf = cfg.get("inference") or {}
    if str(inf.get("mode", "local")).lower() == "remote":
        return is_ready(cfg)

    # Never silently report an old model as the newly selected one.
    if is_ready(cfg, timeout=2.0):
        expected = paths.active_model_path(cfg).resolve()
        actual = _server_model_path or served_model_path(cfg)
        if not force and (actual is None or actual == expected):
            return True
        if _server_process is None:
            raise RuntimeError(
                "A different llama-server is already using the configured port. "
                "Stop it before activating another model."
            )

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

    ngl = str(inf.get("ngl", 28))
    ctx = str(inf.get("ctx", 2048))
    # One slot: multi-slot auto on 8GB GPUs fragments KV and slows 32B badly.
    parallel = str(inf.get("parallel", 1))

    stop_server()
    cmd = [
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
        "-np",
        parallel,
        "-fa",
        "on",
    ]
    log_path = paths.MOTHERBRAIN_DIR / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _server_log_handle = open(log_path, "ab")
    _server_process = subprocess.Popen(
        cmd,
        stdout=_server_log_handle,
        stderr=subprocess.STDOUT,
    )
    _server_model_path = model_path.resolve()

    for i in range(max(1, wait_seconds)):
        if is_ready(cfg):
            return True
        if _server_process.poll() is not None:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
            except Exception:
                pass
            raise RuntimeError(
                f"llama-server exited early (code {_server_process.returncode}). "
                f"See {log_path}:\n{tail}"
            )
        time.sleep(1)
    return False


def stop_server() -> None:
    """Terminate the locally spawned llama-server process, if any."""
    global _server_process, _server_model_path, _server_log_handle
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
    _server_model_path = None
    if _server_log_handle is not None:
        try:
            _server_log_handle.close()
        except Exception:
            pass
        _server_log_handle = None


def activate_model(
    model: Path | str,
    *,
    start: bool = True,
    wait_seconds: int = 90,
    **settings: Any,
) -> dict[str, Any]:
    """Explicitly activate a GGUF and restart the managed server if requested."""
    from . import models

    candidate = Path(model)
    resolved = candidate if candidate.is_absolute() else paths.MODELS_DIR / candidate.name
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    if is_ready(timeout=2.0) and _server_process is None:
        current = served_model_path()
        if current is None or current != resolved.resolve():
            raise RuntimeError(
                "An externally managed llama-server is active. Stop it before changing models."
            )
    stop_server()
    selected = models.set_active_model(str(resolved), **settings)
    if start:
        selected["server_ready"] = start_server(
            model=resolved,
            cfg=paths.load_config(),
            wait_seconds=wait_seconds,
            force=True,
        )
    else:
        selected["server_ready"] = False
    return selected


def server_process() -> subprocess.Popen | None:
    return _server_process
