# Website Build Prompt — Motherbrain

> **Instructions for the AI (Kimi K3):** You are building a complete, modern, production-quality marketing/documentation website for the software project described below. Read the entire document before generating anything. The site must be a single cohesive product website with multiple sections/pages. Use everything in this document as source-of-truth content — do not invent features that are not listed here, but you may rewrite the wording to be punchy and professional.

---

## 1. What Motherbrain Is (Elevator Pitch)

**Motherbrain** is a local-first, privacy-first AI engineering companion platform for people who build real hardware: robots, smart glasses, ESP32-based wearables, and custom microcontroller projects. It runs large language models **entirely on the user's own machines** — no cloud, no subscriptions, no data leaving the network. It pairs a powerful desktop workstation app with a home "vault" server, a laptop client, and a hardened phone web UI, all synced over a private WireGuard VPN.

The core idea: your engineering knowledge — project manifests, chat history, CAD file listings, device configs, datasets, and fine-tuned model adapters — lives in a **vault you own**, indexed and searchable, feeding context into a local AI model that acts as a project-aware engineering companion. Every conversation can be curated into training data, so the companion gets smarter about *your* projects over time — a personal data flywheel.

Tagline candidates (pick or improve):
- "Your AI engineering companion. On your hardware. On your terms."
- "Local-first AI for people who build real things."
- "The engineering brain that lives in your house, not the cloud."

## 2. The Problem It Solves

- Cloud AI assistants leak proprietary hardware designs, firmware, and project data to third parties.
- Generic assistants have zero persistent context about your projects, devices, pinouts, datasets, or design decisions.
- Engineers with capable GPUs at home can run 32B-parameter models locally, but the tooling to make that a daily-driver companion (GUI, sync, phone access, training pipeline) doesn't exist in one place.
- Robotics developers need their AI to talk to simulation (NVIDIA Isaac Sim) — not just chat.

## 3. System Architecture (Explain This Visually on the Site)

Hybrid two-machine topology plus phone:

```
┌────────────────────┐        WireGuard VPN         ┌──────────────────────────┐
│   LAPTOP           │◀────────────────────────────▶│   HOME PC                │
│  Motherbrain.exe   │   vault sync (HTTP :8090)    │  Workstation + vault      │
│  local 32B model   │   remote inference (:8081)   │  sync server + GPU        │
│  (llama.cpp CUDA)  │                              │  llama-server             │
└────────────────────┘                              └───────────┬──────────────┘
                                                                │ HTTPS :8443
                                                    ┌───────────▼──────────────┐
                                                    │  iPHONE / phone browser   │
                                                    │  token-auth web companion │
                                                    └──────────────────────────┘
```

Components:

