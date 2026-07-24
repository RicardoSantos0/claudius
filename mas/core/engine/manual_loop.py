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

import json
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
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    parse_errors: list[str] = field(default_factory=list)
    knowledge_request: dict | None = None
    dispatch_verification: dict = field(default_factory=dict)


def _event_inputs(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload.get("params", {}).get("inputs", payload)


def verify_manual_dispatch(
    project_id: str,
    agent_id: str,
    phase: str,
    receipt: dict | None,
) -> dict:
    """Compare a manual execution receipt with the latest dispatch selection."""
    from core.db import query_events

    def _query(**kwargs) -> list[dict]:
        try:
            return query_events(**kwargs)
        except Exception:
            # A legacy/new workspace may not have an initialized event table yet.
            # Treat that as untracked, never as proof of a dispatch.
            return []

    selection = None
    for row in _query(
        project_id=project_id,
        agent_id=agent_id,
        action_type="decision_recorded",
        limit=100,
    ):
        inputs = _event_inputs(row)
        candidate = inputs.get("route_selection", {})
        if (
            inputs.get("decision_type") == "execution_route_selection"
            and isinstance(candidate, dict)
            and str(candidate.get("phase") or "") == str(phase or "")
        ):
            selection = candidate
            break

    if not selection:
        return {
            "status": "unmatched" if receipt else "legacy_untracked",
            "accepted": False,
            "required": False,
            "dispatch_id": (receipt or {}).get("dispatch_id"),
            "evidence": "none",
            "reason": "no matching persisted dispatch selection",
        }

    dispatch_id = str(selection.get("dispatch_id") or "")
    required = bool(selection.get("verification_required", False))
    base = {
        "dispatch_id": dispatch_id,
        "required": required,
        "selected_provider": selection.get("provider"),
        "selected_model": selection.get("model"),
    }
    if not receipt:
        return {
            **base,
            "status": "missing" if required else "unverified",
            "accepted": False,
            "evidence": "none",
            "reason": "manual client did not return an execution receipt",
        }

    supplied_id = str(receipt.get("dispatch_id") or "")
    provider = str(
        receipt.get("reported_provider") or receipt.get("provider") or ""
    )
    model = str(receipt.get("reported_model") or receipt.get("model") or "")
    source = str(receipt.get("verification_source") or "client").lower()
    if supplied_id != dispatch_id or not provider or not model:
        return {
            **base,
            "status": "mismatch",
            "accepted": False,
            "evidence": source,
            "reported_provider": provider or None,
            "reported_model": model or None,
            "reason": "receipt is incomplete or dispatch_id does not match",
        }

    for row in _query(
        project_id=project_id,
        action_type="dispatch_receipt",
        limit=100,
    ):
        if str(_event_inputs(row).get("dispatch_id") or "") == dispatch_id:
            return {
                **base,
                "status": "replayed",
                "accepted": False,
                "evidence": source,
                "reported_provider": provider,
                "reported_model": model,
                "reason": "dispatch receipt was already consumed",
            }

    candidates = selection.get("candidates", []) or [
        {
            "provider": selection.get("provider"),
            "model": selection.get("model"),
        }
    ]
    accepted_routes = {
        (
            str(candidate.get("provider") or "").lower(),
            str(candidate.get("model") or "").lower(),
        )
        for candidate in candidates
        if isinstance(candidate, dict)
    }
    if (provider.lower(), model.lower()) not in accepted_routes:
        return {
            **base,
            "status": "mismatch",
            "accepted": False,
            "evidence": source,
            "reported_provider": provider,
            "reported_model": model,
            "reason": "reported provider/model is outside approved candidates",
        }

    status_by_source = {
        "provider": "provider_reported",
        "operator": "operator_attested",
        "client": "client_attested",
    }
    return {
        **base,
        "status": status_by_source.get(source, "client_attested"),
        "accepted": True,
        "evidence": source if source in status_by_source else "client",
        "reported_provider": provider,
        "reported_model": model,
        "reason": "receipt matches an approved dispatch candidate",
    }


def apply_ingest(
    project_id: str,
    raw: str,
    agent_id: str | None = None,
    dispatch_receipt: dict | None = None,
) -> IngestResult:
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
    dispatch_verification = verify_manual_dispatch(
        project_id, acting, phase, dispatch_receipt
    )
    unsafe_receipt = dispatch_verification["status"] in {
        "mismatch", "replayed", "unmatched"
    }
    receipt_required = bool(dispatch_verification.get("required"))
    if parsed.next_action in {"advance_phase", "delegate"} and (
        unsafe_receipt
        or (receipt_required and not dispatch_verification.get("accepted"))
    ):
        raise RuntimeError(
            "manual dispatch receipt gate blocked state-changing action: "
            f"{dispatch_verification['status']} — "
            f"{dispatch_verification['reason']}"
        )
    completion_tokens = 0
    try:
        from core.utils.token_counter import TokenCounter
        completion_tokens = TokenCounter().count(raw)
    except Exception:
        completion_tokens = 0
    if parsed.next_action == "advance_phase":
        from core.engine.lifecycle_guard import (
            check_phase_transition,
            check_standard_intake_advance,
        )

        gate = check_standard_intake_advance(phase, mode, acting)
        if gate.blocked:
            details = "; ".join(v.get("detail", "") for v in gate.violations)
            raise RuntimeError(
                "standard intake phase requires inquirer_agent before advancement"
                + (f": {details}" if details else "")
            )
        target_phase = _next_phase(phase, mode)
        task_board_data = None
        if target_phase == "execution":
            from core.engine.task_board import TaskBoard

            board = TaskBoard(project_id, projects_root=sm.projects_root)
            task_board_data = {"tasks": board.list_tasks()}
        transition_gate = check_phase_transition(
            phase=phase,
            target_phase=target_phase,
            mode=mode,
            project_dir=sm.project_dir,
            task_board_data=task_board_data,
        )
        if transition_gate.blocked:
            details = []
            for violation in transition_gate.violations:
                label = violation.get("invariant", "phase-transition")
                detail = violation.get("detail") or violation.get("missing") or ""
                details.append(f"{label}: {detail}".rstrip(": "))
            raise RuntimeError(
                "phase transition blocked: " + "; ".join(details)
            )

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
            "dispatch_verification": dispatch_verification,
        },
        token_usage={
            "prompt_tokens": 0,
            "completion_tokens": completion_tokens,
            "total_tokens": completion_tokens,
        },
    )
    hid = handoff.get("handoff_id", "")
    he.accept(sm=sm, handoff_id=hid)
    if dispatch_receipt:
        from core.engine.event_recorder import EventRecorder

        EventRecorder().record_simple(
            project_id=project_id,
            actor=acting,
            action_type="dispatch_receipt",
            intent=(
                f"Manual dispatch receipt {dispatch_verification['status']}"
            ),
            phase=phase,
            payload=dispatch_verification,
        )
    try:
        from core.db import record_manual_tokens
        record_manual_tokens(
            project_id,
            acting,
            tokens_prompt=0,
            tokens_completion=completion_tokens,
            note=f"response ingested for {acting} (manual-mode output)",
            measurement_source="heuristic_estimate",
        )
    except Exception:
        pass

    result = IngestResult(
        phase_before=phase, phase_after=phase, acting_agent=acting,
        status=parsed.status, action=parsed.next_action, handoff_id=hid,
        next_agent=parsed.next_agent, decisions=len(parsed.decisions),
        artifacts=len(parsed.artifacts), prompt_tokens=0,
        completion_tokens=completion_tokens, total_tokens=completion_tokens,
        parse_errors=list(parsed.parse_errors),
        knowledge_request=parsed.knowledge_request,
        dispatch_verification=dispatch_verification,
    )

    # Persist wire-protocol decisions and artifact references in the canonical
    # projection as well as the handoff payload.
    for decision in parsed.decisions:
        normalized = dict(decision)
        if "decision_id" not in normalized and normalized.get("id"):
            normalized["decision_id"] = normalized["id"]
        if "value" not in normalized and "v" in normalized:
            normalized["value"] = normalized["v"]
        if "rationale" not in normalized and "rat" in normalized:
            normalized["rationale"] = normalized["rat"]
        sm.append(
            "master_orchestrator", "decisions", "decision_log", normalized
        )
    for artifact in parsed.artifacts:
        sm.append(
            "master_orchestrator", "artifacts", "documents", artifact
        )

    if parsed.skills_used:
        try:
            from core.engine.event_recorder import EventRecorder

            recorder = EventRecorder()
            for skill in parsed.skills_used:
                skill_name = skill.get("name") or skill.get("skill")
                if not skill_name:
                    continue
                recorder.record_simple(
                    project_id=project_id,
                    actor=acting,
                    action_type="skill_completed",
                    intent=f"Manual response reported skill use: {skill_name}",
                    phase=phase,
                    payload={
                        "skill": str(skill_name),
                        "source": "manual_wire",
                    },
                )
        except Exception:
            pass

    action = parsed.next_action
    if action == "advance_phase":
        new_phase = _next_phase(phase, mode)
        snapshot_path = sm.snapshot(phase)
        sm.write("master_orchestrator", "core_identity", "current_phase", new_phase)
        completed = sm.load().get("workflow", {}).get("completed_phases", []) or []
        if phase not in completed:
            sm.append("master_orchestrator", "workflow", "completed_phases", phase)
        try:
            from core.engine.event_recorder import EventRecorder

            EventRecorder().record_simple(
                project_id=project_id,
                actor=acting,
                action_type="phase_transition",
                intent=f"Phase transition: {phase} -> {new_phase}",
                phase=new_phase,
                artifacts=parsed.artifacts,
                payload={
                    "from_phase": phase,
                    "to_phase": new_phase,
                    "snapshot": snapshot_path.name,
                    "source": "manual_ingest",
                },
            )
        except Exception:
            pass
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
