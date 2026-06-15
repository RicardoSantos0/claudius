"""
Orchestration Loop
Autonomous execution engine for the Governed Multi-Agent Delivery System.

Drives a project through its phases by:
  1. Determining which agent runs next (from shared state)
  2. Assembling a context-aware prompt (PromptAssembler)
  3. Calling the agent via AgentRunner
  4. Parsing the wire-protocol response (ResponseParser)
  5. Executing the response: handoffs, phase advances, decisions, artifacts
  6. Handling consultation (ConsultationEngine + per-consultant agent calls)
  7. Handling NotebookLM KNOWLEDGE_REQUESTs (subprocess to ask_question.py)
  8. Pausing at phase boundaries for human confirmation (unless --auto)

Usage:
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    config = LoopConfig(project_id="proj-YYYYMMDD-NNN-...", auto=True)
    result = OrchestrationLoop(config).run()
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import subprocess
import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent.parent  # repo root (claude-config/)

# ---------------------------------------------------------------------------
# Config / result types
# ---------------------------------------------------------------------------

@dataclass
class LoopConfig:
    project_id: str
    max_steps: int = 50
    auto: bool = False                # skip human checkpoints
    target_phase: str | None = None   # stop after this phase completes
    max_agent_retries: int = 2        # per-agent consecutive error limit


class StopReason(str, Enum):
    MAX_STEPS        = "max_steps"
    UNANIMOUS_RISK   = "unanimous_risk"
    CONSULTATION_REQUIRED = "consultation_required"
    HUMAN_ESCALATION = "human_escalation"
    PROJECT_CLOSED   = "project_closed"
    PHASE_CHECKPOINT = "phase_checkpoint"
    TARGET_REACHED   = "target_reached"
    ERROR            = "error"


@dataclass
class LoopResult:
    stopped_at_step: int
    reason: StopReason
    last_agent: str
    last_phase: str
    message: str = ""


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------

class _EscalationRequired(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Phase progression
# ---------------------------------------------------------------------------

from core.engine.shared_state_manager import STANDARD_PHASES  # noqa: E402
from core.engine.agent_ids import normalize_agent_id, is_consultant_panel_alias  # noqa: E402

_LITE_PHASES = ("intake", "execution", "closed")

def _next_phase(current: str, mode: str = "standard") -> str:
    phases = _LITE_PHASES if mode == "lite" else STANDARD_PHASES
    try:
        idx = phases.index(current)
        return phases[idx + 1] if idx + 1 < len(phases) else "closed"
    except ValueError:
        return "closed"


# ---------------------------------------------------------------------------
# OrchestrationLoop
# ---------------------------------------------------------------------------

class OrchestrationLoop:
    """
    Runs the MAS project lifecycle autonomously.

    Instantiate with a LoopConfig, call .run(). The loop reads and writes
    shared state on every iteration — resume-safe by design.
    """

    def __init__(self, config: LoopConfig) -> None:
        self.config = config
        self._pending_consultation_synthesis: Any = None
        self._pending_grounded_context: str = ""
        self._pending_deployment_plan: list[dict] = []   # set when HR returns a deploy plan
        self._agent_error_counts: dict[str, int] = {}
        self._recorded_skill_recommendations: set[tuple[str, str, str]] = set()
        # Lazy-loaded helpers
        self._runner = None
        self._assembler = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> LoopResult:
        """Main loop. Reads state each iteration. Never raises."""
        step = 0
        last_agent = "master_orchestrator"
        last_phase = "intake"

        try:
            while step < self.config.max_steps:
                state = self._load_state()
                last_phase = state.get("core_identity", {}).get("current_phase", last_phase)
                status = state.get("core_identity", {}).get("status", "active")

                if status == "closed":
                    return LoopResult(step, StopReason.PROJECT_CLOSED, last_agent, last_phase,
                                      "Project is already closed.")

                pending_agents = self._determine_pending_agents(state)

                if not pending_agents:
                    # ── Master's turn ──────────────────────────────────────────
                    agent_id = "master_orchestrator"
                    last_agent = agent_id
                    self._print_step(step + 1, agent_id, last_phase)

                    agent_resp = self._dispatch_agent(agent_id, state)
                    parsed = self._parse_response(agent_resp.raw_text)

                    status_tag = f" [{parsed.status}]" if parsed.status else ""
                    action_tag = f" -> {parsed.next_action}" if parsed.next_action not in ("", "wait") else ""
                    agent_tag = (f":{parsed.next_agents_label}" if parsed.parallel_agents
                                 else f":{parsed.next_agent}" if parsed.next_agent else "")
                    print(f"  tokens={agent_resp.tokens_used}{status_tag}{action_tag}{agent_tag}")

                    for err in parsed.parse_errors:
                        print(f"  [parse_warn] {err}")

                    if parsed.knowledge_request:
                        answer = self._handle_knowledge_request(parsed.knowledge_request)
                        self._pending_grounded_context = answer
                        print(f"  [notebooklm] grounded context injected for next step")

                    if parsed.skill_request:
                        self._handle_skill_request(agent_id, parsed.skill_request, last_phase)
                        self._record_skills_used(agent_id, parsed.skills_used, last_phase)
                        step += 1
                        continue

                    self._record_skills_used(agent_id, parsed.skills_used, last_phase)
                    stop = self._execute_master_actions(parsed, state)
                    if stop:
                        return stop

                elif len(pending_agents) == 1:
                    # ── Single sub-agent ───────────────────────────────────────
                    agent_id = pending_agents[0]
                    last_agent = agent_id
                    self._print_step(step + 1, agent_id, last_phase)

                    agent_resp = self._dispatch_agent(agent_id, state)
                    parsed = self._parse_response(agent_resp.raw_text)

                    status_tag = f" [{parsed.status}]" if parsed.status else ""
                    action_tag = f" -> {parsed.next_action}" if parsed.next_action not in ("", "wait") else ""
                    print(f"  tokens={agent_resp.tokens_used}{status_tag}{action_tag}")

                    for err in parsed.parse_errors:
                        print(f"  [parse_warn] {err}")

                    if parsed.knowledge_request:
                        answer = self._handle_knowledge_request(parsed.knowledge_request)
                        self._pending_grounded_context = answer
                        print(f"  [notebooklm] grounded context injected for next step")

                    if parsed.skill_request:
                        self._handle_skill_request(agent_id, parsed.skill_request, last_phase)
                        self._record_skills_used(agent_id, parsed.skills_used, last_phase)
                        step += 1
                        continue

                    if parsed.next_action == "escalate":
                        raise _EscalationRequired(
                            parsed.reasoning or f"Agent '{agent_id}' requested escalation."
                        )

                    self._accept_pending_handoff(state, agent_id, parsed)
                    self._record_subagent_output(agent_id, parsed)

                else:
                    # ── Parallel sub-agents ────────────────────────────────────
                    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    print(f"[step {step + 1:>3}] {ts}  [parallel ×{len(pending_agents)}] "
                          f"{' | '.join(pending_agents)}  phase={last_phase}")

                    parallel_results = self._dispatch_agents_parallel(pending_agents, state)

                    for p_id, p_resp, p_parsed in parallel_results:
                        tok = p_resp.tokens_used or 0
                        print(f"  [{p_id}] status={p_parsed.status} tok={tok}")
                        for err in p_parsed.parse_errors:
                            print(f"  [parse_warn:{p_id}] {err}")
                        if p_parsed.next_action == "escalate":
                            raise _EscalationRequired(
                                p_parsed.reasoning or f"Agent '{p_id}' requested escalation."
                            )
                        if p_parsed.skill_request:
                            self._handle_skill_request(p_id, p_parsed.skill_request, last_phase)
                            self._record_skills_used(p_id, p_parsed.skills_used, last_phase)
                            continue
                        self._accept_pending_handoff(state, p_id, p_parsed)
                        self._record_subagent_output(p_id, p_parsed)

                    if parallel_results:
                        last_agent = parallel_results[-1][0]

                # Phase boundary check (all paths)
                new_state = self._load_state()
                new_phase = new_state.get("core_identity", {}).get("current_phase", last_phase)
                if new_phase != last_phase:
                    print(f"\n  [phase] {last_phase} → {new_phase}")
                    last_phase = new_phase

                    if self.config.target_phase and last_phase == self.config.target_phase:
                        return LoopResult(step + 1, StopReason.TARGET_REACHED,
                                          last_agent, last_phase,
                                          f"Reached target phase '{last_phase}'.")

                    stop = self._human_checkpoint(last_phase, new_state)
                    if stop:
                        return stop

                step += 1

            return LoopResult(step, StopReason.MAX_STEPS, last_agent, last_phase,
                              f"Reached max_steps={self.config.max_steps}")

        except _EscalationRequired as e:
            return LoopResult(step, StopReason.HUMAN_ESCALATION, last_agent, last_phase,
                              e.message)
        except KeyboardInterrupt:
            return LoopResult(step, StopReason.PHASE_CHECKPOINT, last_agent, last_phase,
                              "Interrupted by user.")
        except Exception as e:
            return LoopResult(step, StopReason.ERROR, last_agent, last_phase, str(e))

    # ------------------------------------------------------------------
    # Agent dispatch
    # ------------------------------------------------------------------

    def _dispatch_agent(self, agent_id: str, state: dict) -> "_AgentResponse":
        from core.engine.agent_runner import AgentRunner
        from core.engine.prompt_assembler import PromptAssembler
        from core.utils.config import get_model_for_agent

        canonical_agent_id = normalize_agent_id(agent_id) or agent_id

        if self._assembler is None:
            self._assembler = PromptAssembler(agents_dir=ROOT / "agents")

        phase = state.get("core_identity", {}).get("current_phase", "intake")
        extra_ctx = self._build_extra_context()
        self._record_skill_recommendations(state, phase=phase)

        # Inject pending handoff task description for sub-agents
        if canonical_agent_id != "master_orchestrator":
            task_ctx = self._pending_handoff_context(canonical_agent_id, state)
            if task_ctx:
                if extra_ctx is None:
                    extra_ctx = {}
                extra_ctx["pending_task"] = task_ctx

        prompt = self._assembler.assemble(canonical_agent_id, state,
                                          extra_context=extra_ctx)

        model = get_model_for_agent(canonical_agent_id)
        runner = AgentRunner(model=model)

        from core.utils.config import load_config
        max_tokens = load_config().get("llm", {}).get("max_tokens", 4096)

        result = runner.run(
            agent_id=canonical_agent_id,
            prompt=prompt,
            project_id=self.config.project_id,
            max_tokens=max_tokens,
        )

        text = result.get("text", "")
        tokens = result.get("tokens_used", 0)

        if result.get("error"):
            if not result.get("retryable", True):
                raise Exception(f"Non-retryable error from '{canonical_agent_id}': {result['error']}")
            self._agent_error_counts[canonical_agent_id] = (
                self._agent_error_counts.get(canonical_agent_id, 0) + 1
            )
            if self._agent_error_counts[canonical_agent_id] > self.config.max_agent_retries:
                raise Exception(f"Agent '{canonical_agent_id}' failed {self.config.max_agent_retries+1} "
                                f"times: {result['error']}")
            print(f"  [agent_error] {canonical_agent_id}: {result['error']} "
                  f"(retry {self._agent_error_counts[canonical_agent_id]}/{self.config.max_agent_retries})")
            text = ""
        else:
            self._agent_error_counts.pop(canonical_agent_id, None)

        return _AgentResponse(agent_id=canonical_agent_id, raw_text=text, tokens_used=tokens)

    def _handle_skill_request(
        self,
        agent_id: str,
        skill_request: dict,
        phase: str,
    ) -> None:
        """Authorize and render an agent-requested skill as a controlled MAS step."""
        skill_name = skill_request.get("name") or skill_request.get("skill")
        if not skill_name:
            return
        query = str(skill_request.get("query", ""))
        required = bool(skill_request.get("required", False))
        try:
            from core.engine.skill_bridge import SkillBridge
            from core.engine.event_recorder import EventRecorder
            bridge = SkillBridge()
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor=agent_id,
                action_type="skill_requested",
                intent=f"Skill requested: {skill_name}",
                phase=phase,
                payload={"skill": skill_name, "query": query, "required": required},
            )
            result = bridge.invoke(agent_id, str(skill_name), query,
                                   project_id=self.config.project_id)
            if not result.success:
                print(f"  [skill] {skill_name}: {result.outcome}")
                return
            rendered = bridge.render_skill_prompt(
                agent_id, str(skill_name), query, project_id=self.config.project_id
            )
            self._pending_grounded_context = (
                f"[skill_prompt:{skill_name}]\n{rendered}"
            )
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor=agent_id,
                action_type="skill_completed",
                intent=f"Skill prompt rendered: {skill_name}",
                phase=phase,
                payload={
                    "skill": skill_name,
                    "query": query,
                    "outcome": "rendered_prompt",
                },
            )
            print(f"  [skill] {skill_name} prompt rendered for next step")
        except Exception as exc:
            print(f"  [skill_warn] {skill_name}: {exc}")

    def _record_skills_used(
        self,
        agent_id: str,
        skills_used: list[dict],
        phase: str,
    ) -> None:
        if not skills_used:
            return
        try:
            from core.engine.event_recorder import EventRecorder
            recorder = EventRecorder()
            for item in skills_used:
                name = item.get("name") or item.get("skill")
                if not name:
                    continue
                recorder.record_simple(
                    project_id=self.config.project_id,
                    actor=agent_id,
                    action_type="skill_completed",
                    intent=f"Agent reported skill used: {name}",
                    phase=phase,
                    payload=item,
                )
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

    def _record_skill_recommendations(
        self,
        state: dict,
        *,
        phase: str | None = None,
        event: str | None = None,
        changed_paths: list[str] | None = None,
        status: str | None = None,
    ) -> None:
        """Record required/recommended skill triggers as typed events."""
        try:
            from core.engine.skill_trigger import SkillTriggerPolicy
            from core.engine.event_recorder import EventRecorder
            from core.utils.config import resolve_project_dir
            policy = SkillTriggerPolicy()
            project_dir = resolve_project_dir(self.config.project_id,
                                              projects_root=ROOT / "mas" / "projects")
            recs = policy.recommendations_for(
                state=state,
                project_dir=project_dir,
                event=event,
                phase=phase,
                changed_paths=changed_paths or [],
                status=status,
            )
            recorder = EventRecorder()
            for rec in recs:
                key = (self.config.project_id, rec.rule_id, rec.skill)
                if key in self._recorded_skill_recommendations:
                    continue
                recorder.record_simple(
                    project_id=self.config.project_id,
                    actor="system",
                    action_type="skill_recommended",
                    intent=f"Recommended skill: {rec.skill}",
                    phase=phase,
                    rule_id=rec.rule_id,
                    payload={
                        "skill": rec.skill,
                        "required": rec.required,
                        "reason": rec.reason,
                        "event": event,
                    },
                )
                self._recorded_skill_recommendations.add(key)
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

    def _determine_pending_agents(self, state: dict) -> list[str]:
        """
        Return the list of agents with pending handoffs (in handoff order).
        Empty list means no pending handoffs → master_orchestrator's turn.
        Parallel handoffs issued in the same master step all appear as pending.
        """
        history = state.get("workflow", {}).get("handoff_history", [])
        pending: list[str] = []
        seen: set[str] = set()
        for ho in history:
            try:
                from core.engine.handoff_engine import HandoffEngine
                expanded = HandoffEngine.expand(ho)
            except Exception:
                expanded = ho
            if expanded.get("acceptance", {}).get("status") == "pending":
                raw = expanded.get("to_agent", "")
                agent_id = normalize_agent_id(raw) or raw
                if agent_id and agent_id not in seen:
                    pending.append(agent_id)
                    seen.add(agent_id)
        return pending

    def _determine_next_agent(self, state: dict) -> str:
        """Backwards-compat single-agent variant of _determine_pending_agents."""
        pending = self._determine_pending_agents(state)
        return pending[0] if pending else "master_orchestrator"

    def _dispatch_agents_parallel(
            self, agent_ids: list[str], state: dict,
    ) -> list[tuple[str, "_AgentResponse", "ParsedResponse"]]:
        """
        Dispatch multiple agents concurrently via ThreadPoolExecutor.
        Returns a list of (agent_id, response, parsed) in completion order.
        """
        results: list[tuple[str, "_AgentResponse", "ParsedResponse"]] = []

        def _run(aid: str) -> tuple[str, "_AgentResponse", "ParsedResponse"]:
            resp = self._dispatch_agent(aid, state)
            parsed = self._parse_response(resp.raw_text)
            return aid, resp, parsed

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(agent_ids),
                thread_name_prefix="mas_parallel",
        ) as executor:
            futures = {executor.submit(_run, aid): aid for aid in agent_ids}
            for future in concurrent.futures.as_completed(futures):
                aid = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    print(f"  [parallel_error] {aid}: {exc}")
                    self._agent_error_counts[aid] = (
                        self._agent_error_counts.get(aid, 0) + 1
                    )

        # Preserve original dispatch order for deterministic post-processing
        order = {aid: i for i, aid in enumerate(agent_ids)}
        results.sort(key=lambda t: order.get(t[0], 999))
        return results

    def _pending_handoff_context(self, agent_id: str, state: dict) -> str:
        """Return the task_description from the pending handoff for this agent."""
        target_agent_id = normalize_agent_id(agent_id) or agent_id
        history = state.get("workflow", {}).get("handoff_history", [])
        for ho in reversed(history):
            try:
                from core.engine.handoff_engine import HandoffEngine
                expanded = HandoffEngine.expand(ho)
            except Exception:
                expanded = ho
            to_agent = normalize_agent_id(expanded.get("to_agent")) or expanded.get("to_agent")
            if (to_agent == target_agent_id and
                    expanded.get("acceptance", {}).get("status") == "pending"):
                task = expanded.get("task_description", "")
                payload_summary = expanded.get("payload", {}).get("summary", "")
                parts = [p for p in (task, payload_summary) if p]
                return "\n".join(parts)
        return ""

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str) -> "ParsedResponse":
        from core.engine.response_parser import ResponseParser
        return ResponseParser().parse(raw_text)

    # ------------------------------------------------------------------
    # Master action execution
    # ------------------------------------------------------------------

    def _execute_master_actions(self, parsed: "ParsedResponse",
                                state: dict) -> LoopResult | None:
        """
        Translates master_orchestrator wire response into concrete engine calls.
        Returns a LoopResult to stop the loop, or None to continue.
        """
        from core.engine.shared_state_manager import SharedStateManager
        from core.engine.handoff_engine import HandoffEngine

        sm = SharedStateManager(self.config.project_id)
        he = HandoffEngine()
        phase = state.get("core_identity", {}).get("current_phase", "intake")
        now = datetime.now(timezone.utc).isoformat()
        next_agent_raw = parsed.next_agent
        next_agent_id = normalize_agent_id(next_agent_raw) if next_agent_raw else None

        # 1. Record decisions
        for dec in parsed.decisions:
            if isinstance(dec, dict) and dec.get("id"):
                sm.append("master_orchestrator", "decisions", "decision_log", {
                    "decision_id":             dec.get("id"),
                    "value":                   dec.get("v", ""),
                    "rationale":               dec.get("rat", ""),
                    "alternatives_considered": dec.get("alt", []),
                    "related_to":              dec.get("rel", ""),
                    "recorded_at":             now,
                    "source":                  "orchestration_loop",
                })

        # 2. Record artifacts
        for art in parsed.artifacts:
            sm.append("master_orchestrator", "artifacts", "documents", art)
        self._materialize_artifacts(parsed.artifacts, sm.project_dir, author="master_orchestrator")

        # 3. Consultation trigger
        consultation_trigger = parsed.consultation_trigger
        if parsed.next_action == "consult" and consultation_trigger is None:
            consultation_trigger = self._default_consultation_trigger(parsed, state)

        # Group aliases ("experts", etc.) are treated as panel consultation.
        # Master must specify the consultants in consultation_trigger.consultants —
        # no engine-side defaults are added.
        if parsed.next_action == "delegate" and is_consultant_panel_alias(next_agent_raw):
            consultation_trigger = consultation_trigger or self._default_consultation_trigger(parsed, state)

        if consultation_trigger and not self._pending_consultation_synthesis:
            synthesis = self._run_consultation(consultation_trigger, state)
            if synthesis and getattr(synthesis, "unanimous_high_risk", False):
                return LoopResult(0, StopReason.UNANIMOUS_RISK,
                                  "master_orchestrator", phase,
                                  "Unanimous high risk — human review required.")
            if synthesis and getattr(synthesis, "human_escalation_required", False):
                raise _EscalationRequired("Human escalation required by consultation panel.")
            self._pending_consultation_synthesis = synthesis

        consultation_stop = self._consultation_gate_stop(
            parsed, state, consultation_trigger, phase
        )
        if consultation_stop:
            return consultation_stop

        # 4. Escalation
        if parsed.next_action == "escalate":
            raise _EscalationRequired(parsed.reasoning or "Agent requested escalation.")

        # 5. Phase advance
        if parsed.next_action == "advance_phase":
            new_phase = _next_phase(phase, state.get("workflow", {}).get("mode", "standard"))
            sm.snapshot(phase)                         # checkpoint before leaving phase
            sm.write("master_orchestrator", "core_identity", "current_phase", new_phase)
            sm.system_append("workflow", "completed_phases", phase)
            self._write_phase_document(phase, state, sm.project_dir)
            print(f"  [snapshot] {phase} saved")

            # Trigger EpisodeWriter replay when closing project
            if new_phase == "closed":
                print("  [graph] skipped: graph memory is deprecated; prefer SQL-backed retrieval")

        # 6. Delegate to next agent(s)
        if (parsed.next_action == "delegate"
                and not is_consultant_panel_alias(next_agent_raw)):

            if parsed.parallel_agents:
                # Multi-agent parallel dispatch — create one handoff per agent
                task_base = parsed.reasoning or f"Execute {phase} tasks"
                for pa_raw in parsed.parallel_agents:
                    pa_id = normalize_agent_id(pa_raw) or pa_raw
                    self._check_deployment_plan_deviation(
                        pa_id, parsed.reasoning or "", sm, now)
                    payload = self._build_handoff_payload(parsed, target_agent=pa_raw)
                    he.create(sm,
                              from_agent="master_orchestrator",
                              to_agent=pa_id,
                              phase=phase,
                              task_description=task_base,
                              payload=payload)
                    print(f"  [handoff] master_orchestrator → {pa_id} [parallel]")
                    self._consume_deployment_plan_entry(pa_id)

            elif next_agent_id:
                # Single-agent dispatch
                self._check_deployment_plan_deviation(
                    next_agent_id, parsed.reasoning or "", sm, now)
                payload = self._build_handoff_payload(parsed)
                he.create(sm,
                          from_agent="master_orchestrator",
                          to_agent=next_agent_id,
                          phase=phase,
                          task_description=parsed.reasoning or f"Execute {phase} tasks",
                          payload=payload)
                print(f"  [handoff] master_orchestrator → {next_agent_id}")
                self._consume_deployment_plan_entry(next_agent_id)

        return None

    def _accept_pending_handoff(self, state: dict, agent_id: str,
                                parsed: "ParsedResponse") -> None:
        """Accept the pending handoff the sub-agent was working on."""
        from core.engine.shared_state_manager import SharedStateManager
        from core.engine.handoff_engine import HandoffEngine

        sm = SharedStateManager(self.config.project_id)
        he = HandoffEngine()
        target_agent_id = normalize_agent_id(agent_id) or agent_id
        history = state.get("workflow", {}).get("handoff_history", [])

        for ho in reversed(history):
            try:
                expanded = HandoffEngine.expand(ho)
            except Exception:
                expanded = ho
            to_agent = normalize_agent_id(expanded.get("to_agent")) or expanded.get("to_agent")
            if (to_agent == target_agent_id and
                    expanded.get("acceptance", {}).get("status") == "pending"):
                he.accept(sm, expanded["handoff_id"])
                print(f"  [accept] {expanded['handoff_id']}")
                break

    def _record_subagent_output(self, agent_id: str,
                                parsed: "ParsedResponse") -> None:
        """Write sub-agent dec/art from wire response into shared state."""
        from core.engine.shared_state_manager import SharedStateManager

        sm = SharedStateManager(self.config.project_id)
        now = datetime.now(timezone.utc).isoformat()
        phase = sm.load().get("core_identity", {}).get("current_phase", "")
        self._record_skills_used(agent_id, parsed.skills_used, phase)

        for dec in parsed.decisions:
            if isinstance(dec, dict) and dec.get("id"):
                try:
                    sm.append("scribe_agent", "decisions", "decision_log", {
                        "decision_id":             dec.get("id"),
                        "decided_by":              agent_id,
                        "value":                   dec.get("v", ""),
                        "rationale":               dec.get("rat", ""),
                        "alternatives_considered": dec.get("alt", []),
                        "related_to":              dec.get("rel", ""),
                        "recorded_at":             now,
                        "source":                  "orchestration_loop",
                    })
                except Exception as exc:
                    logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        for art in parsed.artifacts:
            try:
                sm.append("scribe_agent", "artifacts", "change_log", {
                    "change_id": f"chg-{agent_id}-{now[:10]}",
                    "phase": "execution",
                    "description": art,
                    "author": agent_id,
                })
            except Exception as exc:
                logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        self._materialize_artifacts(parsed.artifacts, sm.project_dir, author=agent_id)

        # When HR returns a deployment plan, persist it and queue it for master's next step
        if agent_id == "hr_agent" and parsed.deployment_plan:
            self._pending_deployment_plan = parsed.deployment_plan
            try:
                sm.write("hr_agent", "capability", "deployment_plan",
                         parsed.deployment_plan)
            except Exception:
                # deployment_plan may not yet be a registered field — store in reuse_candidates
                try:
                    sm.write("hr_agent", "capability", "reuse_candidates",
                             parsed.deployment_plan)
                except Exception as exc:
                    logger.debug("orchestration loop step failed (non-blocking): %s", exc)
            ready = [e for e in parsed.deployment_plan if e.get("status") == "ready"]
            gaps  = [e for e in parsed.deployment_plan if e.get("status") == "gap_certified"]
            print(f"  [deploy_plan] {len(ready)} ready, {len(gaps)} gap_certified")

    # ------------------------------------------------------------------
    # Consultation
    # ------------------------------------------------------------------

    def _run_consultation(self, trigger: dict,
                          state: dict) -> Any:
        """Run full consultation round. Returns ConsultationSynthesis or None."""
        from core.engine.consultation_engine import ConsultationEngine
        from core.engine.agent_runner import AgentRunner
        from core.engine.prompt_assembler import PromptAssembler
        from core.engine.shared_state_manager import SharedStateManager
        from core.utils.config import get_model_for_agent

        sm = SharedStateManager(self.config.project_id)
        engine = ConsultationEngine()
        assembler = PromptAssembler(agents_dir=ROOT / "agents")
        raw_domain = (trigger.get("domain")
                      or trigger.get("context", {}).get("domain")
                      or "software_engineering")
        domain = str(raw_domain).strip() or "software_engineering"
        domain_context = engine.load_domain_context(domain)
        consultants = trigger.get("consultants")
        if consultants:
            consultants = [normalize_agent_id(c) or c for c in consultants]

        try:
            request = engine.create_request(
                project_id=self.config.project_id,
                question=trigger.get("question", ""),
                context=trigger.get("context", {}),
                decision_type=trigger.get("decision_type", "governance"),
                consultants=consultants,
                domain_context=domain_context,
            )
        except Exception as e:
            print(f"  [consult_error] create_request failed: {e}")
            return None

        print(f"  [consult] decision_type={trigger.get('decision_type')} "
              f"consultants={request.consultants_selected}")

        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor="master_orchestrator",
                action_type="consultation_requested",
                intent=trigger.get("question", "Consultation requested"),
                payload={
                    "request_id": request.request_id,
                    "decision_type": request.decision_type,
                    "consultants": request.consultants_selected,
                },
                phase=state.get("core_identity", {}).get("current_phase"),
            )
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        for consultant_id in request.consultants_selected:
            extra = {
                "injected_consultation_question": request.question,
                "injected_consultation_context": yaml.dump(
                    trigger.get("context", {}), default_flow_style=False),
            }
            if consultant_id == "domain_expert":
                extra["injected_domain_context"] = request.domain_context
            prompt = assembler.assemble(consultant_id, state, extra_context=extra)
            model = get_model_for_agent(consultant_id)
            runner = AgentRunner(model=model)
            result = runner.run(consultant_id, prompt,
                                project_id=self.config.project_id)

            c_text = result.get("text", "")
            c_parsed = self._parse_consultant_response(c_text)

            # Handle consultant KNOWLEDGE_REQUEST (broker pattern)
            if c_parsed.get("knowledge_request"):
                answer = self._handle_knowledge_request(c_parsed["knowledge_request"])
                c_parsed["key_concerns"] = (
                    c_parsed.get("key_concerns", []) + [f"[grounded] {answer[:200]}"]
                )

            try:
                engine.record_response(
                    request,
                    consultant_id=consultant_id,
                    response_text=c_text,
                    risk_level=c_parsed.get("risk_level", "low"),
                    key_concerns=c_parsed.get("key_concerns", []),
                    recommendation=c_parsed.get("recommendation", "proceed"),
                    reasoning=c_parsed.get("reasoning", ""),
                )
                try:
                    from core.engine.event_recorder import EventRecorder
                    EventRecorder().record_simple(
                        project_id=self.config.project_id,
                        actor=consultant_id,
                        action_type="consultation_response",
                        intent=f"Consultation response for {request.request_id}",
                        payload={
                            "request_id": request.request_id,
                            "risk_level": c_parsed.get("risk_level", "low"),
                            "recommendation": c_parsed.get("recommendation", "proceed"),
                        },
                        phase=state.get("core_identity", {}).get("current_phase"),
                    )
                except Exception as exc:
                    logger.debug("orchestration loop step failed (non-blocking): %s", exc)
            except Exception as e:
                print(f"  [consult_warn] record_response for {consultant_id}: {e}")

        # Check unanimous risk
        try:
            if engine.check_unanimous_risk(request):
                synthesis = engine.synthesize(
                    request, "escalated", "Unanimous high risk from panel", "")
                synthesis.unanimous_high_risk = True
                return synthesis
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        # Synthesize
        try:
            synthesis = engine.synthesize(
                request,
                decision_reached=trigger.get("decision_reached", "proceed with caution"),
                rationale=trigger.get("rationale", "Consultant panel reviewed."),
                risks_addressed="synthesized from panel responses",
            )
        except Exception as e:
            print(f"  [consult_warn] synthesize failed: {e}")
            return None

        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor="master_orchestrator",
                action_type="consultation_synthesis",
                intent=f"Consultation synthesis for {request.request_id}",
                payload={
                    "request_id": request.request_id,
                    "synthesis_id": synthesis.synthesis_id,
                    "decision_reached": synthesis.decision_reached,
                    "human_escalation_required": synthesis.human_escalation_required,
                },
                phase=state.get("core_identity", {}).get("current_phase"),
            )
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        # Save to shared state
        try:
            sm.append("master_orchestrator", "consultation", "synthesis",
                      dataclasses.asdict(synthesis) if dataclasses.is_dataclass(synthesis)
                      else dict(synthesis))
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

        return synthesis

    def _consultation_gate_stop(
        self,
        parsed: "ParsedResponse",
        state: dict,
        consultation_trigger: dict | None,
        phase: str,
    ) -> LoopResult | None:
        """Block gated actions until required consultation is triggered or synthesized."""
        if parsed.next_action not in {"delegate", "advance_phase"}:
            return None
        try:
            from core.engine.consultation_gate import ConsultationGate
            gate = ConsultationGate()
            requirements = gate.required_for(
                state=state,
                parsed=parsed,
                changed_paths=parsed.artifacts,
                status=parsed.status,
            )
        except Exception:
            return None

        for req in requirements:
            valid = gate.has_valid_trigger(req, consultation_trigger, state)
            self._record_consultation_requirement(req, parsed, phase, satisfied=valid)
            if valid:
                continue
            message = (
                f"Consultation required by {req.rule_id}. "
                f"Master must emit consultation_trigger with consultants={req.consultants}."
            )
            self._record_policy_block(req.rule_id, message, parsed, phase)
            return LoopResult(
                0,
                StopReason.CONSULTATION_REQUIRED,
                "master_orchestrator",
                phase,
                message,
            )
        return None

    def _record_consultation_requirement(
        self,
        requirement: Any,
        parsed: "ParsedResponse",
        phase: str,
        *,
        satisfied: bool,
    ) -> None:
        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor="system",
                action_type="consultation_required",
                intent=f"Required consultation: {requirement.rule_id}",
                phase=phase,
                rule_id=requirement.rule_id,
                payload={
                    "decision_type": requirement.decision_type,
                    "consultants": requirement.consultants,
                    "next_action": parsed.next_action,
                    "satisfied": satisfied,
                },
            )
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

    def _record_policy_block(
        self,
        rule_id: str,
        message: str,
        parsed: "ParsedResponse",
        phase: str,
    ) -> None:
        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=self.config.project_id,
                actor="system",
                action_type="policy_block",
                intent=message,
                phase=phase,
                rule_id=rule_id,
                payload={
                    "next_action": parsed.next_action,
                    "status": parsed.status,
                    "artifacts": parsed.artifacts,
                },
            )
        except Exception as exc:
            logger.debug("orchestration loop step failed (non-blocking): %s", exc)

    def _parse_consultant_response(self, text: str) -> dict:
        """
        Light parser for consultant responses.
        Extracts risk_level, key_concerns, recommendation, reasoning.
        """
        result: dict[str, Any] = {
            "risk_level": "low",
            "key_concerns": [],
            "recommendation": "proceed",
            "reasoning": "",
            "knowledge_request": None,
        }
        if not text:
            return result

        # Try wire block first
        from core.engine.response_parser import ResponseParser
        parsed = ResponseParser().parse(text)
        if parsed.raw_wire:
            w = parsed.raw_wire
            result["risk_level"] = w.get("risk_level", w.get("rl", "low"))
            result["key_concerns"] = w.get("key_concerns", w.get("kc", []))
            result["recommendation"] = w.get("recommendation", w.get("rec", "proceed"))
            result["reasoning"] = parsed.reasoning
            result["knowledge_request"] = parsed.knowledge_request
            return result

        # Heuristic: scan for risk keywords
        lower = text.lower()
        if any(w in lower for w in ("critical", "high risk", "severe", "dangerous")):
            result["risk_level"] = "high"
        elif any(w in lower for w in ("medium risk", "moderate", "concern")):
            result["risk_level"] = "medium"

        result["reasoning"] = text[:500]
        result["knowledge_request"] = parsed.knowledge_request
        return result

    # ------------------------------------------------------------------
    # NotebookLM
    # ------------------------------------------------------------------

    def _handle_knowledge_request(self, kr_block: dict) -> str:
        """Call skills/notebooklm/scripts/ask_question.py and return answer."""
        question = kr_block.get("question", "")
        if not question:
            return ""

        script = ROOT / "skills" / "notebooklm" / "scripts" / "ask_question.py"
        if not script.exists():
            return f"[notebooklm_unavailable] script not found at {script}"

        cmd = [sys.executable, str(script), "--question", question]
        notebook_id = kr_block.get("notebook_id", "")
        if notebook_id:
            cmd += ["--notebook-id", notebook_id]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(ROOT / "skills" / "notebooklm"),
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return f"[notebooklm_error] exit={result.returncode}: {result.stderr[:200]}"
        except subprocess.TimeoutExpired:
            return "[notebooklm_timeout] query exceeded 120s"
        except Exception as e:
            return f"[notebooklm_exception] {e}"

    # ------------------------------------------------------------------
    # Human checkpoint
    # ------------------------------------------------------------------

    def _human_checkpoint(self, phase: str, state: dict) -> LoopResult | None:
        """
        Print phase summary and optionally wait for human confirmation.
        Returns a LoopResult to stop, or None to continue.
        """
        ci = state.get("core_identity", {})
        wf = state.get("workflow", {})
        completed = wf.get("completed_phases", [])
        handoffs = wf.get("handoff_history", [])
        pending = [h for h in handoffs
                   if h.get("acceptance", {}).get("status") == "pending" or
                      h.get("status") == "pending"]
        violations = state.get("_meta", {}).get("governance_violations", [])

        print(f"\n{'─'*60}")
        print(f"  PHASE CHECKPOINT: {ci.get('current_phase','?').upper()}")
        print(f"  Completed : {', '.join(completed) or 'none'}")
        print(f"  Handoffs  : {len(handoffs)} total, {len(pending)} pending")
        print(f"  Violations: {len(violations)}")
        if pending:
            for h in pending[-2:]:
                print(f"    ↳ pending: {h.get('handoff_id','?')} "
                      f"({h.get('from_agent','?')} → {h.get('to_agent','?')})")
        print(f"{'─'*60}")

        if self.config.auto:
            print("  [auto] continuing without confirmation")
            return None

        try:
            resp = input("  [enter] continue  [q+enter] quit: ").strip().lower()
            if resp in ("q", "quit", "exit"):
                return LoopResult(0, StopReason.PHASE_CHECKPOINT,
                                  "master_orchestrator", phase,
                                  "Stopped at human checkpoint.")
        except (EOFError, KeyboardInterrupt):
            return LoopResult(0, StopReason.PHASE_CHECKPOINT,
                              "master_orchestrator", phase,
                              "Stopped at human checkpoint.")
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_consultation_trigger(self, parsed: "ParsedResponse", state: dict) -> dict:
        """
        Build a safe fallback consultation trigger when the model asks to consult
        but omits the structured trigger payload.
        """
        phase = state.get("core_identity", {}).get("current_phase", "intake")
        if phase in ("planning", "capability_discovery", "execution", "review"):
            decision_type = "architecture"
        else:
            decision_type = "governance"

        question = parsed.reasoning or f"Consultation requested during phase '{phase}'."
        return {
            "decision_type": decision_type,
            "question": question,
            "context": {
                "phase": phase,
                "status": parsed.status or "",
                "next_action": parsed.next_action or "",
            },
            "decision_reached": "pending_consultation",
            "rationale": "Auto-generated trigger because next_action requested consultation without consultation_trigger payload.",
        }

    def _resolve_artifact_path(self, artifact: str, project_dir: Path) -> Path:
        """Resolve artifact paths relative to the current project when needed."""
        path = Path(artifact)
        if path.is_absolute():
            return path
        artifact_norm = artifact.replace("\\", "/").strip()
        if artifact_norm.startswith("mas/projects/"):
            return ROOT / artifact_norm
        if artifact_norm.startswith("projects/"):
            return ROOT / "mas" / artifact_norm
        return project_dir / artifact_norm

    def _materialize_artifacts(self, artifacts: list[str], project_dir: Path, author: str) -> None:
        """
        Ensure artifact files declared by agents exist on disk.
        Creates missing files with lightweight placeholder content.
        """
        if not artifacts:
            return
        for artifact in artifacts:
            try:
                resolved = self._resolve_artifact_path(str(artifact), project_dir)
                if resolved.exists():
                    continue
                resolved.parent.mkdir(parents=True, exist_ok=True)
                ext = resolved.suffix.lower()
                ts = datetime.now(timezone.utc).isoformat()
                if ext in {".md", ".txt"}:
                    content = (
                        f"# Generated Artifact\n\n"
                        f"- source_agent: {author}\n"
                        f"- project_id: {self.config.project_id}\n"
                        f"- created_at: {ts}\n"
                        f"- note: auto-materialized by orchestration loop from `art` declaration.\n"
                    )
                    resolved.write_text(content, encoding="utf-8")
                elif ext in {".yaml", ".yml"}:
                    payload = {
                        "generated_by": author,
                        "project_id": self.config.project_id,
                        "created_at": ts,
                        "note": "Auto-materialized by orchestration loop from art declaration.",
                    }
                    with resolved.open("w", encoding="utf-8") as fh:
                        yaml.dump(payload, fh, default_flow_style=False, sort_keys=False)
                elif ext == ".json":
                    payload = {
                        "generated_by": author,
                        "project_id": self.config.project_id,
                        "created_at": ts,
                        "note": "Auto-materialized by orchestration loop from art declaration.",
                    }
                    import json as _json
                    resolved.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
                else:
                    resolved.touch()
                rel = resolved
                try:
                    rel = resolved.relative_to(project_dir)
                except Exception as exc:
                    logger.debug("orchestration loop step failed (non-blocking): %s", exc)
                print(f"  [artifact] materialized {rel}")
            except Exception as e:
                print(f"  [artifact_warn] could not materialize '{artifact}': {e}")

    def _write_phase_document(self, phase: str, state: dict,
                              project_dir: Path) -> None:
        """
        Write a minimal phase document to the project directory when leaving
        a phase.  Files are only created if they don't already exist (idempotent).
        Documents are derived from shared state so metrics_engine can score them.
        """
        pd = state.get("project_definition", {})
        ex = state.get("execution", {})

        phase_files: dict[str, tuple[Path, dict]] = {
            "intake": (
                project_dir / "intake" / "clarified_spec.yaml",
                {
                    "phase": "intake",
                    "clarified_specification": pd.get("clarified_specification", ""),
                    "project_goal":            pd.get("project_goal", ""),
                    "problem_statement":       pd.get("problem_statement", ""),
                    "success_criteria":        pd.get("success_criteria", []),
                    "acceptance_criteria":     pd.get("acceptance_criteria", []),
                    "original_brief":          pd.get("original_brief", ""),
                },
            ),
            "planning": (
                project_dir / "planning" / "product_plan.yaml",
                {
                    "phase": "planning",
                    "project_goal":       pd.get("project_goal", ""),
                    "scope":              pd.get("scope", {}),
                    "constraints":        pd.get("constraints", []),
                    "success_criteria":   pd.get("success_criteria", []),
                    "acceptance_criteria":pd.get("acceptance_criteria", []),
                    "expected_outputs":   pd.get("expected_outputs", []),
                },
            ),
            "execution": (
                project_dir / "execution" / "execution_plan.yaml",
                {
                    "phase":      "execution",
                    "milestones": ex.get("milestones", []),
                    "tasks":      ex.get("tasks", []),
                    "plan_path":  ex.get("execution_plan_path", ""),
                },
            ),
        }

        if phase not in phase_files:
            return

        dest_path, content = phase_files[phase]
        if dest_path.exists():
            return  # idempotent — don't overwrite existing docs

        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with dest_path.open("w", encoding="utf-8") as f:
                yaml.dump(content, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            print(f"  [doc] {dest_path.relative_to(project_dir)} written")
        except Exception as e:
            print(f"  [doc_warn] could not write {phase} doc: {e}")

    def _load_state(self) -> dict:
        from core.engine.shared_state_manager import SharedStateManager
        return SharedStateManager(self.config.project_id).load()

    def _build_extra_context(self) -> dict | None:
        ctx: dict[str, str] = {}
        if self._pending_consultation_synthesis:
            try:
                raw = (dataclasses.asdict(self._pending_consultation_synthesis)
                       if dataclasses.is_dataclass(self._pending_consultation_synthesis)
                       else dict(self._pending_consultation_synthesis))
                ctx["injected_consultation_synthesis"] = yaml.dump(raw, default_flow_style=False)
            except Exception as exc:
                logger.debug("orchestration loop step failed (non-blocking): %s", exc)
            self._pending_consultation_synthesis = None
        if self._pending_grounded_context:
            ctx["injected_grounded_context"] = self._pending_grounded_context
            self._pending_grounded_context = ""
        if self._pending_deployment_plan:
            # Surface HR's deployment plan so master reads and routes from it
            ctx["injected_hr_deployment_plan"] = yaml.dump(
                self._pending_deployment_plan, default_flow_style=False
            )
            # Keep the plan in memory until master has processed all entries
        return ctx or None

    def _build_handoff_payload(self, parsed: "ParsedResponse",
                              target_agent: str | None = None) -> dict:
        payload: dict = {
            "_v": "1.0",
            "s": "task:delegated",
            "summary": parsed.reasoning[:300] if parsed.reasoning else "",
            "artifacts_produced": parsed.artifacts,
            "decisions_made": [d.get("v", "") for d in parsed.decisions if isinstance(d, dict)],
            "open_questions": [],
            "constraints_for_next": [],
            "shared_state_fields_modified": [],
        }
        # Enrich payload with HR's suggested parameters when a matching plan entry exists.
        # target_agent overrides parsed.next_agent (used in parallel dispatch).
        agent_lookup = target_agent or parsed.next_agent
        if self._pending_deployment_plan and agent_lookup:
            target = normalize_agent_id(agent_lookup) or agent_lookup
            for entry in self._pending_deployment_plan:
                if (normalize_agent_id(entry.get("agent", "")) == target
                        and entry.get("status") == "ready"):
                    if entry.get("payload"):
                        payload["hr_suggested_params"] = entry["payload"]
                    if entry.get("note"):
                        payload["hr_parameterization_note"] = entry["note"]
                    break
        return payload

    def _check_deployment_plan_deviation(
            self, next_agent_id: str, reasoning: str,
            sm: Any, now: str) -> None:
        """Log a governance override if master delegates to an agent not in the deployment plan."""
        if not self._pending_deployment_plan:
            return
        ready_agents = {
            normalize_agent_id(e.get("agent", "")) or e.get("agent", "")
            for e in self._pending_deployment_plan
            if e.get("status") == "ready"
        }
        if next_agent_id not in ready_agents:
            print(f"  [deploy_override] {next_agent_id} not in HR plan "
                  f"(plan agents: {sorted(ready_agents)})")
            try:
                sm.append("master_orchestrator", "decisions", "decision_log", {
                    "decision_id":             f"override-deploy-{now[:10]}-{next_agent_id}",
                    "decided_by":              "master_orchestrator",
                    "value":                   f"delegate to {next_agent_id}",
                    "override_of":             "hr_deployment_recommendation",
                    "hr_plan_agents":          sorted(ready_agents),
                    "rationale":               reasoning[:300] if reasoning else "no rationale provided",
                    "recorded_at":             now,
                    "source":                  "orchestration_loop_deviation_check",
                })
            except Exception as exc:
                logger.debug("orchestration loop step failed (non-blocking): %s", exc)

    def _consume_deployment_plan_entry(self, agent_id: str) -> None:
        """Remove the consumed entry from the pending deployment plan.
        When all ready entries are consumed, clear the plan."""
        if not self._pending_deployment_plan:
            return
        target = normalize_agent_id(agent_id) or agent_id
        self._pending_deployment_plan = [
            e for e in self._pending_deployment_plan
            if not (
                (normalize_agent_id(e.get("agent", "")) or e.get("agent", "")) == target
                and e.get("status") == "ready"
            )
        ]
        remaining_ready = [e for e in self._pending_deployment_plan if e.get("status") == "ready"]
        if not remaining_ready:
            # All ready entries dispatched; clear the plan
            self._pending_deployment_plan = []

    def _print_step(self, step: int, agent_id: str, phase: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[step {step:>3}] {ts}  {agent_id:<30} phase={phase}")


# ---------------------------------------------------------------------------
# Internal response container
# ---------------------------------------------------------------------------

@dataclass
class _AgentResponse:
    agent_id: str
    raw_text: str
    tokens_used: int


# Re-export ParsedResponse for callers
from core.engine.response_parser import ParsedResponse  # noqa: F401, E402
