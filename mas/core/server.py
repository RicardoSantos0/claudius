"""
MAS MCP server (M-d).

Exposes the MAS engine over the Model Context Protocol so ANY MCP client (Claude
Code, Codex, an IDE, ...) can drive governed project state — the surface that makes
"Claude Code as one surface over the package" (d-009 destination) reachable.

Run:  mas-server         (stdio transport)

Tools are thin wrappers over the same engine the CLI uses (SharedStateManager,
OrchestrationLoop, PromptAssembler, manual_loop.apply_ingest), so the MCP surface
and the CLI share one tested code path. `mcp` is an optional dependency
(`pip install 'claudius[server]'` / the `server` extra).
"""

from __future__ import annotations

import dataclasses

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mas-server", instructions=(
    "MAS (Multi-Agent System) engine over MCP. Inspect and drive governed project "
    "state, assemble the next agent prompt, and apply an LLM response from any "
    "provider (the provider-agnostic manual loop)."
))


def _sm(project_id: str):
    from core.engine.shared_state_manager import SharedStateManager
    return SharedStateManager(project_id)


def _yaml(obj) -> str:
    import yaml
    return yaml.safe_dump(obj, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _is_pending(h: dict) -> bool:
    acc = h.get("acceptance")
    if isinstance(acc, dict):
        return acc.get("status") == "pending"
    return h.get("acc") == "pending"


@mcp.tool()
def mas_list_projects() -> str:
    """List known MAS project IDs."""
    from core.config import get_projects_dir
    from core.utils.config import iter_project_dirs
    ids = sorted(p.name for p in iter_project_dirs(projects_root=get_projects_dir()))
    return _yaml(ids)


@mcp.tool()
def mas_status(project_id: str) -> str:
    """Status summary for a project: phase, status, mode, owner, pending-handoff count."""
    st = _sm(project_id).load()
    ci = st.get("core_identity", {})
    wf = st.get("workflow", {})
    pending = [h for h in wf.get("handoff_history", []) if _is_pending(h)]
    return _yaml({
        "project_id": project_id,
        "phase": ci.get("current_phase"),
        "status": ci.get("status"),
        "mode": wf.get("mode", "standard"),
        "owner": wf.get("current_owner"),
        "completed_phases": wf.get("completed_phases", []),
        "pending_handoffs": len(pending),
    })


@mcp.tool()
def mas_state(project_id: str, path: str = "") -> str:
    """Read shared state. Optional dot-path, e.g. 'core_identity.current_phase'."""
    cur = _sm(project_id).load()
    if path:
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return f"[not found] {path}"
    return _yaml(cur)


@mcp.tool()
def mas_pending(project_id: str) -> str:
    """List pending handoffs for a project."""
    st = _sm(project_id).load()
    pending = [h for h in st.get("workflow", {}).get("handoff_history", []) if _is_pending(h)]
    return _yaml([
        {"handoff_id": h.get("handoff_id"), "from": h.get("from_agent"),
         "to": h.get("to_agent"), "task": h.get("task_description")}
        for h in pending
    ])


@mcp.tool()
def mas_decisions(project_id: str) -> str:
    """Return the decision log for a project."""
    return _yaml(_sm(project_id).load().get("decisions", {}).get("decision_log", []))


@mcp.tool()
def mas_milestones(project_id: str) -> str:
    """Return execution milestones and tasks for a project."""
    ex = _sm(project_id).load().get("execution", {})
    return _yaml({"milestones": ex.get("milestones", []), "tasks": ex.get("tasks", [])})


@mcp.tool()
def mas_roster(filter_status: str = "") -> str:
    """Capability registry summary: agent_id, trust_tier, status."""
    import yaml
    from core.paths import mas_root
    registry_path = mas_root() / "roster" / "registry_index.yaml"
    if not registry_path.exists():
        return "[error] registry_index.yaml not found"
    with open(registry_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("registry", {}).get("agents", [])
    if filter_status:
        agents = [a for a in agents if a.get("status") == filter_status]
    return _yaml([
        {"agent_id": a.get("agent_id"), "trust_tier": a.get("trust_tier"), "status": a.get("status")}
        for a in agents
    ])


@mcp.tool()
def mas_next_agent(project_id: str) -> str:
    """Determine the next agent for the current phase."""
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    state = _sm(project_id).load()
    loop = OrchestrationLoop(LoopConfig(project_id=project_id))
    return loop._determine_next_agent(state)


@mcp.tool()
def mas_prompt(project_id: str, agent_id: str = "") -> str:
    """Assemble the next agent prompt (or a specific agent's) for manual/MCP execution."""
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    from core.engine.prompt_assembler import PromptAssembler
    from core.engine.agent_ids import normalize_agent_id
    from core.paths import mas_root
    state = _sm(project_id).load()
    if agent_id:
        aid = normalize_agent_id(agent_id) or agent_id
    else:
        aid = OrchestrationLoop(LoopConfig(project_id=project_id))._determine_next_agent(state)
    agents_dir = mas_root().parent / "agents"
    return PromptAssembler(agents_dir=agents_dir).assemble(aid, state)


@mcp.tool()
def mas_snapshot(project_id: str, phase: str = "") -> str:
    """Save a timestamped snapshot of shared state. Returns the snapshot path."""
    sm = _sm(project_id)
    ph = phase or sm.load().get("core_identity", {}).get("current_phase", "manual")
    return str(sm.snapshot(ph))


@mcp.tool()
def mas_ingest(project_id: str, response: str, agent_id: str = "") -> str:
    """Apply an LLM response (from ANY provider) to governed state — the manual loop.

    Parses the wire block, records an accepted handoff, then advances/delegates/etc.
    Returns the structured outcome (phase_before/after, action, handoff_id, ...).
    """
    from core.engine.manual_loop import apply_ingest
    res = apply_ingest(project_id, response, agent_id or None)
    return _yaml(dataclasses.asdict(res))


def main() -> None:
    """Console entrypoint for the `mas-server` script (MCP stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
