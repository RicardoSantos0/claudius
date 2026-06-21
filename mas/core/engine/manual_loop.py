"""
Manual provider-agnostic loop — shared core (M-c / M-d).

Applies an LLM response (from ANY provider) to governed state: parse the wire
block, record the agent's work as an accepted handoff, then apply the next action
(advance phase / delegate / consult / escalate / wait).

This is the single tested code path behind both surfaces:
  - the `mas ingest` CLI command
  - the MCP server's `mas_ingest` tool

so the manual loop behaves identically whether driven from a terminal or an MCP
client (e.g. Claude Code as one surface over the package).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IngestResult:
    """Structured outcome of apply_ingest()."""
    phase_before: str
    phase_after: str
    acting_agent: str
    status: str
    action: str                       # advance_phase | delegate | consult | escalate | wait
    handoff_id: str = ""
    next_agent: str | None = None     # requested next agent (from the response)
    delegated_to: str | None = None   # set when a delegation handoff was created
    delegation_handoff_id: str | None = None
    delegate_error: str | None = None
    closed: bool = False
    decisions: int = 0
    artifacts: int = 0
    parse_errors: list[str] = field(default_factory=list)
    knowledge_request: dict | None = None


def apply_ingest(project_id: str, raw: str, agent_id: str | None = None) -> IngestResult:
    """Parse `raw` and apply it to `project_id`'s governed state. Returns IngestResult.

    Raises only if the primary handoff cannot be recorded (a delegation failure is
    captured non-fatally in IngestResult.delegate_error).
    """
    from core.engine.response_parser import ResponseParser
    from core.engine.shared_state_manager import SharedStateManager
    from core.engine.handoff_engine import HandoffEngine
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig, _next_phase
    from core.engine.agent_ids import normalize_agent_id

    parsed = ResponseParser().parse(raw)

    sm = SharedStateManager(project_id)
    state = sm.load()
    ci = state.get("core_identity", {})
    wf = state.get("workflow", {})
    phase = ci.get("current_phase", "intake")
    mode = wf.get("mode", "standard")
    loop = OrchestrationLoop(LoopConfig(project_id=project_id))
    acting = (normalize_agent_id(agent_id) or agent_id) if agent_id else loop._determine_next_agent(state)

    # Record the agent's work as a governed handoff (master -> acting) and accept it.
    he = HandoffEngine()
    handoff = he.create(
        sm=sm, from_agent="master_orchestrator", to_agent=acting, phase=phase,
        task_description=f"{phase} phase output (manual ingest)",
        payload={
            "summary": parsed.reasoning or f"{acting} completed {phase}",
            "artifacts_produced": parsed.artifacts,
            "decisions_made": parsed.decisions,
            "open_questions": [],
            "constraints_for_next": [],
            "shared_state_fields_modified": [],
        },
    )
    hid = handoff.get("handoff_id", "")
    he.accept(sm=sm, handoff_id=hid)

    result = IngestResult(
        phase_before=phase, phase_after=phase, acting_agent=acting,
        status=parsed.status, action=parsed.next_action, handoff_id=hid,
        next_agent=parsed.next_agent, decisions=len(parsed.decisions),
        artifacts=len(parsed.artifacts), parse_errors=list(parsed.parse_errors),
        knowledge_request=parsed.knowledge_request,
    )

    action = parsed.next_action
    if action == "advance_phase":
        new_phase = _next_phase(phase, mode)
        sm.write("master_orchestrator", "core_identity", "current_phase", new_phase)
        sm.append("master_orchestrator", "workflow", "completed_phases", phase)
        if new_phase == "closed":
            sm.write("master_orchestrator", "core_identity", "status", "closed")
            result.closed = True
        result.phase_after = new_phase
    elif action == "delegate" and parsed.next_agent:
        # master_orchestrator is the delegation authority (a worker cannot delegate itself).
        nxt = normalize_agent_id(parsed.next_agent) or parsed.next_agent
        try:
            dh = he.create(
                sm=sm, from_agent="master_orchestrator", to_agent=nxt, phase=phase,
                task_description=f"Delegated to {nxt} (requested by {acting} during {phase})",
                payload={"summary": parsed.reasoning or "delegation",
                         "artifacts_produced": [], "decisions_made": [],
                         "open_questions": [], "constraints_for_next": [],
                         "shared_state_fields_modified": []},
            )
            result.delegated_to = nxt
            result.delegation_handoff_id = dh.get("handoff_id", "")
        except Exception as exc:
            result.delegate_error = str(exc)
    return result
