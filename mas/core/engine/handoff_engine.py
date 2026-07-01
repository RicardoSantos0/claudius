"""
Handoff Engine
Creates, validates, accepts, and rejects formal agent-to-agent handoffs.
Every handoff is recorded in shared_state.workflow.handoff_history.

Usage as library:
    from core.engine.handoff_engine import HandoffEngine
    from core.engine.shared_state_manager import SharedStateManager
    engine = HandoffEngine()
    sm = SharedStateManager("proj-YYYYMMDD-NNN-session-scheduler")
    handoff = engine.create(sm, from_agent="master_orchestrator",
                            to_agent="scribe_agent", ...)

Usage as CLI:
    uv run python mas/core/engine/handoff_engine.py create --project-id proj-YYYYMMDD-NNN-session-scheduler --from master_orchestrator --to scribe_agent --phase intake --task "Initialize project folder" --summary "Starting project"
    uv run python core/handoff_engine.py accept --handoff-id ho-proj-YYYYMMDD-NNN-session-scheduler-001 --project-id proj-YYYYMMDD-NNN-session-scheduler
    uv run python core/handoff_engine.py reject --handoff-id ho-proj-YYYYMMDD-NNN-session-scheduler-001 --project-id proj-YYYYMMDD-NNN-session-scheduler --reason "Missing required fields"
    uv run python core/handoff_engine.py pending --project-id proj-YYYYMMDD-NNN-session-scheduler
    uv run python core/handoff_engine.py show --handoff-id ho-proj-YYYYMMDD-NNN-session-scheduler-001 --project-id proj-YYYYMMDD-NNN-session-scheduler
"""

import sys
import json
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from core.paths import mas_root
ROOT = mas_root()

from .shared_state_manager import SharedStateManager
from core.engine.audit_logger import get_logger
from core.engine.checkpoint_writer import CheckpointWriter
from core.utils.wire_protocol import WireValidator as _WireValidator

logger = logging.getLogger(__name__)

_wire_validator = _WireValidator()


REQUIRED_PAYLOAD_KEYS = [
    "summary",
    "artifacts_produced",
    "decisions_made",
    "open_questions",
    "constraints_for_next",
    "shared_state_fields_modified",
]

_DELIVERY_AGENTS = {
    "canonical_engineer", "integration_engineer",
    "reliability_engineer", "analysis_engineer",
}

# TP-milestone-c-001 — consecutive-return-handoff gate
_CONSECUTIVE_RETURN_GATE_FLAG = "consecutive_return_handoff_gate"


def _warn_missing_tasks_completed(agent_id: str, payload: dict) -> None:
    """Emit a warning if a delivery agent handoff is missing tasks_completed."""
    if agent_id in _DELIVERY_AGENTS:
        if not payload.get("tasks_completed"):
            import warnings
            warnings.warn(
                f"[handoff] {agent_id} handoff missing 'tasks_completed' field. "
                "Update task status before returning.",
                stacklevel=3,
            )


def _consecutive_return_gate_enabled(state: dict) -> bool:
    """Return True iff the project has opted into the consecutive-return gate.

    Backward compatibility: default OFF. Existing projects with established
    handoff chains are unaffected unless they explicitly enable the gate by
    appending a policy_flag of type ``consecutive_return_handoff_gate`` with
    ``status: enabled`` to ``decisions.policy_flags``.
    """
    flags = state.get("decisions", {}).get("policy_flags", []) or []
    for f in flags:
        if not isinstance(f, dict):
            continue
        if f.get("type") == _CONSECUTIVE_RETURN_GATE_FLAG:
            return f.get("status") == "enabled"
    return False


