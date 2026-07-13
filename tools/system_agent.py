#!/usr/bin/env python3
"""
System Agent - Gives AI models full computer access.
Reads files, writes files, executes shell commands.
Connects to the kernel via message bus.
"""

import subprocess
import os
import json
import sys
from pathlib import Path

# Allowed directories (restrict to prevent catastrophe)
ALLOWED_ROOTS = [
    Path.home(),
    Path("/tmp"),
    Path.home() / ".motherbrain",
    Path.home() / "motherbrain",
]

WORKSPACE = Path.home() / "motherbrain" / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)


def is_allowed(path: Path) -> bool:
    """Check if path is within allowed directories."""
    resolved = path.resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def read_file(filepath: str) -> str:
    """Read contents of a file."""
    path = Path(filepath).expanduser()
    if not is_allowed(path):
        return f"Error: Access denied. {path} is outside allowed directories."
    if not path.exists():
        return f"Error: File not found: {path}"
    try:
        return path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(filepath: str, content: str) -> str:
    """Write content to a file."""
    path = Path(filepath).expanduser()
    if not is_allowed(path):
        return f"Error: Access denied. {path} is outside allowed directories."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Success: Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_directory(dirpath: str) -> str:
    """List contents of a directory."""
    path = Path(dirpath).expanduser()
    if not is_allowed(path):
        return f"Error: Access denied."
    if not path.is_dir():
        return f"Error: Not a directory: {path}"
    try:
        items = []
        for item in sorted(path.iterdir()):
            suffix = "/" if item.is_dir() else ""
            size = item.stat().st_size if item.is_file() else 0
            items.append(f"  {item.name}{suffix} ({size} bytes)")
        return "\n".join(items) if items else "  (empty)"
    except Exception as e:
        return f"Error: {e}"


def run_command(command: str, timeout: int = 30) -> str:
    """Execute a shell command. Returns stdout + stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(WORKSPACE)
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def search_files(pattern: str, directory: str = "~") -> str:
    """Search for files matching a pattern."""
    path = Path(directory).expanduser()
    if not is_allowed(path):
        return "Error: Access denied."
    try:
        matches = list(path.rglob(pattern))[:50]
        if not matches:
            return f"No files matching '{pattern}' found."
        return "\n".join(str(m) for m in matches)
    except Exception as e:
        return f"Error: {e}"


def _isaac():
    from core import isaac_sim

    return isaac_sim


def isaac_status() -> str:
    status = _isaac().ping()
    return json.dumps(status.as_dict(), indent=2)


def isaac_scene() -> str:
    return json.dumps(_isaac().get_scene_summary(), indent=2)


def isaac_list_prims(path: str = "/World") -> str:
    return json.dumps(_isaac().list_prims(path), indent=2)


def isaac_play() -> str:
    return json.dumps(_isaac().play(), indent=2)


def isaac_pause() -> str:
    return json.dumps(_isaac().pause(), indent=2)


def isaac_reset() -> str:
    return json.dumps(_isaac().reset(), indent=2)


def isaac_set_joints(spec: str) -> str:
    """Parse ``joint=1.2,joint2=0.5`` into set_joint_targets."""
    targets: dict[str, float] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, raw = part.split("=", 1)
        targets[name.strip()] = float(raw.strip())
    if not targets:
        return "Error: expected joint=value,joint2=value2"
    return json.dumps(_isaac().set_joint_targets(targets), indent=2)


# Tool registry — what the AI can call
TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "run_command": run_command,
    "search_files": search_files,
    "isaac_status": isaac_status,
    "isaac_scene": isaac_scene,
    "isaac_list_prims": isaac_list_prims,
    "isaac_play": isaac_play,
    "isaac_pause": isaac_pause,
    "isaac_reset": isaac_reset,
    "isaac_set_joints": isaac_set_joints,
}

TOOL_DESCRIPTIONS = """
Available tools:
- read_file(path) — Read a file's contents
- write_file(path, content) — Create or overwrite a file
- list_directory(path) — List files and folders
- run_command(command) — Execute a shell command
- search_files(pattern, directory) — Find files by pattern
- isaac_status() — Isaac Sim bridge ping/status
- isaac_scene() — Scene summary from the bridge
- isaac_list_prims(path) — List USD children under a prim
- isaac_play() / isaac_pause() / isaac_reset() — Timeline control
- isaac_set_joints(joint=value,...) — Set articulation joint targets
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("System Agent for Motherbrain")
        print("Usage: python system_agent.py <command> [args...]")
        print(TOOL_DESCRIPTIONS)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "read" and len(sys.argv) > 2:
        print(read_file(sys.argv[2]))
    elif cmd == "write" and len(sys.argv) > 3:
        content = sys.stdin.read() if len(sys.argv) == 3 else sys.argv[3]
        print(write_file(sys.argv[2], content))
    elif cmd == "list" and len(sys.argv) > 2:
        print(list_directory(sys.argv[2]))
    elif cmd == "run" and len(sys.argv) > 2:
        print(run_command(" ".join(sys.argv[2:])))
    elif cmd == "search" and len(sys.argv) > 2:
        directory = sys.argv[3] if len(sys.argv) > 3 else "~"
        print(search_files(sys.argv[2], directory))
    elif cmd == "tools":
        print(TOOL_DESCRIPTIONS)
    else:
        print(f"Unknown command: {cmd}")
        print(TOOL_DESCRIPTIONS)
