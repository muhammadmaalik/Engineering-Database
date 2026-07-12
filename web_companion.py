#!/usr/bin/env python3
"""CLI entry for the hardened Motherbrain web companion (phone over WireGuard)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import paths  # noqa: E402
from core.web_companion import run_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Motherbrain secure web companion (HTTPS + token; VPN bind by default)"
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: config web.host, e.g. 10.0.0.1). Never 0.0.0.0.",
    )
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: web.port / 8443)")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="HTTP only on 127.0.0.1 for local testing (no TLS)",
    )
    args = parser.parse_args()

    paths.ensure_dirs()
    paths.ensure_config()
    try:
        run_server(host=args.host, port=args.port, dev=args.dev)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
