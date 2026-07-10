# Motherbrain

Local-first AI orchestration platform for custom hardware projects.

## Architecture

- **kernel/** — C++20 daemon. Shared memory IPC, hardware abstraction layer, inference scheduler.
- **shell/** — Python CLI. Model management, dataset curation, terminal dashboard.
- **models/** — Local model storage (gitignored).

## Phase 1

C++ kernel with shared memory message bus. Terminal output only.

## Hardware Targets

- Smart glasses (ESP32-based wearable)
- Custom robotics platforms
- Any microcontroller speaking the Motherbrain binary protocol
