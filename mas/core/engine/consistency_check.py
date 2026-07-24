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

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ConsistencyReport:
    project_id: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    repair_preview: dict[str, Any] = field(default_factory=dict)

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


def check_decision_record_quality(state_decisions: Any) -> list[dict]:
    """Warn on decision records that cannot meet the documented quality contract."""
    decisions = state_decisions
    if isinstance(decisions, dict):
        decisions = decisions.get("decision_log") or decisions.get("decisions") or []
    if not isinstance(decisions, list):
        return []
    incomplete: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions, start=1):
        if not isinstance(decision, dict):
            continue
        missing = []
        if not decision.get("rationale"):
            missing.append("rationale")
        if "alternatives_considered" not in decision:
            missing.append("alternatives_considered")
        if not decision.get("related_to"):
            missing.append("related_to")
        if missing:
            incomplete.append(
                {
                    "decision_id": (
                        decision.get("decision_id") or decision.get("id") or index
                    ),
                    "missing": missing,
                }
            )
    if not incomplete:
        return []
    return [
        {
            "check": "decision_record_quality",
            "severity": "low",
            "decisions": incomplete,
            "detail": (
                "decision records are missing rationale, alternatives, or linkage; "
                "future wire decisions should include rat/alt/rel"
            ),
        }
    ]


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


def _entity_id(entity: dict[str, Any], entity_type: str) -> str:
    keys = (
        ("task_id", "id")
        if entity_type == "task"
        else ("milestone_id", "id")
    )
    return str(next((entity.get(key) for key in keys if entity.get(key)), ""))


def _entity_text(entity: dict[str, Any], entity_type: str) -> str:
    keys = (
        ("description", "title", "name")
        if entity_type == "task"
        else ("name", "title", "description")
    )
    return str(next((entity.get(key) for key in keys if entity.get(key)), ""))


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _semantic_similarity(left: str, right: str) -> float:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, jaccard)


def _plan_entities(plan: dict[str, Any], entity_type: str) -> list[dict[str, Any]]:
    if entity_type == "milestone":
        raw = plan.get("milestones", []) or []
    else:
        raw = list(plan.get("tasks", []) or [])
        for phase in plan.get("phases", []) or []:
            if isinstance(phase, dict):
                raw.extend(phase.get("tasks", []) or [])
    return [dict(item) for item in raw if isinstance(item, dict)]


def check_plan_board_identifier_drift(
    plan: dict[str, Any],
    board: dict[str, Any],
    *,
    threshold: float = 0.88,
) -> list[dict[str, Any]]:
    """Find highly similar unmatched plan/board records without merging them."""
    findings: list[dict[str, Any]] = []
    for entity_type, board_key in (
        ("task", "tasks"),
        ("milestone", "milestones"),
    ):
        plan_entities = _plan_entities(plan, entity_type)
        board_entities = [
            dict(item)
            for item in (board.get(board_key, []) or [])
            if isinstance(item, dict)
        ]
        plan_ids = {_entity_id(item, entity_type) for item in plan_entities}
        board_ids = {_entity_id(item, entity_type) for item in board_entities}
        unmatched_plan = [
            item
            for item in plan_entities
            if _entity_id(item, entity_type)
            and _entity_id(item, entity_type) not in board_ids
        ]
        unmatched_board = [
            item
            for item in board_entities
            if _entity_id(item, entity_type)
            and _entity_id(item, entity_type) not in plan_ids
        ]
        pairs: list[dict[str, Any]] = []
        for plan_item in unmatched_plan:
            for board_item in unmatched_board:
                similarity = _semantic_similarity(
                    _entity_text(plan_item, entity_type),
                    _entity_text(board_item, entity_type),
                )
                if similarity >= threshold:
                    pairs.append(
                        {
                            "plan": plan_item,
                            "board": board_item,
                            "similarity": round(similarity, 4),
                        }
                    )
        plan_counts: dict[str, int] = {}
        board_counts: dict[str, int] = {}
        for pair in pairs:
            plan_id = _entity_id(pair["plan"], entity_type)
            board_id = _entity_id(pair["board"], entity_type)
            plan_counts[plan_id] = plan_counts.get(plan_id, 0) + 1
            board_counts[board_id] = board_counts.get(board_id, 0) + 1
        for plan_item in unmatched_plan:
            plan_id = _entity_id(plan_item, entity_type)
            candidate_pairs = [
                pair
                for pair in pairs
                if _entity_id(pair["plan"], entity_type) == plan_id
            ]
            if not candidate_pairs:
                continue
            candidates = []
            ambiguous = plan_counts.get(plan_id, 0) > 1
            for pair in sorted(
                candidate_pairs,
                key=lambda item: (
                    -float(item["similarity"]),
                    _entity_id(item["board"], entity_type),
                ),
            ):
                board_item = pair["board"]
                board_id = _entity_id(board_item, entity_type)
                candidate_ambiguous = board_counts.get(board_id, 0) > 1
                ambiguous = ambiguous or candidate_ambiguous
                candidates.append(
                    {
                        "board_id": board_id,
                        "description": _entity_text(board_item, entity_type),
                        "dependencies": (
                            board_item.get("dependencies")
                            or board_item.get("depends_on")
                            or board_item.get("task_ids")
                            or []
                        ),
                        "status": board_item.get("status"),
                        "similarity": pair["similarity"],
                        "ambiguous": candidate_ambiguous,
                    }
                )
            findings.append(
                {
                    "check": "plan_board_identifier_drift",
                    "severity": "medium",
                    "entity_type": entity_type,
                    "plan_id": plan_id,
                    "plan_description": _entity_text(plan_item, entity_type),
                    "plan_dependencies": (
                        plan_item.get("dependencies")
                        or plan_item.get("depends_on")
                        or plan_item.get("task_ids")
                        or plan_item.get("requires")
                        or []
                    ),
                    "plan_status": plan_item.get("status"),
                    "candidates": candidates,
                    "ambiguous": ambiguous,
                    "detail": (
                        "unmatched exact IDs describe highly similar work; exact IDs "
                        "remain authoritative and no automatic merge is permitted"
                    ),
                }
            )
    return findings


