"""
MAS Context Compressor — T-M3-004
Progressive disclosure: summary first, detail on demand.
Reduces injected token count by filtering to only relevant fields.
New standalone file — no imports from existing mas/core/ modules.
"""

from typing import Any, Optional

# Fields always included in a summary (high signal, low token cost)
SUMMARY_FIELDS = {
    "core_identity": ["project_id", "current_phase", "status"],
    "project_definition": ["project_goal", "success_criteria"],
    "workflow": ["current_owner", "completed_phases", "pending_assignments"],
    "decisions": ["open_questions"],
}

# Fields included only on explicit request
DETAIL_FIELDS = {
    "core_identity": ["created_at", "updated_at", "request_id"],
    "workflow": ["handoff_history", "active_agents"],
    "decisions": ["decision_log", "assumptions", "approvals", "policy_flags"],
    "artifacts": ["documents", "deliverables", "change_log"],
    "execution": ["milestones", "tasks", "progress_reports", "blocker_alerts"],
    "capability": ["available_skills_snapshot", "reuse_candidates",
                   "capability_gap_certificates", "spawn_requests"],
}

# Re-anchor YAML keys — tiny payload for handoff continuity
REANCHOR_FIELDS = ["tried", "worked", "failed", "do_not_retry",
                   "current_owner", "next_action", "open_questions"]


def compress(state: dict, mode: str = "summary") -> dict:
    """
    Return a compressed view of shared state.

    modes:
      'summary'  — high-signal fields only (default, lowest token cost)
      'detail'   — summary + detail fields
      'full'     — unchanged (use sparingly)
      'reanchor' — minimal re-anchor payload for handoff continuity
    """
    if mode == "full":
        return state

    if mode == "reanchor":
        return _reanchor(state)

    result: dict = {}
    for section, fields in SUMMARY_FIELDS.items():
        if section in state:
            result[section] = {k: state[section][k]
                               for k in fields
                               if k in state.get(section, {})}

    if mode == "detail":
        for section, fields in DETAIL_FIELDS.items():
            if section in state:
                detail = {k: state[section][k]
                          for k in fields
                          if k in state.get(section, {})}
                if detail:
                    result.setdefault(section, {}).update(detail)

    return result


def _reanchor(state: dict) -> dict:
    """Build a minimal re-anchor payload from shared state."""
    core = state.get("core_identity", {})
    workflow = state.get("workflow", {})
    decisions = state.get("decisions", {})
    return {
        "project_id": core.get("project_id"),
        "phase": core.get("current_phase"),
        "current_owner": workflow.get("current_owner"),
        "open_questions": decisions.get("open_questions", []),
        # Callers populate tried/worked/failed/do_not_retry/next_action
    }


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def compression_ratio(original: dict, compressed: dict) -> float:
    """Return the compression ratio (0–1, lower = more compressed)."""
    import json
    orig_len = len(json.dumps(original))
    comp_len = len(json.dumps(compressed))
    if orig_len == 0:
        return 1.0
    return comp_len / orig_len


def build_reanchor(
    project_id: str,
    phase: str,
    current_owner: str,
    tried: Optional[list] = None,
    worked: Optional[list] = None,
    failed: Optional[list] = None,
    do_not_retry: Optional[list] = None,
    next_action: str = "",
    open_questions: Optional[list] = None,
) -> dict:
    """Explicitly build a re-anchor handoff payload."""
    return {
        "project_id": project_id,
        "phase": phase,
        "current_owner": current_owner,
        "tried": tried or [],
        "worked": worked or [],
        "failed": failed or [],
        "do_not_retry": do_not_retry or [],
        "next_action": next_action,
        "open_questions": open_questions or [],
    }
