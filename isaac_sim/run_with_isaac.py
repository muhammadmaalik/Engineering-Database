"""Minimal Isaac Sim standalone launcher that starts the Motherbrain bridge.

Run with Isaac's Python (``python.bat`` / ``python.sh``), not system Python::

    # Example (adjust Isaac install path):
    C:\\isaacsim\\python.bat motherbrain\\isaac_sim\\run_with_isaac.py --usd /path/to/scene.usd

If Isaac packages are missing, falls back to the mock TCP bridge only.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow importing sibling bridge_server when executed as a script.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from isaac_sim.bridge_server import BridgeServer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Isaac + Motherbrain bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--usd", default="", help="Optional USD stage to open")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Start SimulationApp headless when Isaac is available",
    )
    args = parser.parse_args()

    simulation_app = None
    try:
        from isaacsim import SimulationApp  # type: ignore

        simulation_app = SimulationApp({"headless": bool(args.headless)})
        if args.usd:
            import omni.usd  # type: ignore

            omni.usd.get_context().open_stage(args.usd)
        print("[motherbrain-isaac] SimulationApp started")
    except Exception as e:
        print(f"[motherbrain-isaac] Isaac not available ({e}); running mock bridge")

    bridge = BridgeServer(host=args.host, port=args.port)
    bridge.serve_forever_background()
    print(f"[motherbrain-isaac] bridge on {args.host}:{args.port}")
    print("Enable in Motherbrain Settings → Isaac Sim (enabled=true).")

    try:
        while True:
            if simulation_app is not None:
                simulation_app.update()
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        if simulation_app is not None:
            simulation_app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
