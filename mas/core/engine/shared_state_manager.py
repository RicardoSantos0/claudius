"""
Shared State Manager
Single source of truth for any active project.
Enforces access control, mutability, and append-only rules on every write.

Usage as library:
    from core.engine.shared_state_manager import SharedStateManager
    sm = SharedStateManager("proj-YYYYMMDD-NNN")
    sm.initialize(request_id="req-001")

Usage as CLI:
    uv run python mas/core/engine/shared_state_manager.py init --project-id proj-001 --request-id req-001
    uv run python core/shared_state_manager.py read --project-id proj-001 --path core_identity.current_phase
    uv run python core/shared_state_manager.py write --project-id proj-001 --section core_identity --field status --value active --agent master_orchestrator
    uv run python core/shared_state_manager.py append --project-id proj-001 --section decisions --field assumptions --value-json '{"assumption_id":"a-001","stated_by":"master_orchestrator","description":"..."}' --agent master_orchestrator
    uv run python core/shared_state_manager.py approve --project-id proj-001 --section project_definition --field original_brief --agent master_orchestrator
    uv run python core/shared_state_manager.py snapshot --project-id proj-001 --phase intake
    uv run python core/shared_state_manager.py show --project-id proj-001
"""

import sys
import json
import logging
import shutil
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.paths import mas_root
ROOT = mas_root()

from core.engine.access_control import (
    ACCESS_CONTROL, is_authorized, get_mode, get_mutability,
    requires_append_only, is_immutable, is_immutable_after_approval,
    SYSTEM, ANY_AGENT,
)
from core.engine.audit_logger import AuditLogger, get_logger

logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    success: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.success


# --- INITIAL STATE FACTORY ---

LITE_PHASES = ("intake", "execution", "closed")
STANDARD_PHASES = (
    "intake", "specification", "planning", "capability_discovery",
    "execution", "review", "evaluation", "improvement", "closed",
)


def create_initial_state(project_id: str, request_id: str,
                         mode: str = "standard") -> dict:
    """
    Build the initial shared state for a new project.

    mode="lite"     → 3-phase lifecycle (intake → execution → closed).
                      No capability discovery, no consultation, no HR handoff.
    mode="standard" → full 9-phase lifecycle (default).
    """
    if mode not in ("standard", "lite"):
        mode = "standard"
    now = datetime.now(timezone.utc).isoformat()
    return {
        "core_identity": {
            "project_id": project_id,
            "request_id": request_id,
            "created_at": now,
            "updated_at": now,
            "current_phase": "intake",
            "status": "active",
        },
        "project_definition": {
            "original_brief": None,
            "clarified_specification": None,
            "project_goal": None,
            "problem_statement": None,
            "scope": {"inclusions": [], "exclusions": []},
            "constraints": [],
            "success_criteria": [],
            "acceptance_criteria": [],
            "risk_classification": None,
            "priority": None,
            "target_area": None,
        },
        "workflow": {
            "mode": mode,
            "active_agents": [],
            "completed_phases": [],
            "pending_assignments": [],
            "current_owner": "master_orchestrator",
            "handoff_history": [],
            "resource_requests": [],
            "resource_allocations": [],
        },
        "decisions": {
            "decision_log": [],
            "assumptions": [],
            "open_questions": [],
            "approvals": [],
            "policy_flags": [],
        },
        "capability": {
            "available_skills_snapshot": [],
            "reuse_candidates": [],
            "capability_gap_certificates": [],
            "spawn_requests": [],
            "spawned_agents": [],
            "verification_results": [],
        },
        "execution": {
            "execution_plan_path": None,
            "milestones": [],
            "tasks": [],
            "resource_requests": [],
            "progress_reports": [],
            "blocker_alerts": [],
            "delivery_risks": [],
        },
        "artifacts": {
            "documents": [],
            "deliverables": [],
            "change_log": [],
        },
        "evaluation": {
            "performance_metrics": [],
            "quality_findings": [],
            "improvement_proposals": [],
            "approved_updates": [],
        },
        "consultation": {
            "consultation_requests": [],
            "consultation_responses": [],
            "synthesis": [],
        },
        "communication": {
            "token_tracking_enabled": True,
            "total_tokens_used": 0,
            "tokens_by_agent": {},
            "tokens_by_phase": {},
            "wire_compliance_rate": None,
            "wire_compliant_count": 0,
            "wire_total_count": 0,
        },
        "_meta": {
            "version": "1.0.0",
            "approved_fields": [],
            "governance_violations": [],
        },
    }


