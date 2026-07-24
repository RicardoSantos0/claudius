"""
State Reconciler (proj-YYYYMMDD-NNN roadmap, Sprint 2 / P2)

Makes Claude-Code manual-mode projects first-class in the queryable event store.

In manual mode, SharedStateManager logs state writes / phase transitions / decisions to
the flat, rotating mas/audit.log — NOT to agent_events. So manual projects are invisible
to `mas rollup`, `mas events`, and cross-project metrics. This reconciler reads the
durable per-project shared_state.yaml (the source of truth that does NOT rotate) and
synthesizes the canonical lifecycle/decision events into agent_events for any project
that is missing them.

Design:
  - EVENT-IDEMPOTENT: each canonical event has a deterministic reconcile_key. Existing
    native and reconciled events are mapped to the same keys, so later state additions
    are appended without replaying older events.
  - LOSSLESS-DIRECTIONAL: we never delete or mutate existing events; we only add missing
    canonical ones. Native events (from `mas init`/`mas close`) always win — we only fill
    gaps.
  - Reads shared_state.yaml, not audit.log: the log rotates at 5 MB and is lossy; the
    per-project state file is durable.

Canonical events synthesized (taxonomy names from foundation/event_types.yaml):
  project_initialized · phase_transition · decision_recorded · project_closed
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from core.db import DB_PATH, _get_connection
from core.utils.log_helpers import append_event

from core.paths import mas_root
ROOT = mas_root()
PROJECTS_DIR = ROOT / "projects"

_ACTOR = "master_orchestrator"


def _decision_key(decision: object) -> str:
    if isinstance(decision, dict):
        decision_id = decision.get("decision_id") or decision.get("id")
        if decision_id:
            return f"decision_recorded:{decision_id}"
    encoded = json.dumps(
        decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"decision_recorded:sha256-{digest}"


def _existing_reconcile_keys(conn, project_id: str) -> set[str]:
    """Map both native and reconciled lifecycle events to canonical event keys."""
    rows = conn.execute(
        "SELECT action_type, intent, payload FROM agent_events WHERE project_id=?",
        (project_id,),
    ).fetchall()
    keys: set[str] = set()
    for row in rows:
        action_type = row[0]
        intent = str(row[1] or "")
        try:
            payload = json.loads(row[2] or "{}")
        except Exception:
            payload = {}
        payload = payload.get("params", {}).get("inputs", payload)
        explicit = payload.get("reconcile_key")
        if explicit:
            keys.add(str(explicit))
            continue
        if action_type == "project_initialized":
            keys.add("project_initialized")
        elif action_type == "phase_transition":
            phase = (
                payload.get("from_phase")
                or payload.get("completed_phase")
                or payload.get("phase")
            )
            if not phase and intent.lower().startswith("phase completed:"):
                phase = intent.split(":", 1)[1].split("(", 1)[0].strip()
            if phase:
                keys.add(f"phase_transition:{phase}")
        elif action_type in {"decision_recorded", "decision_made"}:
            decision = payload.get("decision")
            if decision is None:
                decision = {
                    "decision_id": payload.get("decision_id") or payload.get("id")
                }
            keys.add(_decision_key(decision))
        elif action_type == "project_closed":
            keys.add("project_closed")
    return keys


def reconcile_project(project_id: str, state: dict, db_path: Path = DB_PATH,
                      dry_run: bool = False) -> dict:
    """Synthesize missing canonical events for one project. Returns a summary dict."""
    ci = state.get("core_identity", {}) or {}
    wf = state.get("workflow", {}) or {}
    decisions = (state.get("decisions", {}) or {}).get("decision_log", []) or []

    with _get_connection(db_path) as conn:
        existing = _existing_reconcile_keys(conn, project_id)

    planned: list[tuple[str, str, str, dict]] = []

    # 1. project_initialized — only if not natively recorded
    key = "project_initialized"
    if key not in existing and ci.get("created_at"):
        planned.append((
            key,
            "project_initialized",
            f"Project initialized in {wf.get('mode', 'standard')} mode (reconciled)",
            {"reconciled": True, "reconcile_key": key,
             "mode": wf.get("mode", "standard"),
             "created_at": ci.get("created_at")},
        ))

    # 2. phase_transition — one key per completed phase
    for ph in wf.get("completed_phases", []) or []:
        key = f"phase_transition:{ph}"
        if key not in existing:
            planned.append((
                key,
                "phase_transition",
                f"Phase completed: {ph} (reconciled)",
                {"reconciled": True, "reconcile_key": key, "phase": ph},
            ))

    # 3. decision_recorded — one key per decision
    for d in decisions:
        key = _decision_key(d)
        if key not in existing:
            did = d.get("decision_id", "?") if isinstance(d, dict) else "?"
            planned.append((
                key,
                "decision_recorded",
                f"Decision {did} (reconciled)",
                {"reconciled": True, "reconcile_key": key, "decision": d},
            ))

    # 4. project_closed — only if status closed and not natively recorded
    key = "project_closed"
    if ci.get("status") == "closed" and key not in existing:
        planned.append((
            key,
            "project_closed",
            "Project closed (reconciled)",
            {"reconciled": True, "reconcile_key": key,
             "final_phase": ci.get("current_phase", "closed")},
        ))

    if not dry_run:
        for _key, action_type, intent, payload in planned:
            append_event(
                project_id=project_id,
                agent_id=_ACTOR,
                action_type=action_type,
                intent=intent,
                result_shape="reconciled_event",
                payload=payload,
                db_path=db_path,
            )

    return {"project_id": project_id, "added": len(planned),
            "kinds": sorted({p[1] for p in planned})}


def reconcile_all(db_path: Path = DB_PATH, dry_run: bool = False,
                  projects_dir: Path = PROJECTS_DIR) -> dict:
    """Reconcile every project folder that has a shared_state.yaml. Returns a summary."""
    from core.utils.config import iter_project_dirs
    results = []
    for d in iter_project_dirs(projects_root=projects_dir):  # flat + family-nested
        sy = os.path.join(str(d), "shared_state.yaml")
        if not os.path.exists(sy):
            continue
        try:
            state = yaml.safe_load(open(sy, encoding="utf-8")) or {}
        except Exception:
            continue
        pid = d.name
        results.append(reconcile_project(pid, state, db_path=db_path, dry_run=dry_run))

    total_added = sum(r.get("added", 0) for r in results)
    touched = [r for r in results if r.get("added", 0) > 0]
    return {
        "projects_scanned": len(results),
        "projects_updated": len(touched),
        "events_added": total_added,
        "dry_run": dry_run,
        "details": touched,
    }
