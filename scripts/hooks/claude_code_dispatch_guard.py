"""Claude Code PreToolUse adapter for the MAS dispatch guard (prop-001).

Translates hook input into `core.engine.dispatch_guard.check_dispatch` and the verdict
into the hook contract. It holds no decision logic. Contract used: the hook receives a
JSON object on stdin (`tool_name`, `tool_input`, `cwd`); exit 0 lets the launch proceed,
exit 2 blocks it and feeds stderr to Claude. An internal error fails open (exit 0 with
a stderr note) so a guard fault never stops unrelated work.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LAUNCH_TOOLS = {"Agent", "Task"}  # the sub-agent tool is "Task" in older Claude Code versions


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mas"))
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") not in LAUNCH_TOOLS:
            return 0
        tool_input = event.get("tool_input") or {}
        from core.engine.dispatch_guard import check_dispatch

        decision = check_dispatch(
            tool_input.get("subagent_type") or "",
            tool_input.get("prompt") or "",
            event.get("cwd") or os.getcwd(),
            os.environ,
            surface="claude",
        )
    except Exception as exc:  # fail open
        print(f"dispatch guard skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    if decision.verdict == "block":
        print(decision.reason, file=sys.stderr)
        return 2
    if decision.verdict == "override":
        print(f"dispatch guard override: {decision.reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
