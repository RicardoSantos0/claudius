"""Provider-neutral dispatch guard (prop-001).

`check_dispatch` decides whether launching a roster agent on some surface carries an
engine dispatch. It knows nothing about any client's tool names: an adapter passes the
agent-type string, the prompt text, the working directory and the environment, and gets
back allow, block or override with a reason and the `mas prompt` command to run.

Block only when all three hold: the type maps to a roster agent, the launch is governed
(an active project declares `project_definition.governed_paths` and the working
directory or a path in the prompt lies under one), and the prompt carries no
`dispatch <uuid>`, `dispatch_id: <uuid>`, `dispatch_id=<uuid>` or `"dispatch_id": "<uuid>"` matching an engine dispatch for that project
and agent. `MAS_BYPASS_DECISION=<id>` naming a `user_authorized_bypass` decision in the
project turns a block into an override. Governed launches append one line to the
project's `logs/dispatch_guard.log`; launches no project governs have no log to join.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.engine.dispatch_lookup import find_dispatch, normalize_agent_name
from core.paths import mas_root, repo_root

BYPASS_ENV = "MAS_BYPASS_DECISION"
BYPASS_SOURCE = "user_authorized_bypass"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# `dispatch <uuid>`, `dispatch_id: <uuid>`, `dispatch_id=<uuid>`, `"dispatch_id": "<uuid>"`, `dispatch (<uuid>)`
_DISPATCH_ID = re.compile(rf"\bdispatch(?:_id)?[\"']?\s*[:=]?\s*[\"'(]?({_UUID})", re.IGNORECASE)


@dataclass(frozen=True)
class GuardDecision:
    verdict: str  # allow | block | override
    reason: str
    project_id: str | None = None
    agent_id: str | None = None
    command: str | None = None


def _norm_path(text: str) -> str:
    """Casefold, forward slashes, and `/c/x` read as `c:/x` so Windows spellings agree."""
    text = text.replace("\\", "/").casefold()
    return re.sub(r"(?<![\w:])/([a-z])/", r"\1:/", text)


def _under(governed: str, text: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(governed)}(?![\w.-])", text) is not None


def roster_agent(agent_type: str, agents_dir: Path) -> str | None:
    """Map a surface's agent-type string to a roster agent name, or None."""
    wanted = normalize_agent_name(str(agent_type or "").rsplit(":", 1)[-1])
    if not wanted or not agents_dir.is_dir():
        return None
    for path in agents_dir.glob("*.md"):
        if not path.stem.startswith("_") and normalize_agent_name(path.stem) == wanted:
            return path.stem
    return None


def _projects(projects_root: Path):
    """Yield (id, dir, state) for active projects that declare governed_paths."""
    from core.utils.config import iter_project_dirs

    for project_dir in iter_project_dirs(projects_root):
        state_file = project_dir / "shared_state.yaml"
        try:
            text = state_file.read_text(encoding="utf-8")
            if "governed_paths" not in text:  # cheap filter before parsing large states
                continue
            state = yaml.safe_load(text) or {}
        except (OSError, yaml.YAMLError):
            continue
        identity = state.get("core_identity") or {}
        paths = (state.get("project_definition") or {}).get("governed_paths") or []
        if identity.get("status") == "active" and paths:
            yield identity.get("project_id") or project_dir.name, project_dir, state


def _bypass_exists(project_dir: Path, state: dict, decision_id: str) -> bool:
    entries = list((state.get("decisions") or {}).get("decision_log") or [])
    log_file = project_dir / "decisions" / "decision_log.yaml"
    try:
        entries += yaml.safe_load(log_file.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError, TypeError):
        pass
    return any(
        isinstance(e, dict) and e.get("decision_id") == decision_id and e.get("source") == BYPASS_SOURCE
        for e in entries
    )


def _log(project_dir: Path, verdict: str, agent: str, surface: str, detail: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {verdict} agent={agent} surface={surface} {detail}\n"
    try:
        (project_dir / "logs").mkdir(exist_ok=True)
        with (project_dir / "logs" / "dispatch_guard.log").open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass  # a log failure must never change the verdict


def check_dispatch(
    agent_type: str,
    prompt: str,
    cwd: str,
    env: Mapping[str, str],
    surface: str = "claude",
    *,
    projects_root: Path | None = None,
    agents_dir: Path | None = None,
    lookup: Callable[[str, str, str], dict | None] = find_dispatch,
) -> GuardDecision:
    agent = roster_agent(agent_type, agents_dir or repo_root() / "agents")
    if agent is None:
        return GuardDecision("allow", "not a roster agent")

    haystack = _norm_path(f"{cwd}\n{prompt}")
    governing = [
        (pid, pdir, state)
        for pid, pdir, state in _projects(projects_root or mas_root() / "projects")
        if any(_under(_norm_path(str(g)).rstrip("/"), haystack) for g in state["project_definition"]["governed_paths"])
    ]
    if not governing:
        return GuardDecision("allow", "launch is not under any active project's governed_paths", agent_id=agent)

    claimed = {m.lower() for m in _DISPATCH_ID.findall(prompt or "")}
    for pid, pdir, _state in governing:
        for dispatch_id in sorted(claimed):
            if lookup(pid, dispatch_id, agent):
                _log(pdir, "allow", agent, surface, f"project={pid} dispatch={dispatch_id}")
                return GuardDecision("allow", f"engine dispatch {dispatch_id} found", pid, agent)

    pid, pdir, state = governing[0]
    command = f"uv run mas prompt {pid} {agent} --surface {surface} --json"
    bypass = (env.get(BYPASS_ENV) or "").strip()
    if bypass and _bypass_exists(pdir, state, bypass):
        _log(pdir, "override", agent, surface, f"project={pid} decision={bypass}")
        return GuardDecision("override", f"{BYPASS_ENV}={bypass} names a {BYPASS_SOURCE} decision", pid, agent)

    why = "no `dispatch <uuid>` in the prompt matches an engine dispatch for this agent"
    if claimed:
        why = f"dispatch id(s) {', '.join(sorted(claimed))} not found for {agent} in {pid}"
    if bypass:
        why += f"; {BYPASS_ENV}={bypass} is not a {BYPASS_SOURCE} decision in {pid}"
    _log(pdir, "block", agent, surface, f"project={pid} reason={why}")
    return GuardDecision(
        "block",
        f"Roster agent {agent} launched inside governed project {pid}: {why}. "
        f"Run `{command}`, send its prompt, and put the returned dispatch_id in the launch prompt.",
        pid,
        agent,
        command,
    )
