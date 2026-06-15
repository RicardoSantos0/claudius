"""
Training Engine
Analyzes evaluation reports and produces improvement proposals.

Authority: L0 advisory only — proposes changes, never applies them.

Proposal lifecycle:
  pending → approved → applied
           → rejected (can be resubmitted with new evidence)

Backlog: roster/training_backlog.yaml
Training brief (per project): projects/{pid}/training/training_brief.yaml

CLI usage:
  uv run python core/training_engine.py analyze --project-id {pid}
  uv run python core/training_engine.py backlog
  uv run python core/training_engine.py approve --proposal-id {id} --authorized-by master_orchestrator
  uv run python core/training_engine.py reject --proposal-id {id} --reason "{reason}" --authorized-by master_orchestrator
"""

from __future__ import annotations

import sys
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

try:
    from .metrics_engine import MetricsEngine
except ImportError:
    MetricsEngine = None  # type: ignore

try:
    from core.db import append_event as _append_event
except Exception:
    _append_event = None  # type: ignore

logger = logging.getLogger(__name__)


def _log_training_event(project_id: str, agent_id: str, action_type: str,
                        intent: str, payload: dict) -> None:
    if _append_event is None:
        return
    try:
        _append_event(
            project_id=project_id,
            agent_id=agent_id,
            action_type=action_type,
            intent=intent,
            result_shape="training",
            payload=payload,
        )
    except Exception as exc:
        logger.debug("training DB logging failed (non-blocking): %s", exc)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical roster lives under mas/roster/ (single source of truth). This module
# is at mas/core/engine/training_engine.py, so parents[2] is the mas/ directory.
# (Previously a cwd-relative "roster/" path, which created a divergent duplicate
# at the repo root — see req-004 roster consolidation.)
_ROSTER_DIR = Path(__file__).resolve().parents[2] / "roster"
BACKLOG_FILE = _ROSTER_DIR / "training_backlog.yaml"
EXEMPLARY_REFS_FILE = _ROSTER_DIR / "exemplary_references.yaml"

# Priority scores (higher = more urgent)
PRIORITY_SCORES: dict[str, int] = {
    "boundary_violation": 5,
    "governance_failure": 4,
    "repeated_quality_issue": 3,
    "communication_waste": 2,
    "context_bloat": 2,
    "efficiency_improvement": 2,
    "prompt_refinement": 1,
}

# Project metrics that, if low, trigger proposals
LOW_THRESHOLD = 70.0
SYSTEMIC_MIN_REPORTS = 2   # need this many reports showing same issue

# Generic metric/intermediate-state labels that surface as raw report keys but are
# not actionable governance findings. Filtered out of systemic proposals to avoid
# backlog noise (prop-008-003 from proj-YYYYMMDD-NNN).
NON_ACTIONABLE_FINDINGS = frozenset({
    "bottlenecks", "drift_detected", "pattern_issues", "total_violations",
    "patterns", "metrics", "summary", "counts",
})