- **kernel/** — C++20 daemon: shared-memory IPC ring buffer, binary message protocol, hardware abstraction, inference scheduler. (Low-level foundation.)
- **core/** (Python) — shared companion core: config/paths, project context builder, tool execution, llama.cpp inference client, model registry, vault SQLite index with FTS search, two-way sync client, training-data flywheel, Isaac Sim bridge client, PIN auth.
- **workstation.py** — the flagship desktop GUI (Tkinter, dark hacker aesthetic, per-monitor DPI aware). Ships as a single-file Windows exe via PyInstaller.
- **sync_server.py** — stdlib-only HTTP vault sync server on the home PC (port 8090): manifest diffing, batch pull/push, conflict preservation.
- **sync_client.py** — laptop client for vault sync + companion chat.
- **web_companion.py** — hardened HTTPS phone UI: WireGuard-bind only, bearer-token auth, self-signed TLS, rate limiting, CSP headers, filesystem tools disabled by default.
- **shell/** — Python CLI: model downloads (Hugging Face GGUF presets), dataset curation, training export, Unsloth LoRA fine-tuning pipeline.
- **isaac_sim/** — NVIDIA Isaac Sim integration (see section 7).

## 4. The Desktop Workstation (Flagship Feature — Give It a Big Section)

Single window, dark terminal aesthetic (#1a1a1a background, neon green #50fa7b accents, Consolas font). Navigation panels:

1. **AI Chat** — project-aware companion chat against a local 32B model (Qwen2.5-Coder-32B GGUF via llama.cpp CUDA). System prompt auto-injects the active project's manifest: name, status, tags, bound devices, models/adapters, datasets, key CAD/firmware files.
2. **Project Editor** — create/edit project manifests (JSON), bind hardware devices, attach models and datasets, delete projects cleanly (SQLite + files).
3. **Training Console** — Unsloth LoRA fine-tuning launcher: pick base model, JSONL dataset, epochs, output adapter name.
4. **Dashboard** — vault stats: projects indexed, messages logged, curation counts.
5. **Model Manager** — GGUF model registry, active-model selection, size/quantization info.
6. **Vault Explorer** — browse the vault tree.
7. **Photo Analyzer** — attach photos to chats.
8. **Hardware Config** — device/firmware reference (ESP32 templates, WireGuard config help).
9. **Isaac Sim** — bridge config, connection test, Play/Pause/Reset controls of a live simulation.
10. **Dataset Manager** — dataset collections per project.
11. **Vault Sync** — one-click sync server start (home), sync URL/token config, live sync log, Sync Now.
12. **Settings** — inference mode local/remote, model file, GPU layers (ngl), context size, sync settings, Isaac settings.
13. **Integrated WSL terminal** — a real interactive Ubuntu bash session inside the app (with PowerShell fallback).

Top bar: live AI status (Offline/Starting/Ready with model name), sync status, one-click Start AI, one-click Sync Now.

## 5. Security Model (Dedicated Page/Section)

- **PIN lock screen** on app launch: masked input (asterisks), digits only, clipboard paste/copy/cut blocked, context menu blocked, no hints displayed, max 5 attempts then exit. PIN verified against SHA-256 hash with constant-time comparison — plaintext never stored.
- **Vault sync auth**: shared secret token (auto-generated 32-hex), sent as `Authorization: Bearer`; token entry fields in the GUI are masked and paste-blocked.
- **Phone web companion**: binds to WireGuard IP only (refuses 0.0.0.0), HTTPS with self-signed certs, bearer-token or cookie auth on every route, rate limiting, CSP / nosniff / frame-deny headers, filesystem tools off by default.
- **Network posture**: everything rides a private WireGuard VPN; nothing is exposed to the public internet.
- **Local-first**: prompts, chats, and training data never leave user hardware.

## 6. Vault + Sync (How Data Flows)

- Vault root: `~/.motherbrain/vault/` — projects, chats, shared datasets, adapters, exports. Config in `~/.motherbrain/config.json`.
- SQLite index (`vault_index.db`) with FTS full-text search across project manifests; projects/devices/models/datasets tables.
- Sync protocol: SHA-256 + mtime inventory diff → newer file wins → equal-mtime conflicts saved as `.conflict-<timestamp>` copies (nothing silently lost) → batch push/pull as base64 JSON.
- Big GGUF model files intentionally excluded from sync.
- Roles: `home` (runs the server) and `laptop` (points at home's IP).

## 7. Isaac Sim Integration (Robotics Section)

Motherbrain connects to **NVIDIA Isaac Sim** through a lightweight TCP JSON-line bridge (`motherbrain.isaac.v1` protocol, default `127.0.0.1:8765`):

- `isaac_sim/bridge_server.py` runs *inside* Isaac Sim's Python (or standalone in mock mode for development without Isaac installed).
- Methods: `ping`, `get_scene`, `list_prims` (USD stage tree), `set_joint_targets` (articulation control), `play`, `pause`, `reset`.
- The workstation gets a dedicated Isaac Sim panel: enable/host/port config, connection test, timeline controls, live status/scene JSON.
- The AI companion itself gets Isaac tools (`isaac_status`, `isaac_scene`, `isaac_list_prims`, `isaac_play`, `isaac_pause`, `isaac_reset`, `isaac_set_joints`) — the model can inspect and drive the simulation.
- Optional ROS 2 path: pair with Isaac's `isaacsim.ros2.bridge` and a matching `ROS_DOMAIN_ID` for high-rate sensor/actuator topics; the TCP bridge remains the control plane.

## 8. Training Flywheel (Differentiator — Highlight It)

1. Every chat turn is logged to the vault with project metadata.
2. User marks good exchanges (`mark` command / curation UI).
3. Export to JSONL conversation pairs with project context.
4. Fine-tune LoRA adapters with Unsloth on the home GPU (Training Console or `shell/train.py`).
5. Bind the adapter back to the project — the companion now speaks your project's language.

## 9. Tech Stack (For a "Built With" Section)

- **Models**: Qwen2.5-Coder-32B-Instruct GGUF (Q3_K_M/Q4_K_M), llama.cpp llama-server with CUDA, partial GPU offload tuned for 8GB VRAM laptops (ngl 28, ctx 2048, single slot, flash attention).
- **Desktop**: Python 3.12, Tkinter, PyInstaller one-file windowed exe (~27 MB), per-monitor DPI manifest.
- **Kernel**: C++20, shared-memory IPC, CMake.
- **Data**: SQLite + FTS5, JSON manifests, JSONL training exports.
- **Networking**: stdlib HTTP servers, requests, WireGuard, self-signed TLS.
- **Training**: Unsloth, LoRA, Hugging Face Hub downloads.
- **Sim**: NVIDIA Isaac Sim (Omniverse USD, articulations), optional ROS 2.

## 10. Hardware Targets

- Smart glasses (ESP32-based wearable)
- Custom robotics platforms (with Isaac Sim in the loop)
- Any microcontroller speaking the Motherbrain binary protocol
- Typical deployment: gaming laptop (8GB VRAM GPU) + home PC with a bigger GPU

## 11. Website Requirements

Build a **modern, dark-themed, responsive website** with these pages/sections:

1. **Hero** — bold headline, tagline, animated terminal-style typing effect or subtle grid/circuit background. Dark theme matching the app (#1a1a1a base, #50fa7b green accent, #4a9eff blue secondary). Two CTAs: "Get Started" and "View Architecture".
2. **Features grid** — cards for: Local 32B inference, Project-aware chat, Vault + two-way sync, PIN + VPN security, Isaac Sim bridge, Training flywheel, Phone companion, WSL terminal.
3. **Architecture** — render the topology diagram (as a styled HTML/CSS or SVG diagram, not ASCII), with the laptop / home PC / phone nodes and labeled connections.
4. **Security** — dedicated section with the security model bullets, shield/lock iconography.
5. **Isaac Sim / Robotics** — section explaining the bridge with a code snippet of the JSON protocol.
6. **The Flywheel** — 5-step visual loop (chat → mark → export → fine-tune → bind).
7. **Quick Start / Docs** — step-by-step setup for home PC and laptop (use the sync steps from section 6/your knowledge of this doc), with copyable code blocks (`python sync_server.py`, build commands, etc.).
8. **Tech stack** — logo/badge strip or list.
9. **FAQ** — at least 6 questions (Is my data private? What GPU do I need? Does it work offline? Can I use my own model? How does sync handle conflicts? Does it need the cloud?).
10. **Footer** — project name, "local-first, no cloud" statement, GitHub link placeholder.

Style guidance:
- Dark, technical, terminal-inspired; monospace headings or accents (Consolas/JetBrains Mono), clean sans body (Inter).
- Neon green (#50fa7b) primary accent, electric blue (#4a9eff) secondary, warm red (#ff6b6b) sparingly for warnings.
- Subtle animations (fade-in on scroll, hover glows). No stock photos — use CSS/SVG illustrations, terminal mockups, and code blocks.
- Fully responsive, semantic HTML, fast (no heavy frameworks required — vanilla or lightweight is fine).
- Include a fake terminal window component showing a sample companion chat about an ESP32 project.

Deliver: complete HTML/CSS/JS (single page with anchored sections is acceptable, multi-page is also fine).
