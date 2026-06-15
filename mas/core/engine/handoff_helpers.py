"""
MAS Handoff Helpers — T-M3-005
Re-anchor YAML handoff builder: delta-only summaries instead of raw state dumps.
New standalone file — no imports from existing mas/core/ modules.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional


def build_reanchor_payload(
    project_id: str,
    phase: str,
    current_owner: str,
    next_agent: str,
    next_action: str,
    tried: Optional[list] = None,
    worked: Optional[list] = None,
    failed: Optional[list] = None,
    do_not_retry: Optional[list] = None,
    open_questions: Optional[list] = None,
    artifacts_produced: Optional[list] = None,
    decisions_made: Optional[list] = None,
) -> dict:
    """
    Build a compact re-anchor handoff payload.
    Replaces raw shared_state dumps in handoff payloads.
    Contains only delta information since the last milestone.
    """
    return {
        "re_anchor": True,
        "project_id": project_id,
        "phase": phase,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "from_agent": current_owner,
        "to_agent": next_agent,
        "next_action": next_action,
        "tried": tried or [],
        "worked": worked or [],
        "failed": failed or [],
        "do_not_retry": do_not_retry or [],
        "open_questions": open_questions or [],
        "artifacts_produced": artifacts_produced or [],
        "decisions_made": decisions_made or [],
    }


def extract_delta(
    previous_state: dict,
    current_state: dict,
    watched_fields: Optional[list] = None,
) -> dict:
    """
    Return only the fields in current_state that differ from previous_state.
    Used to build delta-only handoff payloads.
    """
    watched = watched_fields or list(current_state.keys())
    delta = {}
    for field in watched:
        prev = previous_state.get(field)
        curr = current_state.get(field)
        if prev != curr:
            delta[field] = curr
    return delta


def summarise_handoff_history(history: list, last_n: int = 3) -> list:
    """
    Return a compact summary of the last N handoffs.
    Strips large payloads, keeps agent routing and phase info.
    """
    summaries = []
    for h in history[-last_n:]:
        summaries.append({
            "handoff_id": h.get("handoff_id"),
            "from": h.get("from_agent"),
            "to": h.get("to_agent"),
            "phase": h.get("phase"),
            "timestamp": h.get("timestamp"),
            "summary": h.get("payload", {}).get("summary", ""),
            "status": h.get("acceptance", {}).get("status"),
        })
    return summaries


def payload_token_estimate(payload: dict) -> int:
    """Rough token estimate for a handoff payload (~4 chars per token)."""
    return max(1, len(json.dumps(payload)) // 4)
