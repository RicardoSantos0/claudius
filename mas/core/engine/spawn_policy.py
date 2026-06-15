"""
Spawn Policy Engine (migrated implementation)

Provides: SpawnPolicyEngine, supporting helpers, and package builder logic.

This module is a moved copy of the original `mas/core/spawn_policy.py` implementation
adjusted to expose a `main()` entrypoint for wrapper delegation.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SPAWNS_PER_PROJECT: int = 3
MAX_SPAWNS_PER_PHASE: int = 1
SPAWN_HISTORY_FILE: str = "spawn_history.yaml"

DENY = "do_not_spawn"
DRAFT = "spawn_draft_only"
VERIFY = "spawn_and_verify"

_WORTHINESS_KEYS = ("bounded", "recurring", "verifiable", "no_existing_match")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PolicyViolation:
    code: str          # machine-readable rule code
    message: str       # human-readable explanation


@dataclass
class LimitCheckResult:
    within_project_limit: bool
    within_phase_limit: bool
    project_spawn_count: int
    phase_spawn_count: int
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.within_project_limit and self.within_phase_limit


@dataclass
class CertificateCheckResult:
    certificate_present: bool
    master_approved: bool
    certificate_id: str = ""
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.certificate_present and self.master_approved


@dataclass
class RecursiveSpawnCheckResult:
    requester_is_spawned: bool   # True means BLOCKED
    requester_agent_id: str
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.requester_is_spawned


@dataclass
class WorthinessResult:
    bounded: bool
    recurring: bool
    verifiable: bool
    no_existing_match: bool
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.bounded and self.recurring and self.verifiable and self.no_existing_match


@dataclass
class ValidationResult:
    decision: str                          # DENY | DRAFT | VERIFY
    rationale: str
    limit_check: LimitCheckResult
    certificate_check: CertificateCheckResult
    recursive_check: RecursiveSpawnCheckResult
    worthiness: WorthinessResult
    all_violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.decision != DENY


# ---------------------------------------------------------------------------
# Spawn history helpers
# ---------------------------------------------------------------------------

def _load_history(project_dir: Path) -> dict:
    path = project_dir / "spawner" / SPAWN_HISTORY_FILE
    if not path.exists():
        return {"spawns": []}
    with open(path) as f:
        return yaml.safe_load(f) or {"spawns": []}


def _save_history(project_dir: Path, history: dict) -> None:
    d = project_dir / "spawner"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / SPAWN_HISTORY_FILE, "w") as f:
        yaml.dump(history, f, default_flow_style=False, sort_keys=False)


def record_spawn(
    project_dir: Path,
    spawn_request_id: str,
    agent_id: str,
    phase: str,
    decision: str,
    package_path: Optional[str] = None,
) -> dict:
    """Append a spawn event to the project's spawn history. Returns the record."""
    history = _load_history(project_dir)
    record = {
        "spawn_id": f"spawn-{uuid.uuid4().hex[:8]}",
        "spawn_request_id": spawn_request_id,
        "agent_id": agent_id,
        "phase": phase,
        "decision": decision,
        "package_path": package_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    history["spawns"].append(record)
    _save_history(project_dir, history)
    return record


# ---------------------------------------------------------------------------
# SpawnPolicyEngine
# ---------------------------------------------------------------------------

class SpawnPolicyEngine:
    """
    Enforces all spawn governance rules.

    Usage:
        engine = SpawnPolicyEngine()
        result = engine.validate(spawn_request, registry_data, project_dir)
    """

    # ------------------------------------------------------------------
    # Limit checks
    # ------------------------------------------------------------------

    def check_limits(
        self,
        phase: str,
        spawn_history: dict,
    ) -> LimitCheckResult:
        spawns = spawn_history.get("spawns", [])
        project_count = len(spawns)
        phase_count = sum(1 for s in spawns if s.get("phase") == phase)

        violations: list[PolicyViolation] = []

        within_project = project_count < MAX_SPAWNS_PER_PROJECT
        within_phase = phase_count < MAX_SPAWNS_PER_PHASE

        if not within_project:
            violations.append(PolicyViolation(
                code="LIMIT_PROJECT_EXCEEDED",
                message=(
                    f"Project spawn limit reached: {project_count}/{MAX_SPAWNS_PER_PROJECT}. "
                    "No further spawns allowed this project."
                ),
            ))
        if not within_phase:
            violations.append(PolicyViolation(
                code="LIMIT_PHASE_EXCEEDED",
                message=(
                    f"Phase '{phase}' already has {phase_count} spawn(s). "
                    f"Maximum 1 spawn per phase."
                ),
            ))

        return LimitCheckResult(
            within_project_limit=within_project,
            within_phase_limit=within_phase,
            project_spawn_count=project_count,
            phase_spawn_count=phase_count,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Certificate checks
    # ------------------------------------------------------------------

    def check_certificate(
        self,
        spawn_request: dict,
        gap_cert: Optional[dict],
    ) -> CertificateCheckResult:
        violations: list[PolicyViolation] = []

        cert_id = spawn_request.get("gap_certificate_id", "")
        cert_present = gap_cert is not None and bool(cert_id)

        if not cert_present:
            violations.append(PolicyViolation(
                code="CERT_MISSING",
                message=(
                    "No Capability Gap Certificate attached. "
                    "A Master-approved certificate is required before spawning."
                ),
            ))
            return CertificateCheckResult(
                certificate_present=False,
                master_approved=False,
                certificate_id=cert_id,
                violations=violations,
            )

        # Check master_approval field on the spawn_request
        master_approved = bool(spawn_request.get("master_approval", False))

        # Also validate gap_cert itself if available
        if gap_cert:
            cert_status = gap_cert.get("status", "") or gap_cert.get("approval_status", "")
            if cert_status not in ("approved", "master_approved"):
                master_approved = False

        if not master_approved:
            violations.append(PolicyViolation(
                code="CERT_NOT_APPROVED",
                message=(
                    f"Certificate '{cert_id}' has not been approved by Master Orchestrator. "
                    "Set master_approval=true on the spawn request after Master signs off."
                ),
            ))

        return CertificateCheckResult(
            certificate_present=cert_present,
            master_approved=master_approved,
            certificate_id=cert_id,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Recursive spawn check
    # ------------------------------------------------------------------

    def check_recursive_spawn(
        self,
        requester_agent_id: str,
        registry_data: dict,
    ) -> RecursiveSpawnCheckResult:
        violations: list[PolicyViolation] = []

        agents = registry_data.get("registry", {}).get("agents", [])
        requester = next(
            (a for a in agents if a["agent_id"] == requester_agent_id), None
        )

        # Spawner self-spawn check
        if requester_agent_id == "spawner_agent":
            violations.append(PolicyViolation(
                code="RECURSIVE_SELF_SPAWN",
                message="spawner_agent cannot spawn itself.",
            ))
            return RecursiveSpawnCheckResult(
                requester_is_spawned=True,
                requester_agent_id=requester_agent_id,
                violations=violations,
            )

        if requester is None:
            # Unknown agent — treat as safe (not a spawned agent on record)
            return RecursiveSpawnCheckResult(
                requester_is_spawned=False,
                requester_agent_id=requester_agent_id,
            )

        # Check spawn_origin — non-null means this agent was itself spawned
        spawn_origin = requester.get("spawn_origin")
        is_spawned = spawn_origin is not None

        if is_spawned:
            violations.append(PolicyViolation(
                code="RECURSIVE_SPAWN_BLOCKED",
                message=(
                    f"Agent '{requester_agent_id}' was itself spawned (origin: '{spawn_origin}'). "
                    "Recursive spawning is not allowed."
                ),
            ))

        return RecursiveSpawnCheckResult(
            requester_is_spawned=is_spawned,
            requester_agent_id=requester_agent_id,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Worthiness
    # ------------------------------------------------------------------

    def check_worthiness(
        self,
        spawn_request: dict,
        gap_cert: Optional[dict],
    ) -> WorthinessResult:
        """
        Evaluate the four worthiness criteria from spawn_policy.yaml.
        Falls back to heuristics when explicit flags are absent.
        """
        violations: list[PolicyViolation] = []
        flags: dict[str, bool] = {}

        # Source: explicit worthiness block on spawn_request
        worthiness_block = spawn_request.get("worthiness", {}) or {}
        for key in _WORTHINESS_KEYS:
            flags[key] = bool(worthiness_block.get(key, False))

        # Heuristic: no_existing_match can be inferred from gap cert
        if gap_cert and not flags["no_existing_match"]:
            # If gap cert has status=approved, HR verified no strong match
            cert_status = gap_cert.get("status", "") or gap_cert.get("approval_status", "")
            if cert_status in ("approved", "master_approved"):
                flags["no_existing_match"] = True

        # Heuristic: bounded — required_inputs and required_outputs must be non-empty
        if not flags["bounded"]:
            has_inputs = bool(spawn_request.get("required_inputs"))
            has_outputs = bool(spawn_request.get("required_outputs"))
            flags["bounded"] = has_inputs and has_outputs

        # Heuristic: verifiable — allowed_tools or scope must be defined
        if not flags["verifiable"]:
            flags["verifiable"] = bool(spawn_request.get("allowed_tools")) or \
                                   bool(spawn_request.get("scope"))

        # Record violations for failed criteria
        for key in _WORTHINESS_KEYS:
            if not flags[key]:
                violations.append(PolicyViolation(
                    code=f"WORTHINESS_{key.upper()}_FAILED",
                    message=f"Worthiness criterion '{key}' not satisfied.",
                ))

        return WorthinessResult(
            bounded=flags["bounded"],
            recurring=flags["recurring"],
            verifiable=flags["verifiable"],
            no_existing_match=flags["no_existing_match"],
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Full validation
    # ------------------------------------------------------------------

    def validate(
        self,
        spawn_request: dict,
        registry_data: dict,
        project_dir: Path,
        gap_cert: Optional[dict] = None,
        *,
        phase: Optional[str] = None,
    ) -> ValidationResult:
        """
        Run all checks and return a ValidationResult with a decision.

        Decision logic:
          - Any hard violation → DENY
          - All checks pass, verification_required=True → DRAFT (default)
          - All checks pass, verification_required=False (rare) → DRAFT (we always draft)
        """
        if phase is None:
            phase = spawn_request.get("phase", "unknown")

        # Lite mode: spawning is not available — return immediate DENY
        state_file = project_dir / "shared_state.yaml"
        if state_file.exists():
            try:
                import yaml as _yaml
                with open(state_file, encoding="utf-8") as _f:
                    _state = _yaml.safe_load(_f) or {}
                if _state.get("workflow", {}).get("mode") == "lite":
                    lite_violation = PolicyViolation(
                        code="LITE_MODE_NO_SPAWN",
                        message=(
                            "Spawning is disabled in lite-mode projects. "
                            "Use 'mas init' (without --mode=lite) for projects that require agents."
                        ),
                    )
                    _empty_limit = LimitCheckResult(
                        within_project_limit=False,
                        within_phase_limit=False,
                        project_spawn_count=0,
                        phase_spawn_count=0,
                        violations=[lite_violation],
                    )
                    _empty_cert = CertificateCheckResult(
                        certificate_present=False,
                        master_approved=False,
                        violations=[],
                    )
                    _empty_rec = RecursiveSpawnCheckResult(
                        requester_is_spawned=False,
                        requester_agent_id="",
                        violations=[],
                    )
                    _empty_worth = WorthinessResult(
                        bounded=False, recurring=False,
                        verifiable=False, no_existing_match=False,
                        violations=[],
                    )
                    return ValidationResult(
                        decision=DENY,
                        rationale=lite_violation.message,
                        limit_check=_empty_limit,
                        certificate_check=_empty_cert,
                        recursive_check=_empty_rec,
                        worthiness=_empty_worth,
                        all_violations=[lite_violation],
                    )
            except Exception as exc:
                logger.debug("state read failed; continuing with normal validation: %s", exc)

        requester = spawn_request.get("requested_by", "")
        history = _load_history(project_dir)

        limit_result = self.check_limits(phase, history)
        cert_result = self.check_certificate(spawn_request, gap_cert)
        recursive_result = self.check_recursive_spawn(requester, registry_data)
        worthiness_result = self.check_worthiness(spawn_request, gap_cert)

        all_violations = (
            limit_result.violations
            + cert_result.violations
            + recursive_result.violations
            + worthiness_result.violations
        )

        hard_checks = [limit_result, cert_result, recursive_result]
        hard_passed = all(c.passed for c in hard_checks)

        if not hard_passed:
            decision = DENY
            rationale = _build_deny_rationale(all_violations)
        elif not worthiness_result.passed:
            decision = DENY
            rationale = (
                "Spawn denied: worthiness criteria not fully met. "
                + "; ".join(v.message for v in worthiness_result.violations)
            )
        else:
            # Always draft-only in v1
            decision = DRAFT
            rationale = (
                "All policy checks passed. Agent package will be drafted for human review. "
                "No automatic deployment."
            )

        return ValidationResult(
            decision=decision,
            rationale=rationale,
            limit_check=limit_result,
            certificate_check=cert_result,
            recursive_check=recursive_result,
            worthiness=worthiness_result,
            all_violations=all_violations,
        )


def _build_deny_rationale(violations: list[PolicyViolation]) -> str:
    if not violations:
        return "Spawn denied: policy check failed."
    parts = [f"[{v.code}] {v.message}" for v in violations]
    return "Spawn denied: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Agent package builder
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_TEMPLATE_MAP = {
    "execution_agent": "execution_agent_template.yaml",
    "analysis_agent": "analysis_agent_template.yaml",
    "utility_agent": "utility_agent_template.yaml",
}


def build_agent_package(
    spawn_request: dict,
    project_dir: Path,
) -> Path:
    """
    Produce a draft agent package from a spawn request.
    Writes to projects/{project_id}/spawner/packages/{agent_id}/
    Returns the package directory path.
    """
    base_template_key = spawn_request.get("base_template") or "utility_agent"
    template_file = TEMPLATES_DIR / _TEMPLATE_MAP.get(
        base_template_key, "utility_agent_template.yaml"
    )

    template: dict = {}
    if template_file.exists():
        with open(template_file) as f:
            template = yaml.safe_load(f) or {}

    agent_purpose = spawn_request.get("agent_purpose", "")
    # Derive a safe agent_id from the purpose
    raw = re.sub(r"[^a-z0-9 ]", "", agent_purpose.lower())
    words = raw.split()[:4]
    agent_id = "_".join(words) + "_agent" if words else "spawned_agent"

    package_dir = project_dir / "spawner" / "packages" / agent_id
    package_dir.mkdir(parents=True, exist_ok=True)

    # --- manifest.yaml ---
    manifest = {
        "agent_id": agent_id,
        "spawn_request_id": spawn_request.get("request_id", ""),
        "gap_certificate_id": spawn_request.get("gap_certificate_id", ""),
        "base_template": base_template_key,
        "status": "draft",
        "trust_tier": "T3_provisional",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verification_status": "pending",
        "agent_purpose": agent_purpose,
        "required_inputs": spawn_request.get("required_inputs", []),
        "required_outputs": spawn_request.get("required_outputs", []),
        "allowed_tools": spawn_request.get("allowed_tools", []),
        "scope": spawn_request.get("scope", "project_scoped"),
        "capabilities": _derive_capabilities(spawn_request),
    }

    with open(package_dir / "manifest.yaml", "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    # --- agent_definition.md ---
    definition = _render_agent_definition(agent_id, agent_purpose, spawn_request, template)
    with open(package_dir / "agent_definition.md", "w") as f:
        f.write(definition)

    # --- tool_contract.yaml ---
    tool_contract = {
        "agent_id": agent_id,
        "allowed_tools": spawn_request.get("allowed_tools", []),
        "forbidden_tools": ["spawn", "retire"],
        "tool_scope": "project_scoped",
        "note": "Tool contract generated from spawn request. Review before promoting.",
    }
    with open(package_dir / "tool_contract.yaml", "w") as f:
        yaml.dump(tool_contract, f, default_flow_style=False, sort_keys=False)

    # --- verification_plan.yaml ---
    verification_plan = _build_verification_plan(agent_id, spawn_request)
    with open(package_dir / "verification_plan.yaml", "w") as f:
        yaml.dump(verification_plan, f, default_flow_style=False, sort_keys=False)

    # --- behavioral_contract.yaml ---
    behavioral_contract = {
        "agent_id": agent_id,
        "trust_tier": "T3_provisional",
        "authority": {
            "can_do": _derive_capabilities(spawn_request),
            "cannot_do": [
                "spawn other agents",
                "approve own outputs",
                "write to decisions.approvals",
                "modify other agents",
            ],
        },
        "escalation_triggers": [
            "Any output uncertainty > 20%",
            "Any unexpected input format",
            "Any tool failure",
        ],
        "governance": {
            "requires_master_approval_for": ["any permanent action"],
            "reports_to": "master_orchestrator",
        },
    }
    with open(package_dir / "behavioral_contract.yaml", "w") as f:
        yaml.dump(behavioral_contract, f, default_flow_style=False, sort_keys=False)

    return package_dir


def _derive_capabilities(spawn_request: dict) -> list[str]:
    """Extract capability tags from the spawn request purpose and outputs."""
    purpose = spawn_request.get("agent_purpose", "").lower()
    outputs = spawn_request.get("required_outputs", [])

    caps: list[str] = []
    # Simple keyword-to-capability mapping
    mapping = {
        "report": "reporting",
        "analys": "analysis",
        "data": "data-processing",
        "integrat": "integration",
        "transform": "data-transformation",
        "generat": "generation",
        "test": "testing",
        "verif": "verification",
        "monitor": "monitoring",
        "notif": "notification",
        "export": "export",
        "import": "import",
        "schedul": "scheduling",
        "summar": "summarization",
    }
    text = purpose + " " + " ".join(str(o) for o in outputs)
    for kw, cap in mapping.items():
        if kw in text and cap not in caps:
            caps.append(cap)

    if not caps:
        caps.append("utility")

    return caps


def _render_agent_definition(
    agent_id: str,
    purpose: str,
    spawn_request: dict,
    template: dict,
) -> str:
    inputs = spawn_request.get("required_inputs", [])
    outputs = spawn_request.get("required_outputs", [])
    tools = spawn_request.get("allowed_tools", [])
    scope = spawn_request.get("scope", "project_scoped")

    inputs_md = "\n".join(f"- {i}" for i in inputs) if inputs else "- (none specified)"
    outputs_md = "\n".join(f"- {o}" for o in outputs) if outputs else "- (none specified)"
    tools_md = ", ".join(f"`{t}`" for t in tools) if tools else "(none specified)"

    return f"""---
name: {agent_id}
description: "DRAFT — {purpose}. Generated by Spawner. Requires human review before activation."
tools: [{", ".join(tools)}]
user-invocable: false
status: draft
trust_tier: T3_provisional
---

> **DRAFT AGENT — NOT ACTIVE**
> This agent definition was generated by the Spawner Agent.
> It must be reviewed, verified, and promoted by a human before use.

You are the **{agent_id.replace("_", " ").title()}**.

## Identity
- Agent ID: `{agent_id}`
- Trust Tier: T3_provisional (draft)
- Scope: {scope}
- Status: **DRAFT — pending verification**

## Mission
{purpose}

## Inputs
{inputs_md}

## Outputs
{outputs_md}

## Allowed Tools
{tools_md}

## Authority Boundaries
- You CANNOT spawn other agents
- You CANNOT approve your own outputs
- You CANNOT write to `decisions.approvals`
- Escalate to Master on any uncertainty or failure

## Escalation Triggers
- Unexpected input format or missing data
- Any tool failure
- Output confidence below 80%
- Any action outside the scope above

## Governance
- Reports to: `master_orchestrator`
- Requires Master approval for any permanent action
- All outputs are provisional until Master-reviewed

---
*Generated from spawn request `{spawn_request.get("request_id", "unknown")}` via gap certificate `{spawn_request.get("gap_certificate_id", "unknown")}`.*
"""


def _build_verification_plan(agent_id: str, spawn_request: dict) -> dict:
    outputs = spawn_request.get("required_outputs", [])
    return {
        "agent_id": agent_id,
        "verifier": "evaluator_agent",
        "status": "pending",
        "verification_steps": [
            {
                "step": 1,
                "description": "Provide sample input and verify output format matches required_outputs",
                "expected": outputs,
                "pass_criteria": "All required outputs produced with correct structure",
            },
            {
                "step": 2,
                "description": "Verify agent respects tool contract (only uses allowed_tools)",
                "pass_criteria": "No calls to forbidden tools",
            },
            {
                "step": 3,
                "description": "Verify governance compliance (no writes to forbidden fields)",
                "pass_criteria": "Zero governance violations in test run",
            },
            {
                "step": 4,
                "description": "Human review of agent definition and behavioral contract",
                "pass_criteria": "Human approves for promotion to T2_supervised or higher",
            },
        ],
        "promotion_criteria": {
            "to_T2_supervised": "Steps 1-3 pass + human review complete",
            "to_T1_established": "Requires 3+ successful projects + Trainer evaluation",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) < 1:
        print("Usage: python core/spawn_policy.py <validate|history> [options]")
        return 1

    cmd = argv[0]
    rest = argv[1:]

    if cmd == "validate":
        _cli_validate(rest)
    elif cmd == "history":
        _cli_history(rest)
    else:
        print(f"Unknown command: {cmd}")
        return 1

    return 0


def _cli_validate(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="spawn_policy validate")
    p.add_argument("--project-id", required=True)
    p.add_argument("--request-file", required=True, help="Path to spawn request YAML")
    p.add_argument("--cert-file", default=None, help="Path to gap certificate YAML")
    p.add_argument("--projects-root", default="projects")
    ns = p.parse_args(args)

    with open(ns.request_file) as f:
        spawn_request = yaml.safe_load(f)

    gap_cert = None
    if ns.cert_file:
        with open(ns.cert_file) as f:
            gap_cert = yaml.safe_load(f)

    # Load registry
    registry_path = Path("roster/registry_index.yaml")
    registry_data: dict = {}
    if registry_path.exists():
        with open(registry_path) as f:
            registry_data = yaml.safe_load(f) or {}

    projects_root = Path(ns.projects_root)
    project_dir = projects_root / ns.project_id

    engine = SpawnPolicyEngine()
    result = engine.validate(spawn_request, registry_data, project_dir, gap_cert)

    print(f"Decision   : {result.decision}")
    print(f"Rationale  : {result.rationale}")
    if result.all_violations:
        print("\nViolations:")
        for v in result.all_violations:
            print(f"  [{v.code}] {v.message}")
    else:
        print("Violations : none")


def _cli_history(args: list[str]) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="spawn_policy history")
    p.add_argument("--project-id", required=True)
    p.add_argument("--projects-root", default="projects")
    ns = p.parse_args(args)

    project_dir = Path(ns.projects_root) / ns.project_id
    history = _load_history(project_dir)
    spawns = history.get("spawns", [])

    if not spawns:
        print(f"No spawns recorded for project '{ns.project_id}'.")
        return

    print(f"Spawn history for project '{ns.project_id}' ({len(spawns)} total):\n")
    for s in spawns:
        print(
            f"  [{s.get('spawn_id')}] {s.get('agent_id')} — "
            f"{s.get('decision')} — phase: {s.get('phase')} — {s.get('timestamp')}"
        )


if __name__ == "__main__":
    sys.exit(main())
