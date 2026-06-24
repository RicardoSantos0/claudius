"""MAS consistency check (G3).

Detects the dual-store divergence that let proj-YYYYMMDD-NNN lose decision d-004
and run with a populated TaskBoard but an empty shared_state task view:

  - decisions : shared_state.decisions.decision_log  vs  decisions/decision_log.yaml
  - tasks     : shared_state.execution.{tasks}        vs  the TaskBoard file

Canonical sources (per CHECKPOINT + metrics_engine): shared_state is canonical for
decisions; the TaskBoard file is canonical for tasks. The checks flag drift; they
do not mutate state (a `mas doctor` fix path can reconcile separately).

Part of proj-YYYYMMDD-NNN-mas-manual-loop-guardrails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ConsistencyReport:
    project_id: str
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _load_yaml(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _decision_ids(obj) -> set[str]:
    """Extract decision_ids from a flat list, {decisions: [...]}, {decision_log: [...]},
    or a legacy dict-of-decisions."""
    ids: set[str] = set()
    if obj is None:
        return ids
    decs = obj
    if isinstance(obj, dict):
        decs = obj.get("decision_log") or obj.get("decisions") or []
    if isinstance(decs, dict):
        decs = list(decs.values())
    if isinstance(decs, list):
        for d in decs:
            if isinstance(d, dict):
                did = d.get("decision_id") or d.get("id")
                if did:
                    ids.add(did)
    return ids


def check_decision_consistency(state_decisions, disk_decisions) -> list[dict]:
    """Compare decision ids between canonical state and the on-disk decision_log file.

    disk_only (decisions on disk missing from state) is HIGH — that is data loss
    (the d-004 case). state_only (disk file trails state) is LOW — benign.
    """
    ss = _decision_ids(state_decisions)
    disk = _decision_ids(disk_decisions)
    findings: list[dict] = []
    disk_only = sorted(disk - ss)
    state_only = sorted(ss - disk)
    if disk_only:
        findings.append({
            "check": "decisions",
            "direction": "disk_only",
            "severity": "high",
            "ids": disk_only,
            "detail": "decisions on disk are missing from canonical state (data-loss risk)",
        })
    if state_only:
        findings.append({
            "check": "decisions",
            "direction": "state_only",
            "severity": "low",
            "ids": state_only,
            "detail": "decisions/decision_log.yaml trails canonical state",
        })
    return findings


def check_task_store_consistency(state_execution, task_board_data) -> list[dict]:
    """Compare task ids between shared_state.execution and the TaskBoard file.

    shared_state.execution.tasks is a legacy/vestigial mirror; the TaskBoard is
    canonical. Only a genuine *conflict* (both populated and disagreeing) is flagged
    — the normal modern layout (legacy field empty, board populated) is not drift,
    so this does not cry wolf on every project.
    """
    ss_tasks = {t.get("task_id") for t in (state_execution.get("tasks") or [])
                if isinstance(t, dict)}
    tb_tasks = {t.get("task_id") for t in (task_board_data.get("tasks") or [])
                if isinstance(t, dict)}
    ss_tasks.discard(None)
    tb_tasks.discard(None)
    if not ss_tasks or ss_tasks == tb_tasks:
        return []
    return [{
        "check": "tasks",
        "severity": "medium",
        "state_only": sorted(ss_tasks - tb_tasks),
        "board_only": sorted(tb_tasks - ss_tasks),
        "detail": "shared_state.execution.tasks conflicts with the TaskBoard (use the TaskBoard as canonical)",
    }]


def check_project(project_id: str, projects_root: Path | None = None) -> ConsistencyReport:
    """Load all sources for a project and run every consistency check."""
    from core.utils.config import resolve_project_dir
    pdir = resolve_project_dir(project_id, projects_root=projects_root)

    state = _load_yaml(pdir / "shared_state.yaml") or {}
    disk_dec = _load_yaml(pdir / "decisions" / "decision_log.yaml")

    findings: list[dict] = []
    findings += check_decision_consistency(state.get("decisions") or {}, disk_dec)

    from core.engine.task_board import TaskBoard
    board = {"tasks": TaskBoard(project_id, projects_root=projects_root).list_tasks()}
    findings += check_task_store_consistency(state.get("execution") or {}, board)

    return ConsistencyReport(project_id, findings)
