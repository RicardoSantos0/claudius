"""
Metrics Engine
Pure scoring functions for project and agent evaluation.
All functions take structured data (dicts/lists from shared state or
task board) and return numeric scores or structured results.

Minimum metrics for v1 (from expert answers):
  - goal_achievement              (0-100)
  - acceptance_criteria_pass_rate (0-100)
  - handoff_acceptance_rate       (0-100)
  - documentation_completeness    (0-100)
  - boundary_violation_count      (integer)

Additional v1 metrics:
  - scope_adherence               (0-100)
  - task_completion_rate          (0-100)
  - decision_quality              (0-100)
  - phase_efficiency              (dict of phase→ratio)

Usage as library:
    from core.engine.metrics_engine import MetricsEngine
    engine = MetricsEngine()
    score = engine.score_goal_achievement(success_criteria, task_outcomes)

Usage as CLI:
    uv run python mas/core/engine/metrics_engine.py score-project --project-id proj-001
    uv run python mas/core/engine/metrics_engine.py score-agent  --project-id proj-001 --agent-id hr_agent
    uv run python mas/core/engine/metrics_engine.py report       --project-id proj-001 [--save]
"""

import sys
import json
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from core.utils.token_counter import TokenCounter

_token_counter = TokenCounter()

ROOT = Path(__file__).parent.parent.parent.resolve()

try:
    from core.db import append_event as _append_event
except Exception:
    _append_event = None  # type: ignore

logger = logging.getLogger(__name__)

# --- evaluation_policy.yaml is the single source of truth for weights/thresholds ---
_EVAL_POLICY_PATH = ROOT / "policies" / "evaluation_policy.yaml"
_POLICY_CACHE: dict | None = None


def _load_eval_policy() -> dict:
    """Load (and cache) evaluation_policy.yaml → evaluation_policy block. {} on failure."""
    global _POLICY_CACHE
    if _POLICY_CACHE is None:
        try:
            with open(_EVAL_POLICY_PATH, encoding="utf-8") as f:
                _POLICY_CACHE = (yaml.safe_load(f) or {}).get("evaluation_policy", {}) or {}
        except Exception:
            _POLICY_CACHE = {}
    return _POLICY_CACHE


def _project_metric_weights() -> dict:
    return _load_eval_policy().get("project_metric_weights", {}) or {}


def _agent_metric_weights() -> dict:
    return _load_eval_policy().get("agent_evaluation", {}).get("metric_weights", {}) or {}


# Thresholds: policy is source of truth; module constants are the documented fallback.
_AGENT_EVAL = _load_eval_policy().get("agent_evaluation", {})
EXEMPLARY_THRESHOLD = float(_AGENT_EVAL.get("exemplary_threshold", 90.0))  # flag exemplary
PROBATION_THRESHOLD = float(_AGENT_EVAL.get("probation_threshold", 60.0))  # recommend probation


def _weighted_average(metric_results: list, weights: dict) -> float:
    """Weighted mean of applicable metric scores. Metrics absent from `weights`
    default to weight 1.0. not_applicable metrics are excluded. Equal-weight
    behaviour is preserved when no weights are configured."""
    applicable = [m for m in metric_results if m.mode != "not_applicable"]
    if not applicable:
        return 0.0
    total_w = 0.0
    acc = 0.0
    for m in applicable:
        w = float(weights.get(m.metric, 1.0))
        acc += m.score * w
        total_w += w
    return acc / total_w if total_w else 0.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    metric: str
    score: float              # 0-100; -1.0 when mode='not_applicable'
    evidence: str
    findings: str
    exemplary: bool = False   # True if score > EXEMPLARY_THRESHOLD
    breakdown: dict = field(default_factory=dict)  # optional detailed data
    mode: str = "live"        # 'live' | 'not_applicable' (excluded from average)


@dataclass
class AgentEvaluation:
    agent_id: str
    metrics: list             # list[MetricResult]
    overall_score: float
    strengths: list
    issues: list
    recommendations: list
    exemplary: bool = False   # True if overall_score > EXEMPLARY_THRESHOLD
    recommend_probation: bool = False

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "metrics": [
                {
                    "metric": m.metric,
                    "score": round(m.score, 2),
                    "evidence": m.evidence,
                    "findings": m.findings,
                    "exemplary": m.exemplary,
                }
                for m in self.metrics
            ],
            "overall_score": round(self.overall_score, 2),
            "strengths": self.strengths,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "exemplary": self.exemplary,
            "recommend_probation": self.recommend_probation,
        }


@dataclass
class EvaluationReport:
    report_id: str
    project_id: str
    timestamp: str
    evaluator: str
    project_metrics: list         # list[MetricResult]
    agent_evaluations: list       # list[AgentEvaluation]
    systemic_findings: dict
    recommendations: dict
    overall_project_score: float
    # Advisory communication-efficiency dimension (policy weight 0.5; never affects
    # the core score or governance — surfaces token/payload/context efficiency).
    communication_efficiency: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "project_id": self.project_id,
            "timestamp": self.timestamp,
            "evaluator": self.evaluator,
            "overall_project_score": round(self.overall_project_score, 2),
            "project_metrics": [
                {
                    "metric": m.metric,
                    "score": round(m.score, 2),
                    "evidence": m.evidence,
                    "findings": m.findings,
                    "exemplary": m.exemplary,
                    "mode": m.mode,
                }
                for m in self.project_metrics
            ],
            "agent_evaluations": [a.to_dict() for a in self.agent_evaluations],
            "systemic_findings": self.systemic_findings,
            "recommendations": self.recommendations,
            "communication_efficiency": self.communication_efficiency,
        }


# ---------------------------------------------------------------------------
# MetricsEngine
# ---------------------------------------------------------------------------