def _detect_consecutive_return(
    history: list, from_agent: str, to_agent: str, task_description: str
) -> dict | None:
    """Detect an A->B->A->B ping-pong on the same task in recent history.

    A new handoff (from=A, to=B, task=T) is a "consecutive return" if the two
    most recent handoffs in history were (from=B, to=A, task=T) and
    (from=A, to=B, task=T) respectively. The pattern indicates the same
    forward request is being re-issued after a single return without
    intervening progress — a smell for unresolved ambiguity.

    Returns a dict describing the pattern when detected, or None.
    Pure function — no state mutation.
    """
    if len(history) < 2:
        return None

    def _h(h, key, alt):
        return h.get(key) or h.get(alt)

    h_recent = history[-1]
    h_prior = history[-2]

    recent_from = _h(h_recent, "from_agent", "from")
    recent_to = _h(h_recent, "to_agent", "to")
    recent_task = _h(h_recent, "task_description", "task") or ""

    prior_from = _h(h_prior, "from_agent", "from")
    prior_to = _h(h_prior, "to_agent", "to")
    prior_task = _h(h_prior, "task_description", "task") or ""

    same_task = (
        recent_task == task_description
        and prior_task == task_description
        and bool(task_description)
    )
    is_return_then_reissue = (
        prior_from == from_agent and prior_to == to_agent
        and recent_from == to_agent and recent_to == from_agent
    )
    if same_task and is_return_then_reissue:
        return {
            "type": "consecutive_return_handoff",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "task_description": task_description,
            "prior_handoff_id": _h(h_prior, "handoff_id", "id"),
            "return_handoff_id": _h(h_recent, "handoff_id", "id"),
        }
    return None