def build_plan_board_repair_preview(
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return an explicit, non-mutating review plan for semantic ID drift."""
    drift = [
        finding
        for finding in findings
        if finding.get("check") == "plan_board_identifier_drift"
    ]
    candidates: list[dict[str, Any]] = []
    for finding in drift:
        for board_candidate in finding.get("candidates", []) or []:
            candidates.append(
                {
                    "entity_type": finding.get("entity_type"),
                    "plan_id": finding.get("plan_id"),
                    "board_id": board_candidate.get("board_id"),
                    "plan_description": finding.get("plan_description"),
                    "board_description": board_candidate.get("description"),
                    "plan_dependencies": finding.get("plan_dependencies", []),
                    "board_dependencies": board_candidate.get(
                        "dependencies", []
                    ),
                    "plan_status": finding.get("plan_status"),
                    "board_status": board_candidate.get("status"),
                    "similarity": board_candidate.get("similarity"),
                    "ambiguous": bool(
                        finding.get("ambiguous")
                        or board_candidate.get("ambiguous")
                    ),
                    "proposed_action": "review_identifier_alignment",
                }
            )
    return {
        "status": (
            "ambiguous"
            if any(item["ambiguous"] for item in candidates)
            else "review_required"
            if candidates
            else "clear"
        ),
        "write_performed": False,
        "automatic_merge_allowed": False,
        "authoritative_key": "exact task_id/milestone_id",
        "candidates": candidates,
    }


# "closed" is an administrative terminal action (mas close), not a delegated work
# phase, so it never carries a handoff — excluded to avoid a guaranteed false
# positive on every closed project.
_NON_HANDOFF_PHASES = {"closed"}


def check_completed_phase_handoffs(workflow: dict) -> list[dict]:
    """G4 manual-loop soft gate: warn when a completed work phase has no handoff.

    Standard-mode MAS projects should record a governed handoff for each phase
    they complete (manual-loop discipline — all surfaces). A completed phase with
    zero matching handoffs in handoff_history is surfaced as a low-severity
    finding (warn only, never blocks). Lite mode is intentionally exempt.
    """
    if str(workflow.get("mode") or "standard") != "standard":
        return []
    phases_with_handoffs = {
        h.get("phase") for h in (workflow.get("handoff_history") or [])
        if isinstance(h, dict)
    }
    missing = [
        p for p in dict.fromkeys(workflow.get("completed_phases") or [])
        if p and p not in _NON_HANDOFF_PHASES and p not in phases_with_handoffs
    ]
    if not missing:
        return []
    return [{
        "check": "manual_loop_discipline",
        "severity": "low",
        "phases": missing,
        "detail": "completed phase(s) have no recorded handoff; materialize phase "
                  "transitions with mas_handoff_create (manual-loop discipline)",
    }]


def check_project(project_id: str, projects_root: Path | None = None) -> ConsistencyReport:
    """Load all sources for a project and run every consistency check."""
    from core.utils.config import resolve_project_dir
    pdir = resolve_project_dir(project_id, projects_root=projects_root)

    state = _load_yaml(pdir / "shared_state.yaml") or {}
    disk_dec = _load_yaml(pdir / "decisions" / "decision_log.yaml")

    findings: list[dict] = []
    findings += check_decision_consistency(state.get("decisions") or {}, disk_dec)
    findings += check_decision_record_quality(state.get("decisions") or {})

    from core.engine.task_board import TaskBoard
    task_board = TaskBoard(project_id, projects_root=projects_root)
    board = {
        "tasks": task_board.list_tasks(),
        "milestones": task_board.list_milestones(),
    }
    findings += check_task_store_consistency(state.get("execution") or {}, board)
    plan = _load_yaml(pdir / "planning" / "execution_plan.yaml") or {}
    if isinstance(plan, dict):
        findings += check_plan_board_identifier_drift(plan, board)

    findings += check_completed_phase_handoffs(state.get("workflow") or {})

    return ConsistencyReport(
        project_id,
        findings,
        repair_preview=build_plan_board_repair_preview(findings),
    )