class MetricsEngine:
    """
    Computes evaluation metrics from project and agent data.
    All methods are pure (no I/O) — they take data, return scores.
    """

    # ------------------------------------------------------------------
    # Project-level metrics
    # ------------------------------------------------------------------

    def score_goal_achievement(
        self,
        success_criteria: list,
        completed_task_descriptions: list,
        task_expected_outputs: list[str] | None = None,
    ) -> MetricResult:
        """
        Score 0-100 based on how many success criteria are evidenced
        in completed tasks.

        Heuristic: for each success criterion, check if any completed
        task description OR expected_output mentions keywords from that criterion.
        Score = matched / total * 100.
        If no success criteria defined: score = 50 (unknown).
        """
        if not success_criteria:
            return MetricResult(
                metric="goal_achievement",
                score=50.0,
                evidence="No success criteria defined in specification",
                findings="Cannot assess goal achievement without success criteria",
            )

        # Combine descriptions and expected outputs into a single searchable corpus
        all_searchable = list(completed_task_descriptions)
        if task_expected_outputs:
            all_searchable.extend(task_expected_outputs)

        matched = 0
        evidence_parts = []

        for criterion in success_criteria:
            criterion_lower = criterion.lower()
            keywords = [w for w in criterion_lower.split() if len(w) > 4]
            if not keywords:
                keywords = [criterion_lower]

            # Check if any completed task or expected output covers this criterion
            covered = any(
                any(kw in text.lower() for kw in keywords)
                for text in all_searchable
            )
            if covered:
                matched += 1
                evidence_parts.append(f"✓ '{criterion[:60]}'")
            else:
                evidence_parts.append(f"✗ '{criterion[:60]}' — no matching task")

        score = matched / len(success_criteria) * 100.0

        return MetricResult(
            metric="goal_achievement",
            score=score,
            evidence=f"{matched}/{len(success_criteria)} criteria evidenced in completed tasks",
            findings="; ".join(evidence_parts),
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_acceptance_criteria_pass_rate(
        self,
        total_criteria: int,
        passed_criteria: int,
    ) -> MetricResult:
        if total_criteria == 0:
            return MetricResult(
                metric="acceptance_criteria_pass_rate",
                score=50.0,
                evidence="No acceptance criteria defined",
                findings="Cannot assess pass rate without acceptance criteria",
            )

        score = passed_criteria / total_criteria * 100.0
        return MetricResult(
            metric="acceptance_criteria_pass_rate",
            score=score,
            evidence=f"{passed_criteria}/{total_criteria} acceptance criteria passed",
            findings=(
                f"Pass rate: {score:.1f}%. "
                + ("All criteria met." if score == 100.0
                   else f"{total_criteria - passed_criteria} criteria not met.")
            ),
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_scope_adherence(
        self,
        planned_task_count: int,
        completed_task_count: int,
        blocked_task_count: int,
        failed_task_count: int,
        over_effort_task_count: int,
    ) -> MetricResult:
        if planned_task_count == 0:
            return MetricResult(
                metric="scope_adherence",
                score=50.0,
                evidence="No tasks planned",
                findings="Cannot assess scope adherence without tasks",
            )

        completion_rate = completed_task_count / planned_task_count * 100.0
        deductions = (blocked_task_count + failed_task_count) * 10
        deductions += over_effort_task_count * 5
        score = max(0.0, completion_rate - deductions)

        findings_parts = [f"Task completion rate: {completion_rate:.1f}%"]
        if blocked_task_count:
            findings_parts.append(f"{blocked_task_count} blocked (−{blocked_task_count * 10}pts)")
        if failed_task_count:
            findings_parts.append(f"{failed_task_count} failed (−{failed_task_count * 10}pts)")
        if over_effort_task_count:
            findings_parts.append(f"{over_effort_task_count} over-effort (−{over_effort_task_count * 5}pts)")

        return MetricResult(
            metric="scope_adherence",
            score=score,
            evidence=f"{completed_task_count}/{planned_task_count} tasks completed",
            findings="; ".join(findings_parts),
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_documentation_completeness(self, project_dir: Path,
                                         shared_state: dict | None = None) -> MetricResult:
        # ip-drift-002: lite-mode projects (intake → execution → closed) never run the
        # specification/planning phases, so the planning artifacts below cannot exist.
        # Scoring them as "missing" unfairly floors the metric to ~0. In lite mode, only
        # require the artifacts that lite projects actually produce; planning artifacts
        # become recommended (credited if present, not penalized if absent).
        mode = "standard"
        if shared_state:
            mode = (shared_state.get("workflow", {}) or {}).get("mode", "standard")
        if mode == "lite":
            required = [
                ("intake/brief.md", "Project brief"),
            ]
        else:
            required = [
                ("intake/clarified_spec.yaml", "Clarified specification"),
                ("planning/product_plan.yaml", "Product plan"),
                # ip-skill-001: the orchestrator phase gate writes planning/execution_plan.yaml;
                # accept it there or at the legacy execution/ path so a correctly-placed plan is
                # not scored as missing.
                (("planning/execution_plan.yaml", "execution/execution_plan.yaml"), "Execution plan"),
            ]
        # ip-005: credit post-planning artifacts (+5 pts each, capped at 20 total)
        recommended = [
            ("evaluation/evaluation_report.yaml", "Evaluation report"),
            ("execution/execution_phase_summary.yaml", "Execution phase summary"),
            ("evaluation/project_evaluation.yaml", "Project evaluation"),
            ("project_summary.yaml", "Project summary"),
            ("lessons_learned.yaml", "Lessons learned"),
        ]

        required_present = 0
        recommended_present = 0
        evidence_parts = []

        # project_dir may not exist for DB-backed projects that store state in SQLite only
        if not project_dir.exists():
            return MetricResult(
                metric="documentation_completeness",
                score=50.0,
                evidence="Project directory not found on filesystem (DB-backed project)",
                findings="Cannot assess filesystem documentation for DB-backed projects. Score defaulted to 50.",
            )

        for rel_path, label in required:
            candidates = (rel_path,) if isinstance(rel_path, str) else tuple(rel_path)
            exists = any((project_dir / p).exists() for p in candidates)
            if exists:
                required_present += 1
                evidence_parts.append(f"+ {label}")
            else:
                evidence_parts.append(f"- {label} (required)")

        for rel_path, label in recommended:
            exists = (project_dir / rel_path).exists()
            if exists:
                recommended_present += 1
                evidence_parts.append(f"+ {label} (recommended)")

        req_score = required_present / len(required) * 80.0 if required else 80.0
        # +5 pts per recommended doc, capped at 20
        rec_score = min(recommended_present * 5.0, 20.0)
        score = req_score + rec_score

        return MetricResult(
            metric="documentation_completeness",
            score=score,
            evidence=f"{required_present}/{len(required)} required docs present",
            findings="; ".join(evidence_parts),
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_phase_efficiency(
        self,
        handoff_history: list,
    ) -> MetricResult:
        """
        Measures phase efficiency by penalizing REWORK, not delegation volume.

        Per phase, the handoff budget is ``max(2, distinct task descriptions)``:
        a phase that delegates N distinct tasks is expected to need ~N handoffs
        (the Claude Code manual one-handoff-per-task pattern), or the canonical 2
        (one out, one back) when task descriptions are absent. Only handoffs
        beyond that budget — repeated/rework handoffs for the same task — count
        as 'extra' at -5 pts each. Min: 0.

        This avoids penalizing legitimate per-task delegation volume in manual
        mode (imp-001), while still flagging churn/rework in any mode.
        """
        if not handoff_history:
            return MetricResult(
                metric="phase_efficiency",
                score=100.0,
                evidence="No handoffs",
                findings="No handoffs to assess.",
            )

        phases: dict[str, list] = {}
        for h in handoff_history:
            phases.setdefault(h.get("phase", "unknown"), []).append(h)

        total_extra = 0
        findings_parts = []
        for phase, handoffs in phases.items():
            distinct = len({
                (h.get("task_description") or "").strip().lower() for h in handoffs
            })
            budget = max(2, distinct)
            extra = max(0, len(handoffs) - budget)
            total_extra += extra
            if extra > 0:
                findings_parts.append(
                    f"{phase}: {len(handoffs)} handoffs, {distinct} distinct "
                    f"task(s) (+{extra} rework)"
                )

        score = max(0.0, 100.0 - total_extra * 5.0)

        return MetricResult(
            metric="phase_efficiency",
            score=round(score, 1),
            evidence=f"phases={len(phases)}, rework_handoffs={total_extra}",
            findings="; ".join(findings_parts) if findings_parts else "All phases efficient.",
        )

    def score_decision_quality(
        self,
        decision_log: list,
    ) -> MetricResult:
        """
        Quality of recorded decisions.
        Base: 50 if no decisions.
        Per decision: +2 for documented, +20 for rationale, +20 for alternatives, +20 for related_to.
        Score = 50 + average(per_decision_scores) / 2.
        """
        if not decision_log:
            return MetricResult(
                metric="decision_quality",
                score=50.0,
                evidence="No decisions recorded",
                findings="Cannot assess without decision log.",
            )

        total_pts = 0
        for d in decision_log:
            pts = 0
            if d.get("description") or d.get("decision_id"):
                pts += 2
            if d.get("rationale"):
                pts += 20
            if d.get("alternatives_considered"):
                pts += 20
            if d.get("related_to"):
                pts += 20
            total_pts += pts

        avg = total_pts / len(decision_log)
        score = min(100.0, 50.0 + avg / 2.0)

        return MetricResult(
            metric="decision_quality",
            score=round(score, 1),
            evidence=f"{len(decision_log)} decisions; avg quality={avg:.1f}",
            findings=f"Avg decision quality score: {avg:.1f}/62. Target: >40.",
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    # score_global_graph_contribution was retired (P8, proj-YYYYMMDD-NNN): the
    # graph_memory subsystem was removed in proj-008 and cross-project memory now
    # uses the SQL-backed path (episodic.db + agent_graph tables). The metric was
    # orphaned (never invoked) and is gone — no graph-named metric remains.

    def _collect_outcome_evidence(self, shared_state: dict) -> list[str]:
        """Return explicit verification/performance evidence for project outcomes."""
        evidence: list[str] = []

        capability = shared_state.get("capability", {})
        evaluation = shared_state.get("evaluation", {})

        for item in capability.get("verification_results", []) or []:
            if isinstance(item, dict):
                status = item.get("status") or item.get("result") or "recorded"
                target = item.get("target") or item.get("artifact") or item.get("name") or "verification"
                evidence.append(f"verification:{target}:{status}")
            elif item:
                evidence.append(f"verification:{item}")

        for item in evaluation.get("performance_metrics", []) or []:
            if isinstance(item, dict):
                metric = item.get("metric") or item.get("name") or "metric"
                score = item.get("score")
                if score is None:
                    evidence.append(f"performance:{metric}")
                else:
                    evidence.append(f"performance:{metric}:{score}")
            elif item:
                evidence.append(f"performance:{item}")

        # imp-005: acceptance criteria recorded as met *with evidence* are explicit
        # outcome evidence. This is the documented shape (mas/CLAUDE.md):
        #   sm.append(..., "acceptance_criteria", {"criterion": ..., "met": True, "evidence": ...})
        # An entry without a non-empty `evidence` does NOT count (forces real evidence).
        project_definition = shared_state.get("project_definition", {})
        for ac in project_definition.get("acceptance_criteria", []) or []:
            if not isinstance(ac, dict) or ac.get("met") is not True:
                continue
            ev = str(ac.get("evidence") or "").strip()
            if ev:
                crit = str(ac.get("criterion") or ac.get("description") or "acceptance")[:60]
                evidence.append(f"acceptance:{crit}:{ev[:80]}")

        return evidence

    def _evidence_required_metric(
        self,
        metric: str,
        evidence_sources: list[str],
        reason: str,
    ) -> MetricResult:
        """Return a not_applicable metric when outcome evidence is missing."""
        evidence = "; ".join(evidence_sources[:5]) if evidence_sources else "No explicit outcome evidence recorded"
        return MetricResult(
            metric=metric,
            score=0.0,
            evidence=evidence,
            findings=reason,
            mode="not_applicable",
        )


    # ------------------------------------------------------------------
    # Agent-level metrics
    # ------------------------------------------------------------------

    def score_task_completion_rate(
        self,
        agent_id: str,
        all_tasks: list,
    ) -> MetricResult:
        """For a given agent: completed / assigned * 100."""
        assigned = [t for t in all_tasks if t.get("assigned_to") == agent_id]
        if not assigned:
            return MetricResult(
                metric="task_completion_rate",
                score=100.0,
                evidence=f"No tasks assigned to {agent_id}",
                findings="Agent was not assigned any tasks in this project",
            )

        completed = [t for t in assigned if t.get("status") == "completed"]
        score = len(completed) / len(assigned) * 100.0

        return MetricResult(
            metric="task_completion_rate",
            score=score,
            evidence=f"{len(completed)}/{len(assigned)} assigned tasks completed",
            findings=(
                f"Completion rate: {score:.1f}%. "
                + (f"{len(assigned) - len(completed)} incomplete tasks." if len(completed) < len(assigned) else "All tasks completed.")
            ),
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_handoff_quality(
        self,
        agent_id: str,
        handoff_history: list,
    ) -> MetricResult:
        """First-acceptance rate for handoffs FROM this agent."""
        outgoing = [h for h in handoff_history if h.get("from_agent") == agent_id]
        if not outgoing:
            return MetricResult(
                metric="handoff_quality",
                score=100.0,
                evidence=f"No outgoing handoffs from {agent_id}",
                findings="Agent produced no handoffs in this project",
            )

        # Real handoffs use nested acceptance.status; test fixtures may use flat status
        accepted = [
            h for h in outgoing
            if h.get("acceptance", {}).get("status") == "accepted"
            or h.get("status") == "accepted"
        ]
        score = len(accepted) / len(outgoing) * 100.0

        return MetricResult(
            metric="handoff_quality",
            score=score,
            evidence=f"{len(accepted)}/{len(outgoing)} outgoing handoffs accepted",
            findings=f"First-acceptance rate: {score:.1f}%",
            exemplary=score > EXEMPLARY_THRESHOLD,
        )

    def score_boundary_adherence(
        self,
        agent_id: str,
        governance_violations: list,
    ) -> MetricResult:
        """0 violations = 100. Each violation = -20 pts. Minimum: 0."""
        agent_violations = [v for v in governance_violations if v.get("agent_id") == agent_id]
        count = len(agent_violations)
        score = max(0.0, 100.0 - count * 20.0)

        findings = (
            f"{count} boundary violation(s) recorded." if count > 0 else "No boundary violations."
        )

        return MetricResult(
            metric="boundary_adherence",
            score=score,
            evidence=f"{count} violations for {agent_id}",
            findings=findings,
            exemplary=(count == 0 and score > EXEMPLARY_THRESHOLD),
        )

    # ------------------------------------------------------------------
    # Communication efficiency metrics
    # ------------------------------------------------------------------

    def score_token_efficiency(
        self,
        handoff_history: list,
        phase_count: int,
    ) -> MetricResult:
        """Tokens per phase. Lower is better. 100 if <500/phase, 0 at 5000/phase."""
        if not handoff_history or phase_count == 0:
            return MetricResult(
                metric="token_efficiency",
                score=0.0,
                evidence="No handoff history or phases",
                findings="Cannot compute — no data.",
            )

        tokens_by_agent: dict[str, int] = {}
        tokens_by_phase: dict[str, int] = {}
        total = 0

        for h in handoff_history:
            tok = h.get("token_usage") or h.get("tok")
            if isinstance(tok, dict):
                t = tok.get("total_tokens", 0) or 0
            elif isinstance(tok, list) and len(tok) >= 3:
                t = tok[2]
            else:
                payload = h.get("payload") or h.get("p") or {}
                t = _token_counter.count_dict(payload)

            agent = h.get("from_agent") or h.get("from", "unknown")
            phase = h.get("phase") or h.get("ph", "unknown")
            tokens_by_agent[agent] = tokens_by_agent.get(agent, 0) + t
            tokens_by_phase[phase] = tokens_by_phase.get(phase, 0) + t
            total += t

        avg_per_phase = total / phase_count
        score = max(0.0, min(100.0, 100.0 - (avg_per_phase - 500) / 45.0))

        findings = (
            f"Total tokens: {total}. Avg per phase: {avg_per_phase:.0f}. "
            f"Top consumer: {max(tokens_by_agent, key=tokens_by_agent.get, default='none')}."
        )

        return MetricResult(
            metric="token_efficiency",
            score=round(score, 1),
            evidence=f"total={total}, phases={phase_count}, avg_per_phase={avg_per_phase:.0f}",
            findings=findings,
            breakdown={
                "total_tokens": total,
                "avg_per_phase": round(avg_per_phase, 1),
                "by_agent": tokens_by_agent,
                "by_phase": tokens_by_phase,
            },
        )

    def score_payload_density(
        self,
        handoff_history: list,
    ) -> MetricResult:
        """Ratio of structured fields to prose in payloads."""
        if not handoff_history:
            return MetricResult(
                metric="payload_density",
                score=0.0,
                evidence="No handoff history",
                findings="Cannot compute — no data.",
            )

        structured = 0
        prose = 0

        for h in handoff_history:
            payload = h.get("payload") or h.get("p") or {}
            summary = payload.get("summary") or payload.get("s", "")
            if isinstance(summary, str) and (len(summary) < 30 and " " not in summary.strip()):
                structured += 1
            else:
                prose += 1

        total = structured + prose
        rate = structured / total if total > 0 else 0.0
        score = rate * 100.0

        return MetricResult(
            metric="payload_density",
            score=round(score, 1),
            evidence=f"structured={structured}, prose={prose}, total={total}",
            findings=(
                f"{rate:.0%} wire format compliance ({structured}/{total} payloads). "
                + ("Target: >90%." if rate < 0.9 else "Target met.")
            ),
        )

    def score_context_injection_efficiency(
        self,
        prompt_token_counts: list,
        total_prompt_tokens: int,
    ) -> MetricResult:
        """Injected context tokens vs total. 100 if <20%, 0 if >80%."""
        if total_prompt_tokens == 0 or not prompt_token_counts:
            return MetricResult(
                metric="context_injection_efficiency",
                score=0.0,
                evidence="No prompt token data",
                findings="Cannot compute — no data.",
            )

        context_tokens = sum(prompt_token_counts)
        ratio = context_tokens / total_prompt_tokens
        score = max(0.0, min(100.0, 100.0 - (ratio - 0.2) / 0.006))

        return MetricResult(
            metric="context_injection_efficiency",
            score=round(score, 1),
            evidence=f"context={context_tokens}, total={total_prompt_tokens}, ratio={ratio:.2f}",
            findings=(
                f"Context injection is {ratio:.0%} of total prompt tokens. "
                + ("Within target (<20%)." if ratio < 0.2 else f"Above target — reduce by {context_tokens - int(total_prompt_tokens * 0.2)} tokens.")
            ),
        )

    def score_consultation_overhead(
        self,
        consultation_data: list,
        decisions_made: int,
    ) -> MetricResult:
        """Consultation tokens per decision. 100 if <200/decision, 0 at 3000."""
        if not consultation_data or decisions_made == 0:
            return MetricResult(
                metric="consultation_overhead",
                score=0.0,
                evidence="No consultation data or decisions",
                findings="Cannot compute — no data.",
            )

        total = sum(
            _token_counter.count(r.get("response_text", ""))
            for r in consultation_data
            if isinstance(r, dict)
        )
        avg = total / decisions_made
        score = max(0.0, min(100.0, 100.0 - (avg - 200) / 28.0))

        return MetricResult(
            metric="consultation_overhead",
            score=round(score, 1),
            evidence=f"total_consultation_tokens={total}, decisions={decisions_made}, avg={avg:.0f}",
            findings=(
                f"Avg {avg:.0f} consultation tokens per decision. "
                + ("Within target." if avg < 200 else "Above target — enforce wire format for consultants.")
            ),
        )

    def score_governance_compliance(
        self,
        governance_violations: list,
    ) -> MetricResult:
        """
        Measures adherence to MAS governance rules.
        0 violations = 100. Each violation = -7.5 pts. Minimum: 0.
        """
        count = len(governance_violations) if governance_violations else 0
        score = max(0.0, 100.0 - count * 7.5)

        if count == 0:
            findings = "No governance violations."
        else:
            by_agent: dict[str, int] = {}
            for v in governance_violations:
                a = v.get("agent_id", "unknown") if isinstance(v, dict) else "unknown"
                by_agent[a] = by_agent.get(a, 0) + 1
            parts = [f"{a}: {n}" for a, n in sorted(by_agent.items())]
            findings = f"{count} violation(s) — {', '.join(parts)}"

        return MetricResult(
            metric="governance_compliance",
            score=round(score, 1),
            evidence=f"{count} governance_violations recorded",
            findings=findings,
            exemplary=(count == 0),
        )

    def score_record_integrity(
        self,
        handoff_history: list,
    ) -> MetricResult:
        """
        Measures governance trail hygiene. Retroactive handoffs lower the score.
        Score = 100 - (retroactive_ratio * 200), capped [0, 100].
        """
        total = len(handoff_history)
        if total == 0:
            return MetricResult(
                metric="record_integrity",
                score=100.0,
                evidence="No handoffs recorded",
                findings="No handoffs to assess.",
            )

        retroactive = sum(
            1 for h in handoff_history
            if h.get("payload", {}).get("retroactive") is True
        )
        ratio = retroactive / total
        score = max(0.0, min(100.0, 100.0 - ratio * 200.0))

        if retroactive == 0:
            findings = "All handoffs recorded in real-time. Perfect governance trail."
        else:
            findings = (
                f"{retroactive}/{total} handoffs ({ratio*100:.0f}%) recorded retroactively. "
                "Retroactive records are honest corrections, not violations."
            )

        return MetricResult(
            metric="record_integrity",
            score=round(score, 1),
            evidence=f"total_handoffs={total}, retroactive={retroactive}, ratio={ratio:.2f}",
            findings=findings,
            exemplary=retroactive == 0,
            breakdown={"total": total, "retroactive": retroactive, "live": total - retroactive},
        )

    def score_test_drift_detection(
        self,
        changed_files: list | None = None,
    ) -> MetricResult:
        """Detect implementation changes that lack paired test updates.

        prop-TP-042 / proj-YYYYMMDD-NNN. The evaluator passes a list of paths
        (typically from ``git diff --name-only <range>``). The metric scores
        the proportion of implementation files that have at least one paired
        test edit.

        Scoring:
        - ``changed_files`` empty / None -> ``mode='not_applicable'``
          (excluded from the project average; the metric had no signal).
        - All changed files are docs / changelog / config -> 100
          (nothing to drift).
        - Otherwise: ``(impl files with a paired test edit) / (impl files) * 100``.

        A "paired test edit" is satisfied for an impl file P when any other
        path in the changed set is under ``tests/`` (or starts with
        ``test_``/ends with ``_test.py``) AND mentions P's module basename.
        Test files themselves do not count as impl files.
        """
        files = list(changed_files or [])
        if not files:
            return MetricResult(
                metric="test_drift_detection",
                score=-1.0,
                evidence="No changed-file context provided",
                findings="Evaluator did not supply a changed-files list — metric "
                         "marked not_applicable and excluded from the average.",
                mode="not_applicable",
            )

        def _norm(p: str) -> str:
            return p.replace("\\", "/")

        files = [_norm(p) for p in files]

        # --- classify ---
        DOC_SUFFIXES = (".md", ".rst", ".txt")
        DOC_PREFIXES = ("docs/",)
        CONFIG_SUFFIXES = (".yaml", ".yml", ".toml", ".cfg", ".ini", ".json")

        def is_doc(p: str) -> bool:
            return p.endswith(DOC_SUFFIXES) or any(
                p.startswith(pre) for pre in DOC_PREFIXES
            ) or p.upper().endswith("CHANGELOG.MD")

        def is_test(p: str) -> bool:
            if "/tests/" in p or p.startswith("tests/"):
                return True
            base = p.rsplit("/", 1)[-1]
            return base.startswith("test_") or base.endswith("_test.py")

        def is_config_only(p: str) -> bool:
            # Treat top-level config / policy files as drift-irrelevant.
            return p.endswith(CONFIG_SUFFIXES)

        impl_files = [
            p for p in files
            if p.endswith(".py") and not is_test(p) and not is_doc(p)
        ]
        test_files = [p for p in files if is_test(p)]

        if not impl_files:
            return MetricResult(
                metric="test_drift_detection",
                score=100.0,
                evidence=f"changed_files={len(files)}, impl_files=0",
                findings="No implementation files changed — nothing to drift.",
                exemplary=True,
                breakdown={"total": len(files), "impl": 0, "tests": len(test_files)},
            )

        def _module_basename(p: str) -> str:
            return p.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        paired_count = 0
        unpaired: list[str] = []
        for impl in impl_files:
            base = _module_basename(impl)
            if any(base in t for t in test_files):
                paired_count += 1
            else:
                unpaired.append(impl)

        score = (paired_count / len(impl_files)) * 100.0

        if not unpaired:
            findings = (
                f"All {len(impl_files)} impl file(s) have paired test edits — "
                "no drift detected."
            )
        else:
            findings = (
                f"{paired_count}/{len(impl_files)} impl file(s) paired with test "
                f"edits; {len(unpaired)} unpaired: {unpaired[:5]}"
                + ("..." if len(unpaired) > 5 else "")
            )

        return MetricResult(
            metric="test_drift_detection",
            score=round(score, 1),
            evidence=(
                f"impl={len(impl_files)}, tests={len(test_files)}, "
                f"paired={paired_count}, unpaired={len(unpaired)}"
            ),
            findings=findings,
            exemplary=score == 100.0,
            breakdown={
                "impl_files": impl_files,
                "test_files": test_files,
                "paired": paired_count,
                "unpaired": unpaired,
            },
        )

    # ------------------------------------------------------------------
    # Aggregate scoring
    # ------------------------------------------------------------------

    def aggregate_project_score(self, metric_results: list) -> float:
        """Weighted average of applicable project metric scores, using the weights in
        evaluation_policy.yaml → project_metric_weights (outcome metrics weighted higher
        than process metrics). Metrics with mode='not_applicable' are excluded; metrics
        with no configured weight default to 1.0 (so an empty policy = equal-weight)."""
        return _weighted_average(metric_results, _project_metric_weights())

    def aggregate_agent_score(self, metric_results: list) -> float:
        """Weighted average of applicable agent metric scores, using
        evaluation_policy.yaml → agent_evaluation.metric_weights."""
        return _weighted_average(metric_results, _agent_metric_weights())

    # ------------------------------------------------------------------
    # Full report construction
    # ------------------------------------------------------------------

    def evaluate_project(
        self,
        project_id: str,
        shared_state: dict,
        project_dir: Path,
        task_board_data: dict,
    ) -> list:
        """Compute all project-level metrics. Returns list[MetricResult]."""
        pd_data = shared_state.get("project_definition", {})
        wf = shared_state.get("workflow", {})
        decisions = shared_state.get("decisions", {})

        success_criteria = pd_data.get("success_criteria") or []
        if isinstance(success_criteria, str):
            success_criteria = [success_criteria]
        # Normalize dict entries (e.g. {"criterion": "..."}) to plain strings
        success_criteria = [
            c.get("criterion", "") if isinstance(c, dict) else c
            for c in success_criteria
            if c
        ]
        success_criteria = [c for c in success_criteria if c]

        handoff_history = wf.get("handoff_history", [])
        decision_log = decisions.get("decision_log", [])

        meta = shared_state.get("_meta", {})
        governance_violations = meta.get("governance_violations", []) or []

        tasks = task_board_data.get("tasks", [])
        planned = len(tasks)
        completed = sum(1 for t in tasks if t["status"] == "completed")
        blocked = sum(1 for t in tasks if t["status"] == "blocked")
        failed = sum(1 for t in tasks if t["status"] == "failed")
        over_effort = sum(1 for t in tasks if t.get("over_effort"))

        completed_descs = [t["description"] for t in tasks if t["status"] == "completed"]
        # ip-006: also collect expected_outputs for richer goal-achievement matching
        task_outputs: list[str] = []
        for t in tasks:
            if t.get("status") == "completed":
                eo = t.get("expected_outputs") or []
                if isinstance(eo, list):
                    task_outputs.extend(str(x) for x in eo)
                elif eo:
                    task_outputs.append(str(eo))
        outcome_evidence = self._collect_outcome_evidence(shared_state)

        # Read ACs directly from shared state (most accurate; avoids stale filesystem reads)
        ac_list = pd_data.get("acceptance_criteria") or []
        total_ac = len(ac_list)
        passed_ac = sum(1 for ac in ac_list if ac.get("met") is True)

        # Fallback: estimate from filesystem product_plan.yaml if shared state has no ACs
        if total_ac == 0:
            plan_path = project_dir / "planning" / "product_plan.yaml"
            if plan_path.exists():
                with open(plan_path, encoding="utf-8") as f:
                    plan = yaml.safe_load(f)
                must_haves = plan.get("requirements", {}).get("must_have", [])
                for req in must_haves:
                    criteria = req.get("acceptance_criteria") or []
                    total_ac += len(criteria)
                if planned > 0 and completed == planned:
                    passed_ac = total_ac
                elif planned > 0:
                    passed_ac = int(total_ac * (completed / planned))

        metrics = []
        if success_criteria and not outcome_evidence:
            metrics.append(self._evidence_required_metric(
                "goal_achievement",
                outcome_evidence,
                "Marked not_applicable until explicit verification or performance evidence is recorded.",
            ))
        else:
            metrics.append(self.score_goal_achievement(success_criteria, completed_descs, task_outputs))

        if total_ac > 0 and not outcome_evidence:
            metrics.append(self._evidence_required_metric(
                "acceptance_criteria_pass_rate",
                outcome_evidence,
                "Marked not_applicable until acceptance criteria are backed by explicit verification evidence.",
            ))
        else:
            metrics.append(self.score_acceptance_criteria_pass_rate(total_ac, passed_ac))

        # prop-TP-042: test_drift_detection — opt-in via shared_state.
        # When evaluator (or master) populates
        # ``evaluation.test_drift_context.changed_files``, the metric scores
        # paired test edits. Without that context the metric is excluded
        # from the average (mode='not_applicable').
        td_ctx = (
            shared_state.get("evaluation", {})
            .get("test_drift_context", {}) or {}
        )
        changed_files = td_ctx.get("changed_files") or []

        metrics.extend([
            self.score_scope_adherence(planned, completed, blocked, failed, over_effort),
            self.score_documentation_completeness(project_dir, shared_state),
            self.score_phase_efficiency(handoff_history),
            self.score_decision_quality(decision_log),
            self.score_governance_compliance(governance_violations),
            self.score_record_integrity(handoff_history),
            self.score_test_drift_detection(changed_files),
        ])

        return metrics

    def evaluate_agent(
        self,
        agent_id: str,
        shared_state: dict,
        task_board_data: dict,
    ) -> "AgentEvaluation":
        """Compute all agent-level metrics for one agent."""
        wf = shared_state.get("workflow", {})
        meta = shared_state.get("_meta", {})

        handoff_history = wf.get("handoff_history", [])
        violations = meta.get("governance_violations", [])
        tasks = task_board_data.get("tasks", [])

        metrics = [
            self.score_task_completion_rate(agent_id, tasks),
            self.score_handoff_quality(agent_id, handoff_history),
            self.score_boundary_adherence(agent_id, violations),
        ]

        overall = self.aggregate_agent_score(metrics)

        strengths = [m.metric for m in metrics if m.score >= 80.0]
        issues = [m.metric for m in metrics if m.score < 60.0]

        recommendations = []
        for m in metrics:
            if m.score < 60.0:
                recommendations.append(f"Improve {m.metric}: {m.findings}")
        if overall > EXEMPLARY_THRESHOLD:
            recommendations.append("Consider promoting to T0_core or using as reference implementation")

        return AgentEvaluation(
            agent_id=agent_id,
            metrics=metrics,
            overall_score=overall,
            strengths=strengths,
            issues=issues,
            recommendations=recommendations,
            exemplary=overall > EXEMPLARY_THRESHOLD,
            recommend_probation=overall < PROBATION_THRESHOLD,
        )

    def produce_report(
        self,
        project_id: str,
        shared_state: dict,
        project_dir: Path,
        task_board_data: dict,
        agents_to_evaluate: list,
    ) -> "EvaluationReport":
        """Produce a full EvaluationReport."""
        now = datetime.now(timezone.utc).isoformat()
        seq = now.replace(":", "").replace("-", "").replace(".", "")[:14]
        report_id = f"eval-{project_id}-{seq}"

        project_metrics = self.evaluate_project(project_id, shared_state, project_dir, task_board_data)
        overall_project = self.aggregate_project_score(project_metrics)

        agent_evals = [
            self.evaluate_agent(agent_id, shared_state, task_board_data)
            for agent_id in agents_to_evaluate
        ]

        total_violations = len(shared_state.get("_meta", {}).get("governance_violations", []))
        exemplary_agents = [a.agent_id for a in agent_evals if a.exemplary]
        probation_agents = [a.agent_id for a in agent_evals if a.recommend_probation]
        tasks = task_board_data.get("tasks", [])
        blocked_tasks = [t for t in tasks if t["status"] == "blocked"]
        over_effort_tasks = [t for t in tasks if t.get("over_effort")]

        bottlenecks = []
        if blocked_tasks:
            bottlenecks.append(
                f"{len(blocked_tasks)} task(s) blocked: "
                + ", ".join(t["task_id"] for t in blocked_tasks)
            )
        if over_effort_tasks:
            bottlenecks.append(
                f"{len(over_effort_tasks)} over-effort task(s): "
                + ", ".join(t["task_id"] for t in over_effort_tasks)
            )

        comms = self.compute_communication_efficiency(shared_state)

        return EvaluationReport(
            report_id=report_id,
            project_id=project_id,
            timestamp=now,
            evaluator="evaluator_agent",
            project_metrics=project_metrics,
            agent_evaluations=agent_evals,
            overall_project_score=overall_project,
            systemic_findings={
                "bottlenecks": bottlenecks,
                "drift_detected": [],
                "pattern_issues": [],
                "total_violations": total_violations,
            },
            recommendations={
                "improvement_areas": [m.metric for m in project_metrics if m.score < 70.0],
                "priority_ranking": sorted(
                    [m.metric for m in project_metrics if m.score < 70.0],
                    key=lambda x: next(m.score for m in project_metrics if m.metric == x),
                ),
                "suggested_actions": (
                    [f"Flag {a} for probation review" for a in probation_agents]
                    + [f"Use {a} as exemplary reference" for a in exemplary_agents]
                ),
            },
            communication_efficiency=comms,
        )

    def compute_communication_efficiency(self, shared_state: dict) -> dict:
        """Advisory communication-efficiency dimension (policy weight 0.5).

        Surfaces token/payload/context/consultation efficiency in the report. ADVISORY
        ONLY — never folded into overall_project_score and never a governance signal
        (per evaluation_policy.yaml → communication_efficiency). Low scores are the
        signal that training_engine turns into communication_waste proposals."""
        wf = shared_state.get("workflow", {}) or {}
        comm = shared_state.get("communication", {}) or {}
        consultation = shared_state.get("consultation", {}) or {}
        handoff_history = wf.get("handoff_history", []) or []
        phase_count = max(1, len(wf.get("completed_phases", []) or []))
        consultation_responses = consultation.get("consultation_responses", []) or []
        decisions_made = len((shared_state.get("decisions", {}) or {}).get("decision_log", []) or [])

        metrics = [
            self.score_token_efficiency(handoff_history, phase_count),
            self.score_payload_density(handoff_history),
            self.score_context_injection_efficiency([], comm.get("total_tokens_used", 0) or 1),
            self.score_consultation_overhead(consultation_responses, decisions_made),
        ]
        applicable = [m for m in metrics if m.mode != "not_applicable"]
        dim_score = round(sum(m.score for m in applicable) / len(applicable), 1) if applicable else None
        low_threshold = float(
            _load_eval_policy().get("communication_efficiency", {}).get("low_score_threshold", 65)
        )
        return {
            "weight": 0.5,
            "advisory": True,
            "dimension_score": dim_score,
            "below_threshold": (dim_score is not None and dim_score < low_threshold),
            "metrics": [
                {"metric": m.metric, "score": round(m.score, 1), "findings": m.findings}
                for m in metrics
            ],
            "note": "Advisory only — not included in overall_project_score or governance.",
        }

    def save_report(
        self,
        report: "EvaluationReport",
        project_dir: Path,
    ) -> Path:
        """Write the evaluation report to disk and log the evaluation event to the DB."""
        eval_dir = project_dir / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        path = eval_dir / "evaluation_report.yaml"
        report_dict = report.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(report_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        # ip-skill-002: the orchestrator phase gate's evaluation exit artifact is
        # project_evaluation.yaml. Emit it alongside the engine's evaluation_report.yaml (identical
        # content) so operators never have to hand-write it. evaluation_report.yaml is retained for
        # the training engine and existing readers.
        canonical_path = eval_dir / "project_evaluation.yaml"
        with open(canonical_path, "w", encoding="utf-8") as f:
            yaml.dump(report_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        if _append_event is not None:
            try:
                _append_event(
                    project_id=report.project_id,
                    agent_id="evaluator_agent",
                    action_type="evaluation_completed",
                    intent=f"Evaluation complete: overall score {report.overall_project_score:.1f}",
                    result_shape="evaluation_report",
                    payload={
                        "overall_score": report.overall_project_score,
                        "report_path": str(path),
                        "agent_scores": {a.agent_id: a.overall_score
                                         for a in report.agent_evaluations},
                    },
                )
            except Exception as exc:
                logger.debug("report DB logging failed (non-blocking): %s", exc)
        return path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="metrics_engine",
        description="Metrics Engine CLI — score projects and agents",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("score-project", help="Score project-level metrics")
    sp.add_argument("--project-id", required=True)

    sa = sub.add_parser("score-agent", help="Score a single agent")
    sa.add_argument("--project-id", required=True)
    sa.add_argument("--agent-id", required=True)

    sr = sub.add_parser("report", help="Produce a full evaluation report")
    sr.add_argument("--project-id", required=True)
    sr.add_argument("--agents", default=None,
                    help="Comma-separated agent IDs (default: all active)")
    sr.add_argument("--save", action="store_true",
                    help="Write report to disk")

    return p


def _load_project_data(project_id: str):
    """Load shared state and task board data for a project."""
    from .shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    state = sm.load()
    project_dir = sm.project_dir  # already layout-resolved (flat or family-nested)

    board_path = project_dir / "execution" / "task_board.yaml"
    if board_path.exists():
        with open(board_path, encoding="utf-8") as f:
            board_data = yaml.safe_load(f) or {}
    else:
        board_data = {"tasks": [], "milestones": []}

    return state, project_dir, board_data


def main_cli(args=None) -> int:
    # Ensure Unicode output (checkmarks etc.) doesn't crash on Windows cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = _build_parser()
    ns = p.parse_args(args)
    engine = MetricsEngine()

    if ns.command == "score-project":
        state, project_dir, board_data = _load_project_data(ns.project_id)
        metrics = engine.evaluate_project(
            ns.project_id, state, project_dir, board_data
        )
        print(f"\nProject metrics for {ns.project_id}:")
        for m in metrics:
            star = " *" if m.exemplary else ""
            print(f"  {m.metric:40} {m.score:6.1f}{star}")
            print(f"    {m.findings}")
        avg = engine.aggregate_project_score(metrics)
        print(f"\n  Overall project score: {avg:.1f}")
        return 0

    if ns.command == "score-agent":
        state, _, board_data = _load_project_data(ns.project_id)
        result = engine.evaluate_agent(ns.agent_id, state, board_data)
        print(f"\nAgent evaluation: {ns.agent_id}")
        for m in result.metrics:
            star = " *" if m.exemplary else ""
            print(f"  {m.metric:40} {m.score:6.1f}{star}")
        print(f"\n  Overall score   : {result.overall_score:.1f}")
        print(f"  Strengths       : {', '.join(result.strengths) or 'none'}")
        print(f"  Issues          : {', '.join(result.issues) or 'none'}")
        print(f"  Exemplary       : {result.exemplary}")
        print(f"  Probation risk  : {result.recommend_probation}")
        return 0

    if ns.command == "report":
        state, project_dir, board_data = _load_project_data(ns.project_id)

        if ns.agents:
            agents = [a.strip() for a in ns.agents.split(",")]
        else:
            wf = state.get("workflow", {})
            agents = list({
                h.get("from_agent") for h in wf.get("handoff_history", [])
                if h.get("from_agent") != "system"
            })

        report = engine.produce_report(
            ns.project_id, state, project_dir, board_data, agents
        )

        print(f"\nEvaluation Report: {report.report_id}")
        print(f"Overall project score: {report.overall_project_score:.1f}")
        print("\nProject metrics:")
        for m in report.project_metrics:
            star = " *" if m.exemplary else ""
            print(f"  {m.metric:40} {m.score:6.1f}{star}")
        print("\nAgent evaluations:")
        for ae in report.agent_evaluations:
            flag = " [EXEMPLARY]" if ae.exemplary else ""
            flag += " [PROBATION RISK]" if ae.recommend_probation else ""
            print(f"  {ae.agent_id:30} {ae.overall_score:6.1f}{flag}")

        if ns.save:
            path = engine.save_report(report, project_dir)
            print(f"\n[ok] Report saved: {path}")
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main_cli())
