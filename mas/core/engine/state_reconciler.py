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
  - IDEMPOTENT: each synthesized event carries payload {"reconciled": true}. A project is
    skipped if it already has reconciled (or natively-recorded) events of that kind, so
    re-running never double-counts.
  - LOSSLESS-DIRECTIONAL: we never delete or mutate existing events; we only add missing
    canonical ones. Native events (from `mas init`/`mas close`) always win — we only fill
    gaps.
  - Reads shared_state.yaml, not audit.log: the log rotates at 5 MB and is lossy; the
    per-project state file is durable.

Canonical events synthesized (taxonomy names from foundation/event_types.yaml):
  project_initialized · phase_transition · decision_recorded · project_closed
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import yaml

from core.db import DB_PATH, _get_connection
from core.utils.log_helpers import append_event

from core.paths import mas_root
ROOT = mas_root()
PROJECTS_DIR = ROOT / "projects"

_ACTOR = "master_orchestrator"


def _existing_action_types(conn, project_id: str) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT action_type FROM agent_events WHERE project_id=?",
        (project_id,),
    ).fetchall()
    return {r[0] for r in rows}


def _reconciled_count(conn, project_id: str) -> int:
    """How many already-reconciled events this project has (idempotency marker)."""
    rows = conn.execute(
        "SELECT COUNT(*) FROM agent_events WHERE project_id=? AND payload LIKE '%\"reconciled\": true%'",
        (project_id,),
    ).fetchone()
    return rows[0] if rows else 0


def reconcile_project(project_id: str, state: dict, db_path: Path = DB_PATH,
                      dry_run: bool = False) -> dict:
    """Synthesize missing canonical events for one project. Returns a summary dict."""
    ci = state.get("core_identity", {}) or {}
    wf = state.get("workflow", {}) or {}
    decisions = (state.get("decisions", {}) or {}).get("decision_log", []) or []

    with _get_connection(db_path) as conn:
        existing = _existing_action_types(conn, project_id)
        already_reconciled = _reconciled_count(conn, project_id)

    # If this project already has reconciled events, treat it as done (idempotent).
    if already_reconciled > 0:
        return {"project_id": project_id, "skipped": "already_reconciled", "added": 0}

    planned: list[tuple[str, str, dict]] = []  # (action_type, intent, payload)

    # 1. project_initialized — only if not natively recorded
    if "project_initialized" not in existing and ci.get("created_at"):
        planned.append((
            "project_initialized",
            f"Project initialized in {wf.get('mode', 'standard')} mode (reconciled)",
            {"reconciled": True, "mode": wf.get("mode", "standard"),
             "created_at": ci.get("created_at")},
        ))

    # 2. phase_transition — one per completed phase, if none natively recorded
    if "phase_transition" not in existing:
        for ph in wf.get("completed_phases", []) or []:
            planned.append((
                "phase_transition",
                f"Phase completed: {ph} (reconciled)",
                {"reconciled": True, "phase": ph},
            ))

    # 3. decision_recorded — one per decision_log entry, if none natively recorded
    if "decision_recorded" not in existing:
        for d in decisions:
            did = d.get("decision_id", "?") if isinstance(d, dict) else "?"
            planned.append((
                "decision_recorded",
                f"Decision {did} (reconciled)",
                {"reconciled": True, "decision": d},
            ))

    # 4. project_closed — only if status closed and not natively recorded
    if ci.get("status") == "closed" and "project_closed" not in existing:
        planned.append((
            "project_closed",
            "Project closed (reconciled)",
            {"reconciled": True, "final_phase": ci.get("current_phase", "closed")},
        ))

    if not dry_run:
        for action_type, intent, payload in planned:
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
            "kinds": sorted({p[0] for p in planned})}


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
