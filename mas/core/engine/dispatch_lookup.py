"""Read path for engine dispatches (the first step of prop-020, the governed read path).

A dispatch is the `execution_route_selection` decision event written by
`ExecutionProfileRouter.record_route_selection`. `manual_loop.verify_manual_dispatch`
reads the latest one per phase; this module answers a different question, "does this
dispatch id exist for this project and agent?", through the same event query.
"""

from __future__ import annotations

import json



def normalize_agent_name(name: str | None) -> str:
    """Hyphen and underscore forms of an agent name compare equal."""
    return str(name or "").strip().lower().replace("-", "_")


def _selection(row: dict) -> dict | None:
    try:
        payload = json.loads(row.get("payload") or "{}")
    except (TypeError, ValueError):
        return None
    inputs = payload.get("params", {}).get("inputs", payload)
    if inputs.get("decision_type") != "execution_route_selection":
        return None
    selection = inputs.get("route_selection")
    return selection if isinstance(selection, dict) else None


def find_dispatch(project_id: str, dispatch_id: str, agent_id: str, *, limit: int = 1000) -> dict | None:
    """Return the route selection for `dispatch_id` served to `agent_id`, else None.

    Missing event storage counts as "no dispatch", never as proof of one.
    """
    from core.db import query_events

    wanted = str(dispatch_id or "").strip().lower()
    if not wanted:
        return None
    try:
        rows = query_events(project_id=project_id, action_type="decision_recorded", limit=limit)
    except Exception:
        return None
    for row in rows:
        selection = _selection(row)
        if not selection or str(selection.get("dispatch_id") or "").lower() != wanted:
            continue
        served = selection.get("agent_id") or row.get("agent_id")
        if normalize_agent_name(served) == normalize_agent_name(agent_id):
            return selection
    return None
