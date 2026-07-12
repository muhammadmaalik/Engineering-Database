#!/usr/bin/env python3
"""First-run helper: create ~/.motherbrain/config.json and vault dirs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.paths import CONFIG_PATH, ensure_config, ensure_dirs  # noqa: E402
from core.vault_index import ensure_tables, index_all_projects  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Motherbrain config + vault")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing config.json with defaults",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Also ensure SQLite tables and index project manifests",
    )
    args = parser.parse_args()

    ensure_dirs()
    cfg = ensure_config(overwrite=args.overwrite)
    print(f"Config: {CONFIG_PATH}")
    print(json.dumps(cfg, indent=2))

    if args.index:
        ensure_tables()
        indexed = index_all_projects()
        print(f"Indexed {len(indexed)} project(s): {', '.join(indexed) or '(none)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