# --- MAIN CLASS ---

class SharedStateManager:
    """
    Manages shared state for a single project.
    Enforces access control and governance rules on every write.
    """

    def __init__(self, project_id: str,
                 projects_root: Path | None = None,
                 audit_logger: AuditLogger | None = None):
        self.project_id = project_id
        self.projects_root = projects_root or (ROOT / "projects")
        # Resolve to the real dir (supports both flat projects/<id>/ and family-nested
        # projects/<family>/<id>/). For a not-yet-created project this returns the flat
        # path, so `initialize()` still creates projects/<id>/ as before. (F2b)
        try:
            from core.utils.config import resolve_project_dir
            self.project_dir = resolve_project_dir(project_id, projects_root=self.projects_root)
        except Exception:
            self.project_dir = self.projects_root / project_id
        self.state_path = self.project_dir / "shared_state.yaml"
        self.logger = audit_logger or get_logger()

    # --- LIFECYCLE ---

    def initialize(self, request_id: str, mode: str = "standard") -> None:
        """Create project directory and initialize shared state. Idempotent."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            state = create_initial_state(self.project_id, request_id, mode=mode)
            self._save(state)
            self.logger.log(
                "project_initialized",
                project_id=self.project_id,
                request_id=request_id,
                mode=mode,
            )

    def exists(self) -> bool:
        return self.state_path.exists()

    # --- READ ---

    def load(self) -> dict:
        """Load and return the current shared state from disk."""
        try:
            from core.runtime_config import get_database_backend
            backend = get_database_backend()
            if backend.get("active_provider") == "postgresql":
                from core.db import get_shared_state
                state = get_shared_state(self.project_id)
                if state:
                    try:
                        with open(self.state_path, "w", encoding="utf-8") as f:
                            yaml.dump(state, f, default_flow_style=False,
                                      allow_unicode=True, sort_keys=False)
                    except Exception as exc:
                        logger.debug("postgres state write-back to disk failed: %s", exc)
                    return state
        except Exception as exc:
            logger.debug("postgres-backend load skipped, falling back to file: %s", exc)
        with open(self.state_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def read(self, path: str) -> Any:
        """
        Read a value by dot-notation path (e.g. 'core_identity.current_phase').
        Returns None if path not found.
        """
        state = self.load()
        parts = path.split(".")
        node = state
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    # --- WRITE ---

    def write(self, agent_id: str, section: str, field: str,
              value: Any) -> WriteResult:
        """
        Write a value to section.field with full governance checks.
        Returns WriteResult(success, reason).
        """
        field_path = f"{section}.{field}"
        state = self.load()

        # 1. Check authorization
        if not is_authorized(agent_id, field_path):
            reason = "unauthorized_write"
            self._record_violation(state, agent_id, field_path, reason)
            self.logger.log_violation(agent_id, field_path, self.project_id, reason)
            return WriteResult(False, reason)

        # 2. Check immutability (set-once fields like created_at)
        if is_immutable(field_path):
            current = state.get(section, {}).get(field)
            if current is not None:
                reason = "field_is_immutable"
                self._record_violation(state, agent_id, field_path, reason)
                self.logger.log_violation(agent_id, field_path, self.project_id, reason)
                return WriteResult(False, reason)

        # 3. Check immutable-after-approval
        if is_immutable_after_approval(field_path):
            approved = state.get("_meta", {}).get("approved_fields", [])
            if field_path in approved:
                reason = "field_is_immutable"
                self._record_violation(state, agent_id, field_path, reason)
                self.logger.log_violation(agent_id, field_path, self.project_id, reason)
                return WriteResult(False, reason)

        # 4. Reject writes to append-only fields (must use append())
        if requires_append_only(field_path):
            reason = "field_is_append_only"
            self._record_violation(state, agent_id, field_path, reason)
            self.logger.log_violation(agent_id, field_path, self.project_id, reason)
            return WriteResult(False, reason)

        # 5. Apply write
        if section not in state:
            state[section] = {}
        state[section][field] = value
        state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(state)
        self.logger.log_write(agent_id, field_path, self.project_id, True)

        # 6. Checkpoint after phase transitions
        if field_path == "core_identity.current_phase":
            try:
                from core.engine.checkpoint_writer import CheckpointWriter
                CheckpointWriter(self.project_id).write()
            except Exception as exc:
                logger.debug("checkpoint write failed (non-blocking): %s", exc)

        return WriteResult(True)

    def write_as(self, synthesizing_agent: str, target_agent: str,
                 section: str, field: str, value: Any,
                 mode: str = "claude_code_manual") -> WriteResult:
        """
        Write on behalf of target_agent identity (TP-043 — inline consultant attribution).

        Only permitted in claude_code_manual mode. The synthesizing_agent must own
        the field OR be master_orchestrator. The write is attributed to target_agent
        for governance purposes and logged with source=inline_synthesis.

        Args:
            synthesizing_agent: the agent performing the write (must be master_orchestrator)
            target_agent: the consultant identity being synthesized (e.g., "risk_advisor")
            section: shared state section
            field: field within section
            value: value to write
            mode: must be "claude_code_manual" — raises ValueError otherwise
        """
        if mode != "claude_code_manual":
            return WriteResult(False, "write_as_only_permitted_in_claude_code_manual_mode")
        if synthesizing_agent != "master_orchestrator":
            return WriteResult(False, "write_as_only_permitted_for_master_orchestrator")

        field_path = f"{section}.{field}"
        state = self.load()

        if section not in state:
            state[section] = {}

        current = state[section].get(field, [])
        if isinstance(current, list):
            if isinstance(value, list):
                current.extend(value)
            else:
                current.append(value)
            state[section][field] = current
        else:
            state[section][field] = value

        state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(state)
        self.logger.log(
            "write_as",
            project_id=self.project_id,
            synthesizing_agent=synthesizing_agent,
            target_agent=target_agent,
            field_path=field_path,
            source="inline_synthesis",
        )
        return WriteResult(True)

    def append(self, agent_id: str, section: str, field: str,
               item: Any) -> WriteResult:
        """
        Append an item to an append-only list field.
        Also works for fields with no mode restriction.
        """
        field_path = f"{section}.{field}"
        state = self.load()

        # 1. Check authorization
        if not is_authorized(agent_id, field_path):
            reason = "unauthorized_write"
            self._record_violation(state, agent_id, field_path, reason)
            self.logger.log_violation(agent_id, field_path, self.project_id, reason)
            return WriteResult(False, reason)

        # 2. The field must currently be (or become) a list
        if section not in state:
            state[section] = {}
        current = state[section].get(field, [])
        if current is None:
            current = []
        if not isinstance(current, list):
            return WriteResult(False, "field_is_not_a_list")

        current.append(item)
        state[section][field] = current
        state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(state)
        self.logger.log_write(agent_id, field_path, self.project_id, True)
        return WriteResult(True)

    def system_append(self, section: str, field: str, item: Any) -> WriteResult:
        """Internal system append — bypasses agent authorization check for system-owned fields."""
        return self.append(SYSTEM, section, field, item)

    def system_increment_wire_compliance(self, compliant: bool) -> None:
        """
        Increment wire compliance counters in communication section.
        Best-effort — never raises. Called by HandoffEngine after each create().
        """
        try:
            state = self.load()
            comm = state.setdefault("communication", {})
            comm["wire_total_count"] = comm.get("wire_total_count", 0) + 1
            if compliant:
                comm["wire_compliant_count"] = comm.get("wire_compliant_count", 0) + 1
            total = comm["wire_total_count"]
            comm["wire_compliance_rate"] = round(
                comm["wire_compliant_count"] / total, 4
            ) if total > 0 else None
            self._save(state)
        except Exception as exc:
            logger.debug("wire-compliance counter update failed (non-blocking): %s", exc)

    def system_add_tokens(
        self,
        agent_id: str,
        phase: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Add estimated token usage to communication counters.

        Best-effort telemetry for manual mode and provider paths where exact
        usage is unavailable. Totals are intentionally additive because a prompt
        can be assembled separately from a later response ingestion.

        ``total_tokens`` (IP-3) lets callers record a usage report that only
        carries a total (e.g. a subagent's reported ``subagent_tokens``) without
        a prompt/completion split: it is used as the total when prompt+completion
        sum to zero.
        """
        try:
            prompt = max(0, int(prompt_tokens or 0))
            completion = max(0, int(completion_tokens or 0))
            total = prompt + completion
            if total <= 0:
                total = max(0, int(total_tokens or 0))
            if total <= 0:
                return

            state = self.load()
            comm = state.setdefault("communication", {})
            comm["total_tokens_used"] = int(comm.get("total_tokens_used", 0) or 0) + total

            by_agent = comm.setdefault("tokens_by_agent", {})
            by_agent[agent_id] = int(by_agent.get(agent_id, 0) or 0) + total

            by_phase = comm.setdefault("tokens_by_phase", {})
            phase_key = phase or "unknown"
            by_phase[phase_key] = int(by_phase.get(phase_key, 0) or 0) + total

            state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save(state)
        except Exception as exc:
            logger.debug("token counter update failed (non-blocking): %s", exc)

    # --- APPROVAL ---

    def approve(self, agent_id: str, section: str, field: str) -> WriteResult:
        """
        Mark a field as approved. After this, immutable_after_approval fields
        cannot be changed.
        Only master_orchestrator can approve fields.
        """
        if agent_id != "master_orchestrator":
            return WriteResult(False, "only_master_can_approve")
        field_path = f"{section}.{field}"
        state = self.load()
        meta = state.setdefault("_meta", {})
        approved = meta.setdefault("approved_fields", [])
        if field_path not in approved:
            approved.append(field_path)
        state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(state)
        self.logger.log("field_approved", agent_id=agent_id,
                        field_path=field_path, project_id=self.project_id)
        return WriteResult(True)

    def mark_acceptance_met(self, agent_id: str, criterion_id: str,
                            met: bool = True, evidence: str | None = None) -> WriteResult:
        """Update only the met/evidence of an existing acceptance criterion.

        Permitted even after project_definition.acceptance_criteria is approved:
        the criterion *text* stays immutable (scope lock preserved), but its
        delivery-verification status can be recorded so goal_achievement and
        acceptance_criteria_pass_rate can be scored after delivery. (prop-008-002)
        """
        field_path = "project_definition.acceptance_criteria"
        state = self.load()

        if not is_authorized(agent_id, field_path):
            reason = "unauthorized_write"
            self._record_violation(state, agent_id, field_path, reason)
            self.logger.log_violation(agent_id, field_path, self.project_id, reason)
            return WriteResult(False, reason)

        criteria = state.get("project_definition", {}).get("acceptance_criteria")
        if not isinstance(criteria, list):
            return WriteResult(False, "acceptance_criteria_not_a_list")

        matched = None
        for c in criteria:
            if isinstance(c, dict) and criterion_id in (c.get("id"), c.get("criterion_id")):
                c["met"] = met
                if evidence is not None:
                    c["evidence"] = evidence
                matched = c
                break
        if matched is None:
            return WriteResult(False, "criterion_not_found")

        state["project_definition"]["acceptance_criteria"] = criteria
        state["core_identity"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(state)
        self.logger.log_write(agent_id, f"{field_path}:{criterion_id}", self.project_id, True)
        return WriteResult(True)

    # --- SNAPSHOT ---

    def snapshot(self, phase: str) -> Path:
        """Save a copy of current state as a phase snapshot."""
        self._flush_decisions_to_disk()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        snap_path = self.project_dir / f"shared_state_snapshot_{phase}_{ts}.yaml"
        shutil.copy2(self.state_path, snap_path)
        self.logger.log_phase_transition(self.project_id, "current", phase)
        return snap_path

    def _flush_decisions_to_disk(self) -> None:
        """Auto-flush all decisions from shared_state to decisions/decision_log.yaml.

        File shape contract (ip-001 / proj-YYYYMMDD-NNN): a flat YAML list of
        decision entries. The Scribe template now writes `[]` to match. For
        backward compatibility, this method tolerates legacy dict-shape files
        (``{decision_log: {entries: [...]}}`` or ``{entries: [...]}``) by
        extracting nested entries on read. The file is always rewritten in the
        canonical flat-list form (never appended to in-place, which would
        produce invalid multi-document YAML).
        """
        try:
            state = self.load()
        except Exception:
            return
        decisions = state.get("decisions", {}).get("decision_log", [])
        if not decisions:
            return
        log_path = self.project_dir / "decisions" / "decision_log.yaml"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if log_path.exists():
            try:
                with open(log_path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
            except yaml.YAMLError:
                loaded = None
            if isinstance(loaded, list):
                existing = loaded
            elif isinstance(loaded, dict):
                # Legacy dict-shape recovery
                nested = loaded.get("decision_log")
                if isinstance(nested, dict):
                    existing = nested.get("entries") or []
                else:
                    existing = loaded.get("entries") or []
            else:
                existing = []
        existing_ids = {
            d.get("decision_id") for d in existing if isinstance(d, dict)
        }
        new_entries = [
            d for d in decisions if d.get("decision_id") not in existing_ids
        ]
        if new_entries:
            merged = list(existing) + new_entries
            with open(log_path, "w", encoding="utf-8") as f:
                yaml.dump(merged, f, default_flow_style=False)

    def cleanup_snapshots(self, keep_latest: int = 0, force: bool = False) -> list[Path]:
        """Delete shared_state_snapshot_*.yaml files for this project.

        Only executes when project status is 'closed' unless force=True.
        Must be called as the LAST step in the close sequence — after all other
        close operations are confirmed — to avoid partial cleanup on mid-sequence failure.
        Returns list of deleted paths.
        """
        state = self.load()
        status = state.get("core_identity", {}).get("status", "")
        if status != "closed" and not force:
            return []

        snapshots = sorted(
            self.project_dir.glob("shared_state_snapshot_*.yaml"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        to_delete = snapshots[keep_latest:]
        deleted: list[Path] = []
        for path in to_delete:
            try:
                path.unlink()
                deleted.append(path)
            except FileNotFoundError:
                continue
        return deleted

    # --- GOVERNANCE TRACKING ---

    def _record_violation(self, state: dict, agent_id: str,
                          field_path: str, reason: str) -> None:
        """Record a governance violation inside the state (best-effort, no save here)."""
        violations = state.get("_meta", {}).setdefault("governance_violations", [])
        violations.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "field_path": field_path,
            "reason": reason,
        })
        # Save violation record
        try:
            self._save(state)
        except Exception as exc:
            logger.debug("violation-record state save failed (audit still recorded): %s", exc)

    def get_violation_count(self, agent_id: str) -> int:
        """Count governance violations for a specific agent."""
        state = self.load()
        violations = state.get("_meta", {}).get("governance_violations", [])
        return sum(1 for v in violations if v.get("agent_id") == agent_id)

    # --- OWNERSHIP PRE-FLIGHT HELPERS (ip-001) ---

    @staticmethod
    def owner_of(field_path: str) -> list[str]:
        """Return the list of agents authorized to write field_path.

        Use this before writing to avoid governance violations:
            owners = SharedStateManager.owner_of("capability.verification_results")
            # → ["evaluator_agent"]
        """
        rule = ACCESS_CONTROL.get(field_path)
        if rule is None:
            return []
        return list(rule.get("write", []))

    @staticmethod
    def can_write(agent_id: str, field_path: str) -> bool:
        """Pre-flight ownership check — call before sm.write() / sm.append().

        Returns True if agent_id is authorized.  When False, log the correct
        owner with owner_of() rather than writing anyway.

        Example:
            if not SharedStateManager.can_write("master_orchestrator", "capability.verification_results"):
                correct = SharedStateManager.owner_of("capability.verification_results")
                # use evaluation.performance_metrics (owned by evaluator_agent) instead
        """
        return is_authorized(agent_id, field_path)

    # --- INTERNAL ---

    def _save(self, state: dict) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            yaml.dump(state, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        try:
            from core.db import upsert_shared_state
            upsert_shared_state(self.project_id, state)
        except Exception as exc:
            logger.debug("shared-state DB upsert failed (non-blocking): %s", exc)


# --- CLI ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shared_state_manager",
        description="Shared State Manager CLI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser("init", help="Initialize a new project state")
    init_p.add_argument("--project-id", required=True)
    init_p.add_argument("--request-id", required=True)

    # read
    read_p = sub.add_parser("read", help="Read a field value")
    read_p.add_argument("--project-id", required=True)
    read_p.add_argument("--path", required=True, help="Dot-notation field path")

    # write
    write_p = sub.add_parser("write", help="Write a field value")
    write_p.add_argument("--project-id", required=True)
    write_p.add_argument("--section", required=True)
    write_p.add_argument("--field", required=True)
    write_p.add_argument("--value", help="String value")
    write_p.add_argument("--value-json", help="JSON value (overrides --value)")
    write_p.add_argument("--agent", required=True)

    # append
    append_p = sub.add_parser("append", help="Append to a list field")
    append_p.add_argument("--project-id", required=True)
    append_p.add_argument("--section", required=True)
    append_p.add_argument("--field", required=True)
    append_p.add_argument("--value-json", required=True, help="JSON item to append")
    append_p.add_argument("--agent", required=True)

    # approve
    approve_p = sub.add_parser("approve", help="Approve a field (makes it immutable)")
    approve_p.add_argument("--project-id", required=True)
    approve_p.add_argument("--section", required=True)
    approve_p.add_argument("--field", required=True)
    approve_p.add_argument("--agent", required=True)

    # mark-acceptance-met (prop-008-002): record delivery verification on an
    # already-approved acceptance criterion (text stays immutable).
    macm_p = sub.add_parser("mark-acceptance-met",
                            help="Mark an acceptance criterion met/evidence (allowed after approval)")
    macm_p.add_argument("--project-id", required=True)
    macm_p.add_argument("--criterion-id", required=True)
    macm_p.add_argument("--agent", required=True)
    macm_p.add_argument("--evidence", default=None)
    macm_p.add_argument("--not-met", action="store_true", help="Record met=false instead of true")

    # snapshot
    snap_p = sub.add_parser("snapshot", help="Snapshot current state at phase")
    snap_p.add_argument("--project-id", required=True)
    snap_p.add_argument("--phase", required=True)

    # show
    show_p = sub.add_parser("show", help="Print current state")
    show_p.add_argument("--project-id", required=True)

    # cleanup
    cleanup_p = sub.add_parser("cleanup", help="Remove snapshot files from project directory, keeping shared_state.yaml")
    cleanup_p.add_argument("--project-id", required=True)
    cleanup_p.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")

    return p


def main_cli(args=None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(args)
    sm = SharedStateManager(ns.project_id)

    if ns.command == "init":
        sm.initialize(ns.request_id)
        print(f"OK project {ns.project_id} initialized")

    elif ns.command == "read":
        val = sm.read(ns.path)
        print(yaml.dump(val, default_flow_style=False, allow_unicode=True))

    elif ns.command == "write":
        value = ns.value
        if ns.value_json:
            value = json.loads(ns.value_json)
        result = sm.write(ns.agent, ns.section, ns.field, value)
        if result.success:
            print("OK")
        else:
            print(f"DENIED: {result.reason}", file=sys.stderr)
            return 1

    elif ns.command == "append":
        item = json.loads(ns.value_json)
        result = sm.append(ns.agent, ns.section, ns.field, item)
        if result.success:
            print("OK")
        else:
            print(f"DENIED: {result.reason}", file=sys.stderr)
            return 1

    elif ns.command == "approve":
        result = sm.approve(ns.agent, ns.section, ns.field)
        if result.success:
            print("OK")
        else:
            print(f"DENIED: {result.reason}", file=sys.stderr)
            return 1

    elif ns.command == "mark-acceptance-met":
        result = sm.mark_acceptance_met(ns.agent, ns.criterion_id,
                                        met=not ns.not_met, evidence=ns.evidence)
        if result.success:
            print("OK")
        else:
            print(f"DENIED: {result.reason}", file=sys.stderr)
            return 1

    elif ns.command == "snapshot":
        path = sm.snapshot(ns.phase)
        print(f"OK snapshot saved: {path}")

    elif ns.command == "show":
        state = sm.load()
        print(yaml.dump(state, default_flow_style=False, allow_unicode=True, sort_keys=False))

    elif ns.command == "cleanup":
        project_dir = sm.project_dir
        snapshots = sorted(project_dir.glob("shared_state_snapshot_*.yaml"))
        if not snapshots:
            print(f"Nothing to clean up in {project_dir}")
            return 0
        if ns.dry_run:
            print(f"[dry-run] Would delete {len(snapshots)} snapshot(s) from {project_dir}:")
            for f in snapshots:
                print(f"  {f.name}")
        else:
            for f in snapshots:
                f.unlink()
            print(f"Deleted {len(snapshots)} snapshot(s) from {project_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