PROPOSAL_STATUSES = {"pending", "approved", "rejected", "applied", "deferred"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrainingProposal:
    proposal_id: str
    proposal_type: str              # boundary_violation | governance_failure | repeated_quality_issue | efficiency_improvement | prompt_refinement
    priority: int                   # 1-5 (5 = highest)
    target_agent: str               # agent_id or "system"
    target_artifact: str            # e.g. "agents/evaluator_agent.md" or "policies/evaluation_policy.yaml"
    description: str                # what was observed
    recommended_change: str         # what to change
    evidence: list[str]             # report_ids or finding_ids
    tradeoffs: str                  # potential downsides of the change
    minimum_evidence_met: bool      # True if evidence threshold satisfied
    systemic: bool                  # True if pattern seen in 2+ reports
    status: str = "pending"         # pending | approved | rejected | applied
    rejection_reason: str = ""
    original_proposal_id: str = ""  # if resubmission
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TrainingEngine
# ---------------------------------------------------------------------------

class TrainingEngine:

    # ------------------------------------------------------------------
    # Analysis: single report
    # ------------------------------------------------------------------

    def _is_duplicate(self, description: str, backlog_proposals: list[dict]) -> bool:
        """
        D6 (AC6): Return True if the same description already exists in the backlog
        with status 'applied' or 'approved'. Prevents re-generating identical proposals
        that have already been actioned.
        """
        for existing in backlog_proposals:
            if existing.get("status") not in ("applied", "approved"):
                continue
            if existing.get("description", "").strip() == description.strip():
                return True
        return False

    def analyze_evaluation_report(
        self,
        report_data: dict,
        project_id: str = "",
    ) -> list[TrainingProposal]:
        """
        Produce proposals from a single evaluation report.
        Returns proposals for: low metrics, probation-risk agents, systemic findings.
        Skips proposals whose description already exists in the backlog as applied/approved.
        """
        proposals: list[TrainingProposal] = []
        report_id = report_data.get("report_id", "unknown")
        pid = project_id or report_data.get("project_id", "unknown")

        # D6: load backlog once for deduplication check
        existing_backlog = self.load_backlog().get("proposals", [])

        # --- 1. Project metrics below threshold ---
        for m in report_data.get("project_metrics", []):
            score = float(m.get("score", 100))
            metric = m.get("metric", "unknown")
            # D2: skip not_applicable metrics (projects without live execution evidence)
            if m.get("mode") == "not_applicable":
                continue
            if score < LOW_THRESHOLD:
                ptype = self._metric_to_proposal_type(metric, score)
                description = (
                    f"Metric '{metric}' scored {score:.1f}/100 "
                    f"(below threshold {LOW_THRESHOLD}). "
                    f"Evidence: {m.get('evidence', 'none')}"
                )
                # D6 (AC6): skip if identical description already applied/approved
                if self._is_duplicate(description, existing_backlog):
                    continue
                proposals.append(TrainingProposal(
                    proposal_id=f"prop-{uuid.uuid4().hex[:8]}",
                    proposal_type=ptype,
                    priority=PRIORITY_SCORES[ptype],
                    target_agent="system",
                    target_artifact=self._metric_to_artifact(metric),
                    description=description,
                    recommended_change=self._recommend_for_metric(metric, score, m),
                    evidence=[report_id],
                    tradeoffs=self._tradeoffs_for_metric(metric),
                    minimum_evidence_met=True,   # 1 report = sufficient for single finding
                    systemic=False,
                    project_ids=[pid],
                ))

        # --- 2. Agents recommended for probation ---
        for agent_eval in report_data.get("agent_evaluations", []):
            if agent_eval.get("recommend_probation"):
                agent_id = agent_eval.get("agent_id", "unknown")
                overall = float(agent_eval.get("overall_score", 0))
                proposals.append(TrainingProposal(
                    proposal_id=f"prop-{uuid.uuid4().hex[:8]}",
                    proposal_type="boundary_violation",
                    priority=PRIORITY_SCORES["boundary_violation"],
                    target_agent=agent_id,
                    target_artifact=f"agents/{agent_id}.md",
                    description=(
                        f"Agent '{agent_id}' scored {overall:.1f}/100 — "
                        f"flagged for probation review. "
                        f"Issues: {'; '.join(agent_eval.get('issues', []))}"
                    ),
                    recommended_change=(
                        f"Review agent definition and governance boundaries for '{agent_id}'. "
                        "Consider restricting tool set or adding explicit escalation triggers."
                    ),
                    evidence=[report_id],
                    tradeoffs="Restricting the agent may reduce its effectiveness on tasks.",
                    minimum_evidence_met=True,
                    systemic=False,
                    project_ids=[pid],
                ))

        # --- 3. Systemic findings from report ---
        for finding in report_data.get("systemic_findings", []):
            finding_text = str(finding).strip()
            # Skip generic metric-label artifacts that aren't real findings (prop-008-003).
            if finding_text.lower() in NON_ACTIONABLE_FINDINGS:
                continue
            description = f"Systemic finding: {finding_text}"
            if self._is_duplicate(description, existing_backlog):
                continue
            proposals.append(TrainingProposal(
                proposal_id=f"prop-{uuid.uuid4().hex[:8]}",
                proposal_type="governance_failure",
                priority=PRIORITY_SCORES["governance_failure"],
                target_agent="system",
                target_artifact="policies/governance_policy.yaml",
                description=description,
                recommended_change=(
                    "Review governance policy and update to address systemic pattern."
                ),
                evidence=[report_id],
                tradeoffs="Policy changes may slow workflow if overly restrictive.",
                minimum_evidence_met=True,
                systemic=True,
                project_ids=[pid],
            ))

        # --- 4. Improvement areas from recommendations ---
        recs = report_data.get("recommendations", {}) or {}
        for area in recs.get("improvement_areas", []):
            description = f"Improvement area identified: {area}"
            if self._is_duplicate(description, existing_backlog):
                continue
            proposals.append(TrainingProposal(
                proposal_id=f"prop-{uuid.uuid4().hex[:8]}",
                proposal_type="efficiency_improvement",
                priority=PRIORITY_SCORES["efficiency_improvement"],
                target_agent="system",
                target_artifact="policies/",
                description=description,
                recommended_change=(
                    f"Investigate and address '{area}' in the relevant agent or policy."
                ),
                evidence=[report_id],
                tradeoffs="Investigation required before recommending specific change.",
                minimum_evidence_met=True,
                systemic=False,
                project_ids=[pid],
            ))

        return proposals

    # ------------------------------------------------------------------
    # Analysis: multiple reports (systemic pattern detection)
    # ------------------------------------------------------------------

    def analyze_multiple_reports(
        self,
        reports: list[dict],
        project_ids: Optional[list[str]] = None,
    ) -> list[TrainingProposal]:
        """
        Analyze N evaluation reports together, surfacing systemic patterns.
        Single-report proposals are also included but flagged accordingly.
        """
        if project_ids is None:
            project_ids = [r.get("project_id", "unknown") for r in reports]

        all_proposals: list[TrainingProposal] = []
        metric_scores: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
            # metric → [(score, report_id, project_id)]

        # Collect per-report proposals + gather metric data
        for i, report in enumerate(reports):
            pid = project_ids[i] if i < len(project_ids) else report.get("project_id", "unknown")
            per_report = self.analyze_evaluation_report(report, project_id=pid)
            all_proposals.extend(per_report)

            for m in report.get("project_metrics", []):
                score = float(m.get("score", 100))
                metric_scores[m.get("metric", "unknown")].append(
                    (score, report.get("report_id", "unknown"), pid)
                )

        # Detect systemic patterns: same metric low in 2+ reports
        systemic_added: set[str] = set()
        for metric, scores in metric_scores.items():
            low_instances = [(s, rid, pid) for s, rid, pid in scores if s < LOW_THRESHOLD]
            if len(low_instances) >= SYSTEMIC_MIN_REPORTS:
                key = f"systemic-{metric}"
                if key in systemic_added:
                    continue
                systemic_added.add(key)
                ptype = self._metric_to_proposal_type(metric, sum(s for s, _, _ in low_instances) / len(low_instances))
                evidence = [rid for _, rid, _ in low_instances]
                pids = [pid for _, _, pid in low_instances]
                all_proposals.append(TrainingProposal(
                    proposal_id=f"prop-systemic-{uuid.uuid4().hex[:8]}",
                    proposal_type=ptype,
                    priority=PRIORITY_SCORES[ptype] + 1,  # bump systemic higher
                    target_agent="system",
                    target_artifact=self._metric_to_artifact(metric),
                    description=(
                        f"SYSTEMIC: Metric '{metric}' scored below {LOW_THRESHOLD} "
                        f"in {len(low_instances)}/{len(reports)} reports. "
                        f"Average score: {sum(s for s, _, _ in low_instances) / len(low_instances):.1f}. "
                        f"Consistent pattern suggests structural issue."
                    ),
                    recommended_change=self._recommend_systemic(metric),
                    evidence=evidence,
                    tradeoffs=self._tradeoffs_for_metric(metric),
                    minimum_evidence_met=True,
                    systemic=True,
                    project_ids=pids,
                ))

        return all_proposals

    # ------------------------------------------------------------------
    # Prioritization
    # ------------------------------------------------------------------

    def prioritize(self, proposals: list[TrainingProposal]) -> list[TrainingProposal]:
        """Sort proposals: higher priority first, systemic before non-systemic, newer evidence."""
        return sorted(
            proposals,
            key=lambda p: (
                -p.priority,
                not p.systemic,              # systemic first within same priority
                not p.minimum_evidence_met,  # evidence-met first
            ),
        )

    # ------------------------------------------------------------------
    # Training brief (per project)
    # ------------------------------------------------------------------

    def produce_training_brief(
        self,
        project_id: str,
        proposals: list[TrainingProposal],
        project_dir: Path,
    ) -> Path:
        """Write training brief to projects/{pid}/training/training_brief.yaml."""
        brief_dir = project_dir / "training"
        brief_dir.mkdir(parents=True, exist_ok=True)

        prioritized = self.prioritize(proposals)
        brief = {
            "project_id": project_id,
            "trainer": "trainer_agent",
            "authority_level": "L0_advisory",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_proposals": len(proposals),
            "proposal_summary": {
                "by_type": _count_by(proposals, "proposal_type"),
                "by_priority": _count_by(proposals, "priority"),
                "systemic_count": sum(1 for p in proposals if p.systemic),
                "minimum_evidence_met_count": sum(1 for p in proposals if p.minimum_evidence_met),
            },
            "proposals": [_proposal_to_dict(p) for p in prioritized],
            "note": (
                "These are advisory proposals only. No changes will be applied "
                "until Master Orchestrator approves each proposal individually."
            ),
        }

        path = brief_dir / "training_brief.yaml"
        with open(path, "w") as f:
            yaml.dump(brief, f, default_flow_style=False, sort_keys=False)

        return path

    # ------------------------------------------------------------------
    # Backlog management
    # ------------------------------------------------------------------

    def load_backlog(self) -> dict:
        if not BACKLOG_FILE.exists():
            return {"proposals": [], "last_updated": None}
        with open(BACKLOG_FILE) as f:
            return yaml.safe_load(f) or {"proposals": [], "last_updated": None}

    def update_backlog(self, proposals: list[TrainingProposal]) -> None:
        """Append new proposals to the backlog. Deduplicates by proposal_id."""
        backlog = self.load_backlog()
        existing_ids = {p["proposal_id"] for p in backlog.get("proposals", [])}
        added = 0
        for p in proposals:
            if p.proposal_id not in existing_ids:
                backlog["proposals"].append(_proposal_to_dict(p))
                existing_ids.add(p.proposal_id)
                added += 1
                _log_training_event(
                    project_id=(p.project_ids[0] if p.project_ids else "__system__"),
                    agent_id="trainer_agent",
                    action_type="proposal_added",
                    intent=f"New proposal {p.proposal_id}: {p.description[:80]}",
                    payload={"proposal_id": p.proposal_id, "description": p.description[:120],
                             "priority": p.priority, "target_agent": p.target_agent,
                             "proposal_type": p.proposal_type},
                )
        backlog["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_backlog(backlog)
        return added

    def approve_proposal(self, proposal_id: str, authorized_by: str) -> bool:
        """Mark a proposal as approved. Only master_orchestrator may approve."""
        if authorized_by != "master_orchestrator":
            return False
        backlog = self.load_backlog()
        for p in backlog["proposals"]:
            if p["proposal_id"] == proposal_id:
                if p["status"] in ("pending",):
                    p["status"] = "approved"
                    p["approved_by"] = authorized_by
                    p["approved_at"] = datetime.now(timezone.utc).isoformat()
                    _save_backlog(backlog)
                    _log_training_event(
                        project_id=p.get("source_project", "__system__"),
                        agent_id=authorized_by,
                        action_type="proposal_approved",
                        intent=f"Approved {proposal_id}: {p.get('title', '')}",
                        payload={"proposal_id": proposal_id, "title": p.get("title", ""),
                                 "target_agent": p.get("target_agent", "")},
                    )
                    return True
        return False

    def reject_proposal(
        self,
        proposal_id: str,
        reason: str,
        authorized_by: str,
    ) -> bool:
        """Mark a proposal as rejected with a reason. Only master_orchestrator may reject."""
        if authorized_by != "master_orchestrator":
            return False
        backlog = self.load_backlog()
        for p in backlog["proposals"]:
            if p["proposal_id"] == proposal_id:
                if p["status"] in ("pending",):
                    p["status"] = "rejected"
                    p["rejection_reason"] = reason
                    p["rejected_by"] = authorized_by
                    p["rejected_at"] = datetime.now(timezone.utc).isoformat()
                    _save_backlog(backlog)
                    _log_training_event(
                        project_id=p.get("source_project", "__system__"),
                        agent_id=authorized_by,
                        action_type="proposal_rejected",
                        intent=f"Rejected {proposal_id}: {reason[:120]}",
                        payload={"proposal_id": proposal_id, "title": p.get("title", ""),
                                 "reason": reason},
                    )
                    return True
        return False

    def mark_applied(self, proposal_id: str, authorized_by: str) -> bool:
        """Mark an approved proposal as applied."""
        if authorized_by != "master_orchestrator":
            return False
        backlog = self.load_backlog()
        for p in backlog["proposals"]:
            if p["proposal_id"] == proposal_id and p["status"] == "approved":
                p["status"] = "applied"
                p["applied_at"] = datetime.now(timezone.utc).isoformat()
                _save_backlog(backlog)
                _log_training_event(
                    project_id=p.get("source_project", "__system__"),
                    agent_id=authorized_by,
                    action_type="proposal_applied",
                    intent=f"Applied {proposal_id}: {p.get('title', '')}",
                    payload={"proposal_id": proposal_id, "title": p.get("title", ""),
                             "target_agent": p.get("target_agent", ""),
                             "target_artifact": p.get("target_artifact", "")},
                )
                return True
        return False

    def get_pending(self) -> list[dict]:
        backlog = self.load_backlog()
        return [p for p in backlog.get("proposals", []) if p["status"] == "pending"]

    def get_by_status(self, status: str) -> list[dict]:
        backlog = self.load_backlog()
        return [p for p in backlog.get("proposals", []) if p["status"] == status]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _metric_to_proposal_type(metric: str, score: float) -> str:
        if metric in ("boundary_adherence",):
            return "boundary_violation"
        if metric in ("handoff_quality", "phase_efficiency"):
            return "governance_failure"
        if metric in ("documentation_completeness", "decision_quality"):
            return "efficiency_improvement"
        if metric in ("token_efficiency", "payload_density"):
            return "communication_waste"
        if metric in ("context_injection_efficiency", "consultation_overhead"):
            return "context_bloat"
        if score < 50.0:
            return "repeated_quality_issue"
        return "efficiency_improvement"

    @staticmethod
    def _metric_to_artifact(metric: str) -> str:
        mapping = {
            "boundary_adherence": "policies/governance_policy.yaml",
            "handoff_quality": "core/handoff_engine.py",
            "phase_efficiency": "agents/master_orchestrator.md",
            "documentation_completeness": "agents/scribe_agent.md",
            "decision_quality": "agents/scribe_agent.md",
            "goal_achievement": "agents/product_manager_agent.md",
            "acceptance_criteria_pass_rate": "agents/project_manager_agent.md",
            "scope_adherence": "agents/project_manager_agent.md",
            "task_completion_rate": "agents/project_manager_agent.md",
            "token_efficiency": "core/wire_protocol.py",
            "payload_density": "core/wire_protocol.py",
            "context_injection_efficiency": "core/prompt_assembler.py",
            "consultation_overhead": "core/consultation_engine.py",
        }
        return mapping.get(metric, "policies/")

    @staticmethod
    def _recommend_for_metric(metric: str, score: float, m: dict) -> str:
        recs = {
            "documentation_completeness": (
                "Review the Scribe Agent definition to ensure all required documents "
                "are produced in every phase. Consider adding checklist to handoff protocol."
            ),
            "decision_quality": (
                "Improve decision log richness. Add explicit rationale, alternatives considered, "
                "and traceability fields to every decision entry."
            ),
            "scope_adherence": (
                "Review task decomposition in Project Manager Agent. Investigate blocked/failed "
                "tasks and over-effort patterns. Add pre-phase scope review step."
            ),
            "acceptance_criteria_pass_rate": (
                "Strengthen acceptance criteria definition in Product Manager Agent. "
                "Ensure each AC is testable and has a clear pass/fail condition."
            ),
            "goal_achievement": (
                "Improve alignment between success criteria and task decomposition. "
                "Ensure task descriptions use keywords from success criteria."
            ),
            "phase_efficiency": (
                "Reduce excess handoffs per phase. Review Master Orchestrator's phase "
                "management to batch related actions into fewer handoff cycles."
            ),
            "handoff_quality": (
                "Improve first-acceptance rate of handoffs. Review handoff payload "
                "requirements and add validation before submission."
            ),
            "boundary_adherence": (
                "Agent violated governance boundaries. Review agent definition, restrict "
                "tool access, and add explicit forbidden-action checklist."
            ),
            "task_completion_rate": (
                "Agent did not complete assigned tasks. Review task assignment process "
                "and ensure agents only receive tasks within their capability profile."
            ),
            "token_efficiency": (
                "High token waste detected in handoff payloads. Adopt wire protocol "
                "compact encoding for all agent-to-agent messages."
            ),
            "payload_density": (
                "Handoff payloads carry too much redundant content. Strip empty fields, "
                "compress repeated context, and limit reasoning to 100 words."
            ),
            "context_injection_efficiency": (
                "State injection into prompts is over-sized. Review STATE_PROJECTIONS in "
                "prompt_assembler.py — tighten per-agent field lists."
            ),
            "consultation_overhead": (
                "Consultation rounds are consuming excessive tokens. Ensure responses use "
                "structured wire format with reasoning capped at 100 words."
            ),
        }
        return recs.get(metric, f"Investigate and address low score for metric '{metric}' (score: {score:.1f}).")

    @staticmethod
    def _recommend_systemic(metric: str) -> str:
        return (
            f"SYSTEMIC: Metric '{metric}' has been consistently low across multiple projects. "
            "Recommend a policy review, agent definition update, and addition to training checklist."
        )

    @staticmethod
    def _tradeoffs_for_metric(metric: str) -> str:
        tradeoffs = {
            "documentation_completeness": "More documentation requirements slow delivery pace.",
            "decision_quality": "Richer decision logging adds overhead per decision.",
            "scope_adherence": "Tighter scope control may require more planning time upfront.",
            "acceptance_criteria_pass_rate": "Stricter AC may increase rejection rate during review.",
            "phase_efficiency": "Fewer handoffs may mean less review and higher error risk.",
            "handoff_quality": "More handoff validation adds latency to each phase transition.",
            "boundary_adherence": "Restricting agent tools may limit its ability to handle edge cases.",
            "token_efficiency": "Wire encoding reduces human readability of raw payloads.",
            "payload_density": "Stripping context may require recipients to re-query state.",
            "context_injection_efficiency": "Tighter projections may miss edge-case fields agents need.",
            "consultation_overhead": "Capping reasoning may reduce nuance in high-stakes decisions.",
        }
        return tradeoffs.get(metric, "No known tradeoffs for this metric.")

    # ------------------------------------------------------------------
    # Communication proposals
    # ------------------------------------------------------------------

    def generate_communication_proposals(
        self,
        state: dict,
        project_id: str = "",
    ) -> list[TrainingProposal]:
        """
        Generate training proposals from communication metrics in shared state.
        Reads the `communication` section and 4 efficiency scores from MetricsEngine.
        """
        if MetricsEngine is None:
            return []
        _me = MetricsEngine()

        proposals: list[TrainingProposal] = []
        pid = project_id or state.get("core_identity", {}).get("project_id", "unknown")

        comm = state.get("communication", {})
        wf = state.get("workflow", {})
        handoff_history = wf.get("handoff_history") or []
        completed_phases = wf.get("completed_phases") or []
        phase_count = max(len(completed_phases), 1)

        consultation_responses = (
            state.get("consultation", {}).get("consultation_responses") or []
        )
        decisions_made = len(
            state.get("decisions", {}).get("decision_log") or []
        ) or 1

        metric_calls = [
            ("token_efficiency",             lambda: _me.score_token_efficiency(handoff_history, phase_count)),
            ("payload_density",              lambda: _me.score_payload_density(handoff_history)),
            ("context_injection_efficiency", lambda: _me.score_context_injection_efficiency([], comm.get("total_tokens_used", 0) or 1)),
            ("consultation_overhead",        lambda: _me.score_consultation_overhead(consultation_responses, decisions_made)),
        ]

        for metric_name, call_fn in metric_calls:
            try:
                result = call_fn()
            except Exception:
                continue

            score = result.score
            if score >= LOW_THRESHOLD:
                continue

            ptype = self._metric_to_proposal_type(metric_name, score)
            proposals.append(TrainingProposal(
                proposal_id=f"prop-comm-{uuid.uuid4().hex[:8]}",
                proposal_type=ptype,
                priority=PRIORITY_SCORES[ptype],
                target_agent="system",
                target_artifact=self._metric_to_artifact(metric_name),
                description=(
                    f"Communication metric '{metric_name}' scored {score:.1f}/100 "
                    f"(below threshold {LOW_THRESHOLD}). "
                    f"Breakdown: {result.breakdown}"
                ),
                recommended_change=self._recommend_for_metric(metric_name, score, {}),
                evidence=[f"comm-metrics-{pid}"],
                tradeoffs=self._tradeoffs_for_metric(metric_name),
                minimum_evidence_met=True,
                systemic=False,
                project_ids=[pid],
            ))

        wire_total = comm.get("wire_total_count", 0)
        wire_compliant = comm.get("wire_compliant_count", 0)
        compliance_rate = comm.get("wire_compliance_rate")

        if wire_total >= 5 and compliance_rate is not None and compliance_rate < 0.5:
            proposals.append(TrainingProposal(
                proposal_id=f"prop-wire-adopt-{uuid.uuid4().hex[:8]}",
                proposal_type="communication_waste",
                priority=PRIORITY_SCORES["communication_waste"],
                target_agent="system",
                target_artifact="core/wire_protocol.py",
                description=(
                    f"Wire protocol adoption is low: {wire_compliant}/{wire_total} "
                    f"handoffs compliant ({compliance_rate:.0%}). "
                    "Agents are using legacy prose payloads."
                ),
                recommended_change=(
                    "Update all agent templates to use wire protocol encode() for "
                    "handoff payloads. Prioritize high-frequency agents."
                ),
                evidence=[f"wire-compliance-{pid}"],
                tradeoffs="Wire encoding reduces human readability of raw handoff data.",
                minimum_evidence_met=wire_total >= 10,
                systemic=False,
                project_ids=[pid],
            ))

        return proposals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proposal_to_dict(p: TrainingProposal) -> dict:
    return {
        "proposal_id": p.proposal_id,
        "proposal_type": p.proposal_type,
        "priority": p.priority,
        "target_agent": p.target_agent,
        "target_artifact": p.target_artifact,
        "description": p.description,
        "recommended_change": p.recommended_change,
        "evidence": p.evidence,
        "tradeoffs": p.tradeoffs,
        "minimum_evidence_met": p.minimum_evidence_met,
        "systemic": p.systemic,
        "status": p.status,
        "rejection_reason": p.rejection_reason,
        "original_proposal_id": p.original_proposal_id,
        "created_at": p.created_at,
        "project_ids": p.project_ids,
    }


def _count_by(proposals: list[TrainingProposal], attr: str) -> dict:
    counts: dict = defaultdict(int)
    for p in proposals:
        counts[getattr(p, attr)] += 1
    return dict(counts)


def _save_backlog(backlog: dict) -> None:
    BACKLOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKLOG_FILE, "w") as f:
        yaml.dump(backlog, f, default_flow_style=False, sort_keys=False)


def _load_exemplary_refs() -> dict:
    if not EXEMPLARY_REFS_FILE.exists():
        return {"exemplary_references": []}
    with open(EXEMPLARY_REFS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"exemplary_references": []}


def _save_exemplary_refs(data: dict) -> None:
    EXEMPLARY_REFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EXEMPLARY_REFS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def record_exemplary_agent(
    agent_id: str,
    project_id: str,
    score: float,
    strengths: list[str],
    context: str = "",
) -> str:
    """
    Add an exemplary agent performance record to roster/exemplary_references.yaml.
    Returns the ref_id. Idempotent: skips if identical (agent_id + project_id) exists.
    """
    data = _load_exemplary_refs()
    refs = data["exemplary_references"]

    existing_ids = {(r["agent_id"], r["project_id"]) for r in refs}
    if (agent_id, project_id) in existing_ids:
        # return existing ref_id
        for r in refs:
            if r["agent_id"] == agent_id and r["project_id"] == project_id:
                return r["ref_id"]

    ref_id = f"ref-{agent_id}-{project_id}"
    refs.append({
        "ref_id": ref_id,
        "agent_id": agent_id,
        "project_id": project_id,
        "score": round(score, 2),
        "strengths": strengths,
        "context": context,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_exemplary_refs(data)
    return ref_id


def list_exemplary_agents() -> list[dict]:
    """Return all exemplary agent reference records."""
    return _load_exemplary_refs().get("exemplary_references", [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_analyze(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="training_engine analyze")
    p.add_argument("--project-id", required=True)
    p.add_argument("--projects-root", default="mas/projects")
    ns = p.parse_args(args)

    project_dir = Path(ns.projects_root) / ns.project_id
    report_path = project_dir / "evaluation" / "evaluation_report.yaml"

    if not report_path.exists():
        print(f"No evaluation report found at {report_path}")
        sys.exit(1)

    with open(report_path) as f:
        report_data = yaml.safe_load(f)

    engine = TrainingEngine()
    proposals = engine.analyze_evaluation_report(report_data, project_id=ns.project_id)
    brief_path = engine.produce_training_brief(ns.project_id, proposals, project_dir)
    added = engine.update_backlog(proposals)

    print(f"Analysis complete: {len(proposals)} proposal(s) produced.")
    print(f"Training brief: {brief_path}")
    print(f"Backlog: {added} new proposal(s) added.")
    for p in engine.prioritize(proposals):
        prefix = "[SYSTEMIC]" if p.systemic else ""
        print(f"  P{p.priority} {prefix} [{p.proposal_type}] {p.description[:80]}...")


def _cli_backlog(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="training_engine backlog")
    p.add_argument("--status", default=None, choices=list(PROPOSAL_STATUSES))
    ns = p.parse_args(args)

    engine = TrainingEngine()
    backlog = engine.load_backlog()
    proposals = backlog.get("proposals", [])

    if ns.status:
        proposals = [p for p in proposals if p["status"] == ns.status]

    if not proposals:
        print("No proposals in backlog" + (f" with status '{ns.status}'" if ns.status else "") + ".")
        return

    print(f"Training backlog ({len(proposals)} proposal(s)):\n")
    for p in proposals:
        prefix = "[SYSTEMIC]" if p.get("systemic") else ""
        print(
            f"  [{p['proposal_id']}] P{p['priority']} {prefix} "
            f"[{p['proposal_type']}] [{p['status']}] "
            f"{p['description'][:70]}..."
        )


def _cli_approve(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="training_engine approve")
    p.add_argument("--proposal-id", required=True)
    p.add_argument("--authorized-by", required=True)
    ns = p.parse_args(args)

    engine = TrainingEngine()
    ok = engine.approve_proposal(ns.proposal_id, ns.authorized_by)
    if ok:
        print(f"[ok] Proposal '{ns.proposal_id}' approved by {ns.authorized_by}.")
    else:
        print(f"[fail] Could not approve '{ns.proposal_id}'. Check proposal_id, status, and authorization.")
        sys.exit(1)


def _cli_reject(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="training_engine reject")
    p.add_argument("--proposal-id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--authorized-by", required=True)
    ns = p.parse_args(args)

    engine = TrainingEngine()
    ok = engine.reject_proposal(ns.proposal_id, ns.reason, ns.authorized_by)
    if ok:
        print(f"[ok] Proposal '{ns.proposal_id}' rejected: {ns.reason}")
    else:
        print(f"[fail] Could not reject '{ns.proposal_id}'. Check proposal_id, status, and authorization.")
        sys.exit(1)


def _cli_exemplary(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="training_engine exemplary")
    sub = p.add_subparsers(dest="sub", required=True)

    add = sub.add_parser("add", help="Index an exemplary agent into the training corpus")
    add.add_argument("--agent-id", required=True)
    add.add_argument("--project-id", required=True)
    add.add_argument("--score", type=float, required=True)
    add.add_argument("--strengths", required=True,
                     help="Comma-separated list of observed strengths")
    add.add_argument("--context", default="")

    sub.add_parser("list", help="List all exemplary agent references")

    ns = p.parse_args(args)

    if ns.sub == "add":
        strengths = [s.strip() for s in ns.strengths.split(",") if s.strip()]
        ref_id = record_exemplary_agent(
            agent_id=ns.agent_id,
            project_id=ns.project_id,
            score=ns.score,
            strengths=strengths,
            context=ns.context,
        )
        print(f"[ok] Indexed exemplary agent: {ref_id}")

    elif ns.sub == "list":
        refs = list_exemplary_agents()
        if not refs:
            print("[none] No exemplary references recorded yet.")
            return
        print(f"Exemplary agent references ({len(refs)}):\n")
        for r in refs:
            print(
                f"  {r['ref_id']:50} score={r['score']:.1f}  "
                f"project={r['project_id']}"
            )
            print(f"    strengths: {', '.join(r.get('strengths', []))}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python core/training_engine.py <analyze|backlog|approve|reject|exemplary> [options]")
        sys.exit(1)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    dispatch = {
        "analyze": _cli_analyze,
        "backlog": _cli_backlog,
        "approve": _cli_approve,
        "reject": _cli_reject,
        "exemplary": _cli_exemplary,
    }

    if cmd not in dispatch:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    dispatch[cmd](rest)
