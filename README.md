# Occhialini

For Developers, By Developers. A local-first AI companion for hardware, robotics, and simulation. Model installation is optional and explicit; vault syncs over LAN or WireGuard using per-device keys.

## Architecture

- **kernel/** — C++20 daemon. Shared memory IPC, hardware abstraction, inference scheduler.
- **shell/** — Python CLI. Model management, dataset curation, training export.
- **core/** — Shared companion core (catalog/downloads, inference, Ed25519 peer sync, context, tools, flywheel, Isaac Sim).
- **isaac_sim/** — TCP bridge to run inside NVIDIA Isaac Sim.
- **workstation.py** — Classic Tk workstation, packaged as `Motherbrain.exe`.
- **modern_app.py**, **modern_desktop/** — Separate PySide6 application, packaged as `Occhialini.exe`.
- **sync_client.py** — Laptop client (vault sync + companion chat).
- **sync_server.py** — Home vault sync HTTP server (`:8090`).
- **web_companion.py** — Hardened HTTPS phone UI (WireGuard bind + token auth).

## Config

All runtime config lives at `~/.motherbrain/config.json` (created on first run / via `scripts/init_motherbrain.py`):

```json
{
  "inference": {
    "mode": "local",
    "url": "http://127.0.0.1:8081",
    "model": "",
    "ngl": 28,
    "ctx": 2048
  },
  "sync": {
    "server_url": "http://10.0.0.1:8090",
    "protocol": "v2",
    "allow_legacy_token": false
  },
  "web": {
    "host": "10.0.0.1",
    "port": 8443,
    "token": "",
    "tls_cert": "",
    "tls_key": "",
    "allow_tools": false
  },
  "role": "laptop"
}
```

Vault root: `~/.motherbrain/vault/` (projects, chats, models, datasets, exports).  
TLS certs for the web companion: `~/.motherbrain/certs/`.  
`web.token` is generated automatically on first web-companion start if empty.

## Hybrid runbook

### Home PC

```bash
python3 sync_server.py          # vault sync on :8090
# llama-server (or kernel) on :8081 as usual
python3 workstation.py
```

Point laptop `sync.server_url` at this machine’s WireGuard (or LAN) IP, e.g. `http://10.0.0.1:8090`.

### Laptop

Choose an exact GGUF in either desktop app, import a local GGUF, or use the shell preset:

```bash
python3 shell/main.py model download qwen-32b
# community repositories require explicit exact-file selection
```

Run local inference (CUDA Docker one-liner):

```bash
sudo docker run --gpus all -p 8081:8081 \
  -v ~/.motherbrain/vault/shared/base_models:/models \
  ghcr.io/ggml-org/llama.cpp:full-cuda \
  --server -m /models/your-selected-model.gguf \
  --host 127.0.0.1 --port 8081 -ngl 28 -c 2048

python3 sync_client.py          # sync vault + chat as companion
```

Set `inference.mode` to `"remote"` and `inference.url` to the home llama URL when you want to fall back over VPN instead of local 32B.

### WireGuard

Use WireGuard (or equivalent) so the laptop can reach home `:8090` and optionally `:8081`. Pair with a two-minute connection key, compare the same 8-digit code, and confirm on both machines. Ed25519 signatures authenticate peers; WireGuard encrypts file contents.

## Windows .exe

Build a double-clickable GUI (no terminal) from the repo root on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
# optional laptop client too:
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -IncludeSyncClient
```

Outputs:

- `dist/Motherbrain.exe` — classic workstation.
- `dist/Occhialini.exe` — modern PySide6 desktop.
- `dist/SHA256SUMS.txt` — release verification hashes.

Runtime data still uses `%USERPROFILE%\.motherbrain` when frozen — never the PyInstaller `_MEIPASS` unpack dir. Do not commit `dist/` or `build/`.

The build script installs pinned dependencies from `requirements-desktop.txt`, runs tests, builds both apps, and performs frozen startup smoke tests.

## Model safety and activation

- Two Occhialini custom models appear as disabled **Coming Soon** entries.
- Reviewed presets disclose publisher, license, exact file, revision, quantization, and size.
- Community Hugging Face search is opt-in and never executes repository code.
- Downloads resume from `.part`, check disk space, verify size/hash when available, then atomically install and register.
- Changing the active model explicitly restarts the managed llama-server; failed downloads are never activated.

## Device-key sync

1. On the host, start the sync server and open a two-minute pairing window.
2. On the other device, enter/scan the connection key.
3. Confirm the identical 8-digit code on both devices.
4. Sync over a private LAN or WireGuard address.

Private identities and trusted peers stay outside the synchronized vault. Signed `/v2` requests include timestamp, nonce, and body hash; replayed, revoked, over-limit, or out-of-skew requests are rejected. Legacy bearer endpoints are disabled unless `sync.allow_legacy_token` is explicitly enabled.

## iPhone web companion

Run on the home PC (reachable only on your WireGuard address by default):

```bash
python3 web_companion.py
# local HTTP smoke-test only:
python3 web_companion.py --dev --port 8443
```

Open `https://10.0.0.1:8443` on the phone (over WireGuard). Trust the self-signed cert once. Log in with `web.token` from `~/.motherbrain/config.json`.

### Security model

- **VPN-only bind** — default `web.host` is `10.0.0.1` (not `0.0.0.0`). Public all-interfaces bind is refused.
- **Token auth** — every page/API needs `Authorization: Bearer …` or the `mb_token` cookie (set via `/login`).
- **HTTPS** — self-signed cert/key under `~/.motherbrain/certs/`; plain HTTP only with `--dev` on `127.0.0.1`.
- **No filesystem tools by default** — `web.allow_tools: false` so the phone UI cannot trigger read/write/shell tools unless you opt in.
- Rate limiting + CSP / nosniff / frame-deny headers.

## Shell quick reference

```bash
python3 shell/main.py dashboard
python3 shell/main.py model download qwen-32b
python3 shell/main.py mark          # mark last turn good for training
python3 shell/main.py export        # JSONL with project_id metadata
python3 shell/main.py exportpairs
```

## Isaac Sim

Motherbrain does **not** embed Isaac Sim. It connects over a small TCP JSON-line bridge so the workstation (and companion tools) can control a running Sim.

```
Motherbrain.exe  ──TCP :8765──▶  isaac_sim/bridge_server.py  ──▶  Isaac scene / articulations
                                      (runs in Isaac Python)
```

### 1. Start the bridge inside Isaac

With Isaac’s Python (or Script Editor after the stage is loaded):

```bash
# Mock / protocol smoke-test (system Python):
python isaac_sim/bridge_server.py --host 127.0.0.1 --port 8765

# With Isaac installed (use Isaac's python.bat / python.sh):
C:\isaacsim\python.bat isaac_sim\run_with_isaac.py --usd C:\path\to\scene.usd
```

### 2. Enable in Motherbrain

In the GUI: **Isaac Sim** nav panel (or Settings) → set `enabled=true`, host/port, save, **Test Connection**.

Or edit `~/.motherbrain/config.json`:

```json
"isaac_sim": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8765,
  "timeout": 3.0,
  "transport": "tcp",
  "ros_domain_id": 0,
  "default_robot_prim": "/World/Robot"
}
```

### Protocol methods

`ping`, `get_scene`, `list_prims`, `set_joint_targets`, `play`, `pause`, `reset`.

Companion tools (when tools are enabled): `isaac_status`, `isaac_scene`, `isaac_list_prims`, `isaac_play`, `isaac_pause`, `isaac_reset`, `isaac_set_joints`.

### Optional ROS 2

For high-rate sensors/actuators, enable Isaac’s `isaacsim.ros2.bridge` and match `ROS_DOMAIN_ID` with `isaac_sim.ros_domain_id`. Keep the TCP bridge for Motherbrain status/commands.

## Hardware targets

- Smart glasses (ESP32-based wearable)
- Custom robotics platforms
- NVIDIA Isaac Sim (TCP bridge above)
- Any microcontroller speaking the Motherbrain binary protocol

Firmware / MQTT hooks exist as stubs in `core/devices.py`; no robot firmware in this pass.
