# Motherbrain

Local-first AI companion platform for custom hardware projects. Hybrid topology: laptop runs a local 32B model by default; home PC hosts the vault sync server and heavier GPU inference. Vault syncs both ways over the LAN/VPN.

## Architecture

- **kernel/** — C++20 daemon. Shared memory IPC, hardware abstraction, inference scheduler.
- **shell/** — Python CLI. Model management, dataset curation, training export.
- **core/** — Shared companion core (context, tools, inference, models, sync, flywheel).
- **workstation.py** — Home GUI (project-aware companion chat, models, sync).
- **sync_client.py** — Laptop client (vault sync + companion chat).
- **sync_server.py** — Home vault sync HTTP server (`:8090`).

## Config

All runtime config lives at `~/.motherbrain/config.json` (created on first run / via `scripts/init_motherbrain.py`):

```json
{
  "inference": {
    "mode": "local",
    "url": "http://127.0.0.1:8081",
    "model": "Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf",
    "ngl": 99,
    "ctx": 8192
  },
  "sync": {
    "server_url": "http://10.0.0.1:8090",
    "token": ""
  },
  "role": "laptop"
}
```

Vault root: `~/.motherbrain/vault/` (projects, chats, models, datasets, exports).

## Hybrid runbook

### Home PC

```bash
python3 sync_server.py          # vault sync on :8090
# llama-server (or kernel) on :8081 as usual
python3 workstation.py
```

Point laptop `sync.server_url` at this machine’s WireGuard (or LAN) IP, e.g. `http://10.0.0.1:8090`.

### Laptop

Download the 32B GGUF once (shell preset):

```bash
python3 shell/main.py model download qwen-32b
# equivalent: model download Qwen/Qwen2.5-Coder-32B-Instruct-GGUF Q4_K_M
```

Run local inference (CUDA Docker one-liner):

```bash
sudo docker run --gpus all -p 8081:8081 \
  -v ~/.motherbrain/vault/shared/base_models:/models \
  ghcr.io/ggml-org/llama.cpp:full-cuda \
  --server -m /models/Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8081 -ngl 99 -c 8192

python3 sync_client.py          # sync vault + chat as companion
```

Set `inference.mode` to `"remote"` and `inference.url` to the home llama URL when you want to fall back over VPN instead of local 32B.

### WireGuard

Use WireGuard (or equivalent) so the laptop can reach home `:8090` (sync) and optionally `:8081` (remote inference). Put the home VPN IP in `sync.server_url` / remote `inference.url`.

## Shell quick reference

```bash
python3 shell/main.py dashboard
python3 shell/main.py model download qwen-32b
python3 shell/main.py mark          # mark last turn good for training
python3 shell/main.py export        # JSONL with project_id metadata
python3 shell/main.py exportpairs
```

## Hardware targets

- Smart glasses (ESP32-based wearable)
- Custom robotics platforms
- Any microcontroller speaking the Motherbrain binary protocol

Firmware / MQTT hooks exist as stubs in `core/devices.py`; no robot firmware in this pass.

## Future packaging

Windows `.exe` (PyInstaller) and an iPhone web UI are planned packaging work — **not implemented in this pass**.