class HandoffEngine:
    """
    Manages formal agent-to-agent handoffs.
    All handoffs are logged in shared_state.workflow.handoff_history.
    """

    def __init__(self, audit_logger=None):
        self.logger = audit_logger or get_logger()
        self._audit_warnings: list[dict] = []

    def _log_audit_warning(self, handoff_id: str, msg: str) -> None:
        """Record a warning in the audit trail without raising an exception."""
        entry = {
            "handoff_id": handoff_id,
            "warning": msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_warnings.append(entry)
        self.logger.log("handoff_warning", handoff_id=handoff_id, warning=msg)

    # --- SEQUENCE COUNTER ---

    def _next_sequence(self, state: dict, project_id: str) -> int:
        history = state.get("workflow", {}).get("handoff_history", [])
        return len(history) + 1

    def _make_id(self, project_id: str, sequence: int) -> str:
        return f"ho-{project_id}-{sequence:03d}"

    # --- CREATE ---

    def create(
        self,
        sm: SharedStateManager,
        from_agent: str,
        to_agent: str,
        phase: str,
        task_description: str,
        payload: dict,
        authorized_by: str = "master_orchestrator",
        token_usage: Optional[dict] = None,
    ) -> dict:
        """
        Create a new handoff record and write it to shared state.
        Returns the handoff dict.
        """
        state = sm.load()
        seq = self._next_sequence(state, sm.project_id)
        handoff_id = self._make_id(sm.project_id, seq)
        now = datetime.now(timezone.utc).isoformat()

        handoff = {
            "handoff_id": handoff_id,
            "project_id": sm.project_id,
            "timestamp": now,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "authorized_by": authorized_by,
            "phase": phase,
            "task_description": task_description,
            "payload": {
                **{k: v for k, v in payload.items()
                   if k not in {"summary", "artifacts_produced", "decisions_made",
                                "open_questions", "constraints_for_next",
                                "shared_state_fields_modified"}},
                "summary": payload.get("summary", ""),
                "artifacts_produced": payload.get("artifacts_produced", []),
                "decisions_made": payload.get("decisions_made", []),
                "open_questions": payload.get("open_questions", []),
                "constraints_for_next": payload.get("constraints_for_next", []),
                "shared_state_fields_modified": payload.get("shared_state_fields_modified", []),
            },
            "token_usage": token_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "acceptance": {
                "status": "pending",
                "rejection_reason": None,
                "follow_up_questions": None,
                "accepted_at": None,
            },
        }

        _warn_missing_tasks_completed(from_agent, payload)

        # TP-milestone-c-001 — consecutive-return-handoff gate.
        # Backward compat: OFF by default; opt-in via decisions.policy_flags.
        prior_history = state.get("workflow", {}).get("handoff_history", []) or []
        pattern = _detect_consecutive_return(
            prior_history, from_agent, to_agent, task_description
        )
        if pattern is not None:
            if _consecutive_return_gate_enabled(state):
                # Record a policy_flag violation. Non-blocking (governance signal)
                # to avoid breaking in-flight projects mid-handoff; the flag is
                # surfaced in evaluation and can drive an improvement proposal.
                flag_entry = {
                    **pattern,
                    "handoff_id": handoff_id,
                    "timestamp": now,
                    "severity": "warn",
                    "source": "handoff_engine.consecutive_return_gate",
                }
                try:
                    sm.system_append("decisions", "policy_flags", flag_entry)
                except Exception as exc:
                    logger.debug("policy_flag persistence failed (non-blocking): %s", exc)
                self._log_audit_warning(
                    handoff_id,
                    f"[gate] consecutive_return_handoff: {from_agent}->{to_agent} "
                    f"re-issued same task without forward progress "
                    f"(prior={pattern['prior_handoff_id']}, "
                    f"return={pattern['return_handoff_id']})",
                )
            else:
                # Gate disabled — emit a non-fatal audit warning only (no flag).
                self._log_audit_warning(
                    handoff_id,
                    f"[hint] consecutive_return_handoff pattern detected "
                    f"({from_agent}->{to_agent}, task='{task_description[:40]}') "
                    f"— enable consecutive_return_handoff_gate to surface as policy_flag",
                )

        # Log audit warning for delivery agents missing tasks_completed
        if from_agent in _DELIVERY_AGENTS and not payload.get("tasks_completed"):
            warning_msg = (
                f"[WARN] Delivery handoff from {from_agent} "
                f"accepted without 'tasks_completed' field. "
                f"Task board may be out of sync."
            )
            self._log_audit_warning(handoff_id, warning_msg)

        result = sm.system_append("workflow", "handoff_history", handoff)
        if not result:
            raise RuntimeError(f"Failed to record handoff: {result.reason}")

        self.logger.log_handoff(
            "handoff_created",
            handoff_id=handoff_id,
            project_id=sm.project_id,
            from_agent=from_agent,
            to_agent=to_agent,
        )

        # Track wire protocol compliance (metric only — never blocks)
        try:
            compliant, warnings = _wire_validator.validate(payload)
            sm.system_increment_wire_compliance(compliant)
            if not compliant:
                self.logger.log(
                    "wire_noncompliant",
                    handoff_id=handoff_id,
                    project_id=sm.project_id,
                    warnings=warnings,
                )
        except Exception as exc:
            logger.debug("wire-compliance tracking failed (non-blocking): %s", exc)

        # Persist to SQLite event log via typed EventRecorder (non-fatal)
        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=sm.project_id,
                actor=from_agent,
                action_type="handoff_created",
                intent=task_description,
                phase=phase,
                result_shape="handoff",
                payload={"handoff_id": handoff_id, "to_agent": to_agent},
            )
        except Exception as exc:
            logger.debug("handoff-created event recording failed (non-blocking): %s", exc)

        # Lint handoff summary for verbosity/compliance (non-fatal, never blocks)
        try:
            from core.engine.output_linter import OutputLinter
            from core.engine.event_recorder import EventRecorder
            summary = payload.get("summary", "")
            if summary:
                lint_result = OutputLinter().lint(summary, agent_id=from_agent)
                if lint_result.findings:
                    EventRecorder().record_simple(
                        project_id=sm.project_id,
                        actor=from_agent,
                        action_type="output_lint",
                        intent=f"Lint check on handoff {handoff_id} summary",
                        payload={"findings": lint_result.findings,
                                 "handoff_id": handoff_id},
                    )
        except Exception as exc:
            logger.debug("wire-lint event recording failed (non-blocking): %s", exc)

        # Audit skill usage on artifacts (non-fatal)
        try:
            from core.engine.skill_bridge import SkillBridge as _SkillBridge
            _SkillBridge().audit_handoff(handoff)
        except Exception as exc:
            logger.debug("skill-bridge handoff audit failed (non-blocking): %s", exc)

        return handoff

    # --- VALIDATE ---

    def validate(self, handoff: dict) -> tuple[bool, list[str]]:
        """
        Validate a handoff has all required fields.
        Accepts both expanded and compact format.
        Returns (is_valid, list_of_errors).
        """
        # Expand if compact
        h = self.expand(handoff)
        errors = []
        for key in ("handoff_id", "project_id", "from_agent", "to_agent",
                    "authorized_by", "phase", "task_description", "payload"):
            if not h.get(key):
                errors.append(f"Missing required field: {key}")

        payload = h.get("payload", {})
        for key in REQUIRED_PAYLOAD_KEYS:
            if key not in payload:
                errors.append(f"Missing payload field: {key}")

        return len(errors) == 0, errors

    # --- ACCEPT / REJECT ---

    def _update_handoff_in_state(self, sm: SharedStateManager,
                                  handoff_id: str, updates: dict) -> bool:
        """Find the handoff in history and update its acceptance record.
        Handles both expanded (handoff_id) and compact (id) keys."""
        state = sm.load()
        history = state.get("workflow", {}).get("handoff_history", [])
        found = False
        for h in history:
            hid = h.get("handoff_id") or h.get("id")
            if hid == handoff_id:
                # Handle both compact and expanded acceptance format
                if "acceptance" in h:
                    h["acceptance"].update(updates)
                else:
                    # Compact format: update acc, rej, fq, aat directly
                    if "status" in updates:
                        h["acc"] = updates["status"]
                    if updates.get("rejection_reason"):
                        h["rej"] = updates["rejection_reason"]
                    if updates.get("follow_up_questions"):
                        h["fq"] = updates["follow_up_questions"]
                    if updates.get("accepted_at"):
                        h["aat"] = updates["accepted_at"]
                found = True
                break
        if not found:
            return False
        from pathlib import Path
        import yaml as _yaml
        with open(sm.state_path, "w", encoding="utf-8") as f:
            _yaml.dump(state, f, default_flow_style=False,
                       allow_unicode=True, sort_keys=False)
        return True

    def accept(self, sm: SharedStateManager, handoff_id: str,
               follow_up_questions: Optional[list] = None) -> bool:
        """Accept a pending handoff. Returns True on success."""
        now = datetime.now(timezone.utc).isoformat()
        status = "accepted_with_questions" if follow_up_questions else "accepted"
        ok = self._update_handoff_in_state(sm, handoff_id, {
            "status": status,
            "accepted_at": now,
            "follow_up_questions": follow_up_questions,
        })
        if ok:
            self.logger.log_handoff(
                "handoff_accepted",
                handoff_id=handoff_id,
                project_id=sm.project_id,
                from_agent="",
                to_agent="",
                status=status,
            )
            try:
                CheckpointWriter(sm.project_id).write()
            except Exception as exc:
                logger.debug("checkpoint write failed on accept (non-blocking): %s", exc)

            # Persist to SQLite event log via typed EventRecorder (non-fatal)
            try:
                from core.engine.event_recorder import EventRecorder
                EventRecorder().record_simple(
                    project_id=sm.project_id,
                    actor="system",
                    action_type="handoff_accepted",
                    intent=f"{handoff_id} status={status}",
                    payload={"handoff_id": handoff_id, "status": status},
                )
            except Exception as exc:
                logger.debug("accept event recording failed (non-blocking): %s", exc)

            # D1 (AC1): auto-append compact 'dec' items → decisions.decision_log (non-fatal)
            try:
                state = sm.load()
                history = state.get("workflow", {}).get("handoff_history", [])
                ho = next((h for h in history
                           if (h.get("handoff_id") or h.get("id")) == handoff_id), None)
                if ho:
                    payload_dec = ho.get("payload", {}).get("dec", [])
                    now_ts = datetime.now(timezone.utc).isoformat()
                    for item in payload_dec:
                        if isinstance(item, dict) and item.get("id"):
                            entry = {
                                "decision_id": item["id"],
                                "value": item.get("v", ""),
                                "source_handoff": handoff_id,
                                "recorded_at": now_ts,
                            }
                            sm.system_append("decisions", "decision_log", entry)
            except Exception as exc:
                logger.debug("decision_log auto-population failed (non-blocking): %s", exc)

            # IP-3: auto-capture token accounting from the accepted handoff's
            # token_usage into communication counters (non-fatal). This is what
            # makes communication.total_tokens_used reflect real usage instead of
            # staying at 0 — it accumulates whatever usage the handoff carried.
            try:
                state = sm.load()
                history = state.get("workflow", {}).get("handoff_history", [])
                ho = next((h for h in history
                           if (h.get("handoff_id") or h.get("id")) == handoff_id), None)
                if ho:
                    tu = ho.get("token_usage") or {}
                    sm.system_add_tokens(
                        agent_id=str(ho.get("from_agent") or "unknown"),
                        phase=str(ho.get("phase")
                                  or state.get("core_identity", {}).get("current_phase")
                                  or "unknown"),
                        prompt_tokens=int(tu.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(tu.get("completion_tokens", 0) or 0),
                        total_tokens=int(tu.get("total_tokens", 0) or 0),
                    )
            except Exception as exc:
                logger.debug("token auto-capture on accept failed (non-blocking): %s", exc)

        return ok

    def reject(self, sm: SharedStateManager, handoff_id: str,
               reason: str) -> bool:
        """Reject a pending handoff. Returns True on success."""
        ok = self._update_handoff_in_state(sm, handoff_id, {
            "status": "rejected",
            "rejection_reason": reason,
        })
        if ok:
            self.logger.log_handoff(
                "handoff_rejected",
                handoff_id=handoff_id,
                project_id=sm.project_id,
                from_agent="",
                to_agent="",
                reason=reason,
            )
            try:
                from core.engine.event_recorder import EventRecorder
                EventRecorder().record_simple(
                    project_id=sm.project_id,
                    actor="system",
                    action_type="handoff_rejected",
                    intent=f"{handoff_id} reason={reason}",
                    payload={"handoff_id": handoff_id, "reason": reason},
                )
            except Exception as exc:
                logger.debug("reject event recording failed (non-blocking): %s", exc)
        return ok

    # --- QUERY ---

    def get(self, sm: SharedStateManager, handoff_id: str) -> Optional[dict]:
        """Get a specific handoff by ID. Returns expanded format."""
        state = sm.load()
        for h in state.get("workflow", {}).get("handoff_history", []):
            hid = h.get("handoff_id") or h.get("id")
            if hid == handoff_id:
                return self.expand(h)
        return None

    def get_pending(self, sm: SharedStateManager,
                    to_agent: Optional[str] = None) -> list[dict]:
        """Get all pending handoffs, optionally filtered by recipient. Returns expanded format."""
        state = sm.load()
        history = state.get("workflow", {}).get("handoff_history", [])
        pending = []
        for h in history:
            acc = h.get("acceptance", {}).get("status") if "acceptance" in h else h.get("acc")
            if acc == "pending":
                pending.append(self.expand(h))
        if to_agent:
            pending = [h for h in pending if h.get("to_agent") == to_agent]
        return pending

    def get_all(self, sm: SharedStateManager) -> list[dict]:
        """Get all handoffs for the project."""
        state = sm.load()
        return state.get("workflow", {}).get("handoff_history", [])

    # --- COMPACT WIRE FORMAT ---
    # Reduces token count for inter-agent storage.
    # CLI output and CHECKPOINT.md always use expand() for humans.

    # Expanded key → compact key
    _COMPACT_MAP = {
        "handoff_id": "id",
        "project_id": "pid",
        "timestamp": "ts",
        "from_agent": "from",
        "to_agent": "to",
        "authorized_by": "auth",
        "phase": "ph",
        "task_description": "task",
    }
    _PAYLOAD_COMPACT_MAP = {
        "summary": "s",
        "artifacts_produced": "art",
        "decisions_made": "dec",
        "open_questions": "oq",
        "constraints_for_next": "con",
        "shared_state_fields_modified": "mod",
    }
    _EXPAND_MAP = {v: k for k, v in _COMPACT_MAP.items()}
    _PAYLOAD_EXPAND_MAP = {v: k for k, v in _PAYLOAD_COMPACT_MAP.items()}

    @classmethod
    def compact(cls, handoff: dict) -> dict:
        """Convert an expanded handoff dict to compact wire format.
        Omits empty lists, null values, and zero-token entries."""
        c: dict = {}
        for full_key, short_key in cls._COMPACT_MAP.items():
            if full_key in handoff and handoff[full_key] is not None:
                c[short_key] = handoff[full_key]

        # Payload — omit empty lists
        payload = handoff.get("payload", {})
        p: dict = {}
        for full_key, short_key in cls._PAYLOAD_COMPACT_MAP.items():
            val = payload.get(full_key)
            if val is not None and val != "" and val != []:
                p[short_key] = val
        # Include any extra payload keys not in the standard map
        for k, v in payload.items():
            if k not in cls._PAYLOAD_COMPACT_MAP and v is not None and v != "" and v != []:
                p[k] = v
        if p:
            c["p"] = p

        # Token usage — compact to list [prompt, completion, total], omit if all zero
        tok = handoff.get("token_usage", {})
        tok_vals = [tok.get("prompt_tokens", 0), tok.get("completion_tokens", 0), tok.get("total_tokens", 0)]
        if any(t != 0 for t in tok_vals):
            c["tok"] = tok_vals

        # Acceptance — compact: just status, plus non-null fields
        acc = handoff.get("acceptance", {})
        acc_status = acc.get("status", "pending")
        c["acc"] = acc_status
        if acc.get("rejection_reason"):
            c["rej"] = acc["rejection_reason"]
        if acc.get("follow_up_questions"):
            c["fq"] = acc["follow_up_questions"]
        if acc.get("accepted_at"):
            c["aat"] = acc["accepted_at"]

        return c

    @classmethod
    def expand(cls, compact_handoff: dict) -> dict:
        """Convert a compact wire-format dict back to full expanded format.
        Also handles already-expanded dicts gracefully (passthrough)."""
        # Detect if already expanded
        if "handoff_id" in compact_handoff:
            return compact_handoff

        h: dict = {}
        for short_key, full_key in cls._EXPAND_MAP.items():
            if short_key in compact_handoff:
                h[full_key] = compact_handoff[short_key]

        # Payload
        cp = compact_handoff.get("p", {})
        payload: dict = {}
        for short_key, full_key in cls._PAYLOAD_EXPAND_MAP.items():
            payload[full_key] = cp.get(short_key, [] if full_key != "summary" else "")
        # Extra payload keys
        for k, v in cp.items():
            if k not in cls._PAYLOAD_EXPAND_MAP:
                payload[k] = v
        h["payload"] = payload

        # Token usage
        tok = compact_handoff.get("tok", [0, 0, 0])
        if isinstance(tok, list) and len(tok) == 3:
            h["token_usage"] = {"prompt_tokens": tok[0], "completion_tokens": tok[1], "total_tokens": tok[2]}
        else:
            h["token_usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Acceptance
        h["acceptance"] = {
            "status": compact_handoff.get("acc", "pending"),
            "rejection_reason": compact_handoff.get("rej"),
            "follow_up_questions": compact_handoff.get("fq"),
            "accepted_at": compact_handoff.get("aat"),
        }

        return h


# --- CLI ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="handoff_engine", description="Handoff Engine CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # create
    c = sub.add_parser("create", help="Create a new handoff")
    c.add_argument("--project-id", required=True)
    c.add_argument("--from", dest="from_agent", required=True)
    c.add_argument("--to", dest="to_agent", required=True)
    c.add_argument("--phase", required=True)
    c.add_argument("--task", required=True, dest="task_description")
    c.add_argument("--summary", default="")
    c.add_argument("--authorized-by", default="master_orchestrator")
    c.add_argument("--payload-json", help="Full payload as JSON (overrides --summary)")

    # accept
    a = sub.add_parser("accept", help="Accept a handoff")
    a.add_argument("--handoff-id", required=True)
    a.add_argument("--project-id", required=True)
    a.add_argument("--questions-json", help="Follow-up questions as JSON array")

    # reject
    r = sub.add_parser("reject", help="Reject a handoff")
    r.add_argument("--handoff-id", required=True)
    r.add_argument("--project-id", required=True)
    r.add_argument("--reason", required=True)

    # pending
    pe = sub.add_parser("pending", help="List pending handoffs")
    pe.add_argument("--project-id", required=True)
    pe.add_argument("--to-agent", default=None)

    # show
    s = sub.add_parser("show", help="Show a specific handoff")
    s.add_argument("--handoff-id", required=True)
    s.add_argument("--project-id", required=True)

    return p


def main_cli(args=None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(args)
    engine = HandoffEngine()
    sm = SharedStateManager(ns.project_id)

    if ns.command == "create":
        if ns.payload_json:
            payload = json.loads(ns.payload_json)
        else:
            payload = {
                "summary": ns.summary,
                "artifacts_produced": [],
                "decisions_made": [],
                "open_questions": [],
                "constraints_for_next": [],
                "shared_state_fields_modified": [],
            }
        handoff = engine.create(
            sm,
            from_agent=ns.from_agent,
            to_agent=ns.to_agent,
            phase=ns.phase,
            task_description=ns.task_description,
            payload=payload,
            authorized_by=ns.authorized_by,
        )
        print(f"OK handoff_id={handoff['handoff_id']}")

    elif ns.command == "accept":
        questions = json.loads(ns.questions_json) if ns.questions_json else None
        ok = engine.accept(sm, ns.handoff_id, follow_up_questions=questions)
        print("OK" if ok else "NOT FOUND")

    elif ns.command == "reject":
        ok = engine.reject(sm, ns.handoff_id, ns.reason)
        print("OK" if ok else "NOT FOUND")

    elif ns.command == "pending":
        pending = engine.get_pending(sm, to_agent=ns.to_agent)
        print(yaml.dump(pending, default_flow_style=False, allow_unicode=True))

    elif ns.command == "show":
        h = engine.get(sm, ns.handoff_id)
        if h:
            print(yaml.dump(h, default_flow_style=False, allow_unicode=True))
        else:
            print("NOT FOUND", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
