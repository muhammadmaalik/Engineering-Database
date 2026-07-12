"""Tool wrapper around tools/system_agent.py + [TOOL:name|arg...] parse loop."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

# Ensure repo root is importable when core is used as a package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import system_agent  # noqa: E402

TOOL_PATTERN = re.compile(
    r"\[TOOL:([a-zA-Z0-9_]+)(?:\|([^\]]*))?\]",
    re.IGNORECASE,
)

TOOL_HINT = """
To use a tool, respond with exactly this format on a single line:
[TOOL:name|arg1|arg2]

Available tools:
- read_file|path
- write_file|path|content
- list_directory|path
- run_command|command
- search_files|pattern|directory
""".strip()

MAX_TOOL_ROUNDS = 5

CompleteFn = Callable[[str], str]


def parse_tool_calls(text: str) -> list[tuple[str, list[str]]]:
    """Parse all [TOOL:name|arg1|arg2] calls from model output."""
    found: list[tuple[str, list[str]]] = []
    for m in TOOL_PATTERN.finditer(text or ""):
        name = m.group(1).strip()
        raw_args = m.group(2) or ""
        args = [a for a in raw_args.split("|")] if raw_args != "" else []
        # Drop a single empty arg from trailing/empty pipe groups carefully:
        # "[TOOL:list_directory]" -> no args; "[TOOL:list_directory|]" -> [""]
        if args == [""]:
            args = [""]
        found.append((name, args))
    return found


def run_tool(name: str, args: list[str] | None = None) -> str:
    """Dispatch a tool call to system_agent."""
    args = args or []
    fn = system_agent.TOOLS.get(name)
    if not fn:
        return f"Error: Unknown tool '{name}'. Available: {', '.join(system_agent.TOOLS)}"
    try:
        return str(fn(*args))
    except TypeError as e:
        return f"Error: Bad arguments for {name}: {e}"
    except Exception as e:
        return f"Error running {name}: {e}"


def extract_final_text(text: str) -> str:
    """Strip tool-call lines from a final assistant reply when present."""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        if TOOL_PATTERN.search(line) and line.strip().startswith("["):
            continue
        lines.append(line)
    return "\n".join(lines).strip() or text.strip()


def run_with_tools(
    prompt: str,
    complete: CompleteFn,
    *,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> str:
    """Call `complete(prompt)` and resolve tool calls up to max_rounds.

    When the model emits [TOOL:name|arg...], run via system_agent, append
    results to the prompt, and re-query.
    """
    current = prompt
    last_content = ""

    for _ in range(max(1, max_rounds)):
        last_content = (complete(current) or "").strip()
        calls = parse_tool_calls(last_content)
        if not calls:
            return last_content

        results: list[str] = []
        for name, args in calls:
            result = run_tool(name, args)
            results.append(f"[TOOL_RESULT:{name}]\n{result}")

        current = (
            f"{current}{last_content}\n"
            + "\n".join(results)
            + "\nAssistant:"
        )

    return last_content
