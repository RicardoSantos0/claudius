"""
MAS Lifecycle Guard

Evaluates lifecycle invariants from mas/policies/lifecycle_invariants.yaml
before phase transitions, project close, and spawn requests.
Blocks actions that violate hard invariants; warns on soft ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _find_repo_root() -> Path:
    # Repo root in a clone, or the $MAS_HOME workspace root when pip-installed.
    from core.paths import repo_root
    return repo_root()


@dataclass
class GuardResult:
    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.passed


class LifecycleGuard:
    """Checks lifecycle invariants and artifact contracts before key MAS actions."""

    _INVARIANTS_REL = Path("mas") / "policies" / "lifecycle_invariants.yaml"
    _CONTRACTS_REL = Path("mas") / "policies" / "artifact_contracts.yaml"

    def __init__(self) -> None:
        self._repo_root = _find_repo_root()
        self._invariants = self._load_invariants()
        self._contracts = self._load_contracts()

    def _load_invariants(self) -> list[dict]:
        path = self._repo_root / self._INVARIANTS_REL
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("invariants", [])

    def _load_contracts(self) -> dict:
        path = self._repo_root / self._CONTRACTS_REL
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("phases", {})

    def check_phase_artifacts(self, phase: str, project_dir: Path) -> GuardResult:
        """Check that required artifacts exist for the given phase."""
        contract = self._contracts.get(phase, {})
        required = contract.get("required", [])
        violations = []
        for artifact in required:
            if not (project_dir / artifact).exists():
                violations.append({
                    "invariant": f"artifact-contract:{phase}",
                    "missing": artifact,
                    "severity": "block",
                })
        return GuardResult(passed=len(violations) == 0, violations=violations)

    def check_close(self, project_dir: Path, shared_state: dict) -> GuardResult:
        """Run close-specific invariants against current state."""
        violations = []
        warnings = []

        # no-close-with-open-handoffs
        workflow = shared_state.get("workflow", {})
        pending = list(workflow.get("pending_assignments", []) or [])
        for handoff in workflow.get("handoff_history", []) or []:
            acceptance = handoff.get("acceptance", {})
            status = acceptance.get("status") if isinstance(acceptance, dict) else handoff.get("status")
            if status == "pending":
                pending.append(handoff)
        if pending:
            violations.append({
                "invariant": "no-close-with-open-handoffs",
                "detail": f"{len(pending)} pending assignment(s)",
                "severity": "block",
            })

        trace_result = check_standard_handoff_trace(shared_state)
        violations.extend(trace_result.violations)
        warnings.extend(trace_result.warnings)

        # no-close-with-open-questions
        open_q = shared_state.get("decisions", {}).get("open_questions", [])
        if open_q:
            warnings.append({
                "invariant": "no-close-with-open-questions",
                "detail": f"{len(open_q)} open question(s)",
                "severity": "warn",
            })

        # required closed-phase artifacts
        artifact_result = self.check_phase_artifacts("closed", project_dir)
        violations.extend(artifact_result.violations)

        return GuardResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    def check_spawn(self, project_dir: Path) -> GuardResult:
        """Verify gap certificate exists before spawn."""
        cert_path = project_dir / "governance" / "gap_certificate.yaml"
        if not cert_path.exists():
            return GuardResult(
                passed=False,
                violations=[{
                    "invariant": "no-spawn-without-gap-certification",
                    "missing": "governance/gap_certificate.yaml",
                    "severity": "block",
                }],
            )
        return GuardResult(passed=True)


# ---------------------------------------------------------------------------
# Standard-mode trace integrity
# ---------------------------------------------------------------------------

def _project_mode(shared_state: dict) -> str:
    workflow = shared_state.get("workflow", {}) or {}
    core_identity = shared_state.get("core_identity", {}) or {}
    return str(workflow.get("mode") or core_identity.get("mode") or "standard")


def _handoff_status(handoff: dict) -> str:
    acceptance = handoff.get("acceptance", {})
    if isinstance(acceptance, dict) and acceptance.get("status"):
        return str(acceptance["status"])
    return str(handoff.get("status", ""))


def _is_accepted_inquirer_intake_handoff(handoff: dict) -> bool:
    if _handoff_status(handoff) != "accepted":
        return False
    if handoff.get("phase") != "intake":
        return False
    return "inquirer_agent" in {
        str(handoff.get("from_agent", "")),
        str(handoff.get("to_agent", "")),
    }


def check_standard_handoff_trace(shared_state: dict) -> GuardResult:
    """Require a real handoff trace for standard MAS projects.

    Lite mode intentionally remains flexible. Standard mode is the governed
    multi-phase workflow, so closing or post-intake operation without any
    handoff evidence is treated as a blocking governance gap.
    """
    if _project_mode(shared_state) != "standard":
        return GuardResult(passed=True)

    workflow = shared_state.get("workflow", {}) or {}
    history = workflow.get("handoff_history", []) or []
    violations: list[dict[str, Any]] = []

    if not history:
        violations.append({
            "invariant": "standard-project-requires-handoffs",
            "detail": (
                "standard MAS projects must record governed handoffs; use "
                "mas prompt/mas ingest or mas run instead of direct state edits"
            ),
            "severity": "block",
        })

    if not any(_is_accepted_inquirer_intake_handoff(h) for h in history):
        violations.append({
            "invariant": "standard-project-requires-inquirer-intake",
            "detail": (
                "standard MAS intake must include an accepted inquirer_agent "
                "handoff before later phase completion or closure"
            ),
            "severity": "block",
        })

    return GuardResult(passed=not violations, violations=violations)


def check_standard_intake_advance(phase: str, mode: str, acting_agent: str) -> GuardResult:
    """Block standard-mode intake advancement by anyone except inquirer_agent."""
    if mode != "standard" or phase != "intake":
        return GuardResult(passed=True)
    if acting_agent == "inquirer_agent":
        return GuardResult(passed=True)
    return GuardResult(
        passed=False,
        violations=[{
            "invariant": "standard-intake-requires-inquirer",
            "detail": (
                f"acting_agent={acting_agent}; standard intake must be ingested "
                "as inquirer_agent"
            ),
            "severity": "block",
        }],
    )


# ---------------------------------------------------------------------------
# Proposal 3a — max_handoffs_per_phase policy
# ---------------------------------------------------------------------------

def check_handoff_count(phase: str, handoff_count: int) -> dict:
    """Evaluate whether the handoff count for *phase* is within policy limits.

    Returns a dict with keys ``status`` (``"ok"``, ``"warn"``, or ``"flag"``)
    and ``message``.
    """
    if handoff_count <= 4:
        return {"status": "ok", "message": ""}
    if handoff_count <= 6:
        return {
            "status": "warn",
            "message": (
                f"Handoff count {handoff_count} exceeds 2x ideal for phase {phase}. "
                "Review necessity."
            ),
        }
    return {
        "status": "flag",
        "message": (
            f"Handoff count {handoff_count} exceeds 3x ideal for phase {phase}. "
            "Governance flag raised — Master acknowledgement required before phase close."
        ),
    }


# ---------------------------------------------------------------------------
# Proposal 3b — stale handoff auto-expire
# ---------------------------------------------------------------------------

def expire_stale_handoffs(
    pending_handoffs: list[dict],
    phase_closing: str,
) -> tuple[list[dict], list[dict]]:
    """Split *pending_handoffs* into those that remain pending and those auto-expired.

    A handoff is auto-expired when its ``status`` is ``"pending"`` and its
    ``phase`` matches *phase_closing*.  Auto-expired entries receive:
      - ``status: "auto_expired"``
      - ``expired_at: <ISO 8601 UTC timestamp>``
      - ``reason: "auto_expired_on_phase_close"``
    """
    now = datetime.now(timezone.utc).isoformat()
    still_pending: list[dict] = []
    auto_expired: list[dict] = []

    for handoff in pending_handoffs:
        if handoff.get("status") == "pending" and handoff.get("phase") == phase_closing:
            expired = dict(handoff)
            expired["status"] = "auto_expired"
            expired["expired_at"] = now
            expired["reason"] = "auto_expired_on_phase_close"
            auto_expired.append(expired)
        else:
            still_pending.append(handoff)

    return still_pending, auto_expired


# ---------------------------------------------------------------------------
# G1 — execution-entry task-board gate
# (proj-YYYYMMDD-NNN-mas-manual-loop-guardrails)
# ---------------------------------------------------------------------------

def check_execution_entry(
    task_board_data: dict | None,
    *,
    mode: str = "standard",
) -> GuardResult:
    """Guard the transition into the *execution* phase.

    A project must have a materialized task board (>= 1 task) before it enters
    execution — the gap that let proj-YYYYMMDD-NNN cross into execution with an
    empty board. In standard mode an empty board blocks; in lite mode it warns
    only. Pure function (no filesystem) so callers may attempt
    ``TaskBoard.sync_from_execution_plan()`` first and re-check.
    """
    tasks = (task_board_data or {}).get("tasks") or []
    if tasks:
        return GuardResult(passed=True)

    entry = {
        "invariant": "no-execution-without-task-board",
        "detail": (
            "task board has no tasks; populate it (or sync from the execution "
            "plan) before entering execution"
        ),
        "severity": "block",
    }
    if mode == "lite":
        entry["severity"] = "warn"
        return GuardResult(passed=True, warnings=[entry])
    return GuardResult(passed=False, violations=[entry])
