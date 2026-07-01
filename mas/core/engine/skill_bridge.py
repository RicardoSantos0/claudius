"""
Skill Bridge (migrated)

Gateway between MAS agents and the skills/ repository.
"""

from __future__ import annotations

import sys
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

try:
    from core.utils.token_counter import TokenCounter as _TokenCounter
    _tc = _TokenCounter()
except ImportError:
    _tc = None  # type: ignore

try:
    from core.utils.log_helpers import DB_PATH as _DB_PATH, _get_connection as _db_connect
except Exception:
    _DB_PATH = None  # type: ignore
    _db_connect = None  # type: ignore

from core.paths import mas_root
ROOT = mas_root()  # mas/
REPO_ROOT = ROOT.parent                    # repo root (holds skills/)
SKILLS_DIR = REPO_ROOT / "skills"

# Attribution decided in proj-YYYYMMDD-NNN-skill-attribution (lite MAS).
# See mas/projects/proj-YYYYMMDD-NNN-skill-attribution/planning/product_plan.yaml
# for per-agent rationale.
SKILL_ACCESS: dict[str, list[str]] = {
    "master_orchestrator": ["*"],
    "scribe_agent": [
        "research-extract", "research-sync", "mas-document", "mas-handoff",
        "writing-guidelines",
    ],
    "inquirer_agent": [
        "research-extract", "mas-clarify",
        "adaptive-communication",
    ],
    "product_manager_agent": [
        "research-extract", "research-sync", "mas-clarify",
        "adaptive-communication", "writing-guidelines", "human-architect-mindset",
    ],
    # graphify = recon: navigate/query the target folder before decomposing execution.
    "project_manager_agent": [
        "research-extract", "mas-plan", "mas-examine", "graphify",
        "human-architect-mindset", "negentropy-lens",
    ],
    "hr_agent": [],
    "evaluator_agent": [
        "research-extract", "mas-postmortem",
        "vanity-engineering-review", "negentropy-lens",
    ],
    # skill-builder = on-demand skill creation/optimization, the natural home for
    # "we keep re-doing X -> make it a skill" improvement proposals.
    "trainer_agent": [
        "mas-postmortem", "skill-builder",
        "find-skills", "renaissance-architecture", "vanity-engineering-review",
    ],
    "spawner_agent": [
        "skill-builder",
        "find-skills", "mas-examine",
    ],
    "risk_advisor": [
        "mas-examine",
        "negentropy-lens",
    ],
    "quality_advisor": [
        "mas-examine",
        "writing-guidelines", "vanity-engineering-review",
        "design-audit", "ui-typography", "web-design-guidelines",
    ],
    "devils_advocate": [
        "vanity-engineering-review", "negentropy-lens",
    ],
    # graphify = grounded codebase/architecture comprehension for domain reasoning.
    # NotebookLM grounding for this agent stays brokered via master_orchestrator (see
    # domain_expert.md "Knowledge Retrieval"), so no direct notebooklm grant here.
    "domain_expert": [
        "research-extract", "mas-examine", "graphify",
        "human-architect-mindset", "renaissance-architecture", "adaptive-communication",
        "agentic-ux-design-relationship-centric-interfaces",
    ],
    "efficiency_advisor": [
        "vanity-engineering-review", "negentropy-lens",
    ],
    "session_scheduler": ["mas-review", "mas-handoff", "mas-logwork"],

    # ---- Delivery engineers (previously omitted -> silently denied all) ----
    "canonical_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
    ],
    "analysis_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
    ],
    "integration_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
    ],
    "reliability_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
    ],
    "ml_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
    ],
    "nlp_taxonomy_specialist": [
        "mas-examine", "graphify", "mas-logwork",
    ],
    "librarian_agent": [
        "mas-examine",
    ],

    # ---- Specialist agents added 2026-07-01 ----
    "appsec_specialist_agent": [
        "mas-examine", "graphify", "vanity-engineering-review",
    ],
    "backend_platform_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "webapp-delivery", "frontend-design",
        "deploy-to-vercel", "vercel-cli-with-tokens", "vercel-optimize",
        "vercel-composition-patterns", "vercel-react-best-practices",
        "vercel-react-view-transitions",
    ],
}


class SkillMetadata:
    def __init__(self, name: str, description: str, path: Path):
        self.name = name
        self.description = description
        self.path = path

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "path": str(self.path)}


class InvocationResult:
    def __init__(
        self,
        success: bool,
        skill_name: str,
        agent_id: str,
        outcome: str,
        message: str = "",
        tokens_used: int = 0,
        audit_entry: dict | None = None,
    ):
        self.success = success
        self.skill_name = skill_name
        self.agent_id = agent_id
        self.outcome = outcome
        self.message = message
        self.tokens_used = tokens_used
        self.audit_entry = audit_entry or {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "skill_name": self.skill_name,
            "agent_id": self.agent_id,
            "outcome": self.outcome,
            "message": self.message,
            "tokens_used": self.tokens_used,
        }


class SkillBridge:
    def __init__(self, skills_dir: Path = SKILLS_DIR,
                 projects_root: Path | None = None):
        self.skills_dir = skills_dir
        # Where per-project skill_audit_log.yaml files are written. Defaults to the
        # real mas/projects/ dir; tests inject a tmp_path so auditing never pollutes
        # the real projects tree (ip-rm-002).
        self.projects_root = projects_root or (ROOT / "projects")
        self._cache: dict[str, SkillMetadata] | None = None

    def _db_skills(self) -> list[dict]:
        """Query mas_skills table for active skills. Returns [] on any error."""
        if _db_connect is None or _DB_PATH is None:
            return []
        try:
            with _db_connect(_DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT skill_id, name, description, trigger_pattern, skill_path, metadata"
                    " FROM mas_skills WHERE status = 'active'"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def discover(self, force_refresh: bool = False) -> list[SkillMetadata]:
        if self._cache is not None and not force_refresh:
            return list(self._cache.values())

        found: dict[str, SkillMetadata] = {}

        db_rows = self._db_skills() if self.skills_dir == SKILLS_DIR else []
        if db_rows:
            for row in db_rows:
                skill_path = REPO_ROOT / row["skill_path"]
                name = row["name"] or row["skill_id"]
                description = row.get("description") or ""
                found[name] = SkillMetadata(name=name, description=description, path=skill_path)
            self._cache = found
            return list(found.values())

        # Filesystem fallback
        if not self.skills_dir.exists():
            self._cache = {}
            return []

        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta = self._parse_skill_md(skill_md)
            if meta:
                found[meta.name] = meta

        self._cache = found
        return list(found.values())

    def get_skill(self, skill_name: str) -> SkillMetadata | None:
        if self._cache is None:
            self.discover()
        return self._cache.get(skill_name)  # type: ignore[union-attr]

    def is_skill_authorized(self, agent_id: str, skill_name: str) -> bool:
        allowed = SKILL_ACCESS.get(agent_id)
        if allowed is None:
            return False
        if "*" in allowed:
            return True
        return skill_name in allowed

    def authorized_skills(self, agent_id: str) -> list[SkillMetadata]:
        all_skills = self.discover()
        if agent_id not in SKILL_ACCESS:
            return []
        allowed = SKILL_ACCESS[agent_id]
        if "*" in allowed:
            return all_skills
        return [s for s in all_skills if s.name in allowed]

    def invoke(
        self,
        agent_id: str,
        skill_name: str,
        query: str,
        project_id: str = "",
    ) -> InvocationResult:
        timestamp = datetime.now(timezone.utc).isoformat()
        tokens_used = _tc.count(query) if _tc else 0

        if not self.is_skill_authorized(agent_id, skill_name):
            audit = self._make_audit(
                agent_id, skill_name, query, project_id,
                outcome="denied", tokens_used=0, timestamp=timestamp,
            )
            self._persist_invocation_event(project_id, audit, "skill_skipped",
                                           "Skill invocation denied")
            return InvocationResult(
                success=False,
                skill_name=skill_name,
                agent_id=agent_id,
                outcome="denied",
                message=f"Agent '{agent_id}' is not authorized to invoke skill '{skill_name}'.",
                tokens_used=0,
                audit_entry=audit,
            )

        skill_meta = self.get_skill(skill_name)
        if skill_meta is None:
            audit = self._make_audit(
                agent_id, skill_name, query, project_id,
                outcome="skill_not_found", tokens_used=0, timestamp=timestamp,
            )
            self._persist_invocation_event(project_id, audit, "skill_skipped",
                                           "Skill not found")
            return InvocationResult(
                success=False,
                skill_name=skill_name,
                agent_id=agent_id,
                outcome="skill_not_found",
                message=f"Skill '{skill_name}' not found in {self.skills_dir}.",
                tokens_used=0,
                audit_entry=audit,
            )

        audit = self._make_audit(
            agent_id, skill_name, query, project_id,
            outcome="ok", tokens_used=tokens_used, timestamp=timestamp,
        )
        self._persist_invocation_event(project_id, audit, "skill_invoked",
                                       "Skill invocation authorized")

        return InvocationResult(
            success=True,
            skill_name=skill_name,
            agent_id=agent_id,
            outcome="ok",
            message=(
                f"Skill '{skill_name}' authorized for agent '{agent_id}'. "
                f"Invoke via: /{skill_name} {query}"
            ),
            tokens_used=tokens_used,
            audit_entry=audit,
        )

    def get_audit_log(self, project_id: str) -> list[dict]:
        log_path = self._audit_path(project_id)
        if not log_path.exists():
            return []
        with log_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("entries", [])

    def write_audit_entry(self, project_id: str, entry: dict) -> None:
        if not project_id:
            return
        log_path = self._audit_path(project_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        existing = self.get_audit_log(project_id)
        existing.append(entry)

        data = {
            "project_id": project_id,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "entries": existing,
        }
        with log_path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def _parse_skill_md(self, path: Path) -> SkillMetadata | None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        if not content.startswith("---"):
            return None

        end = content.find("---", 3)
        if end == -1:
            return None

        frontmatter = content[3:end].strip()
        try:
            meta = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            return None

        name = meta.get("name") or path.parent.name
        description = meta.get("description", "")
        return SkillMetadata(name=name, description=description, path=path)

    def _make_audit(
        self,
        agent_id: str,
        skill_name: str,
        query: str,
        project_id: str,
        outcome: str,
        tokens_used: int,
        timestamp: str,
    ) -> dict:
        return {
            "timestamp": timestamp,
            "agent_id": agent_id,
            "skill_name": skill_name,
            "project_id": project_id or "unknown",
            "query_preview": query[:100] + ("..." if len(query) > 100 else ""),
            "outcome": outcome,
            "tokens_used": tokens_used,
        }

    def render_skill_prompt(self, agent_id: str, skill_name: str, query: str,
                            project_id: str = "") -> str:
        """
        Render a skill invocation as an executable prompt block.
        Returns a markdown block the agent can act on, or an error string.
        Never raises.
        """
        if not self.is_skill_authorized(agent_id, skill_name):
            return f"[skill denied: {skill_name!r} not authorized for {agent_id!r}]"
        skill = self.get_skill(skill_name)
        if skill is None:
            return f"[skill not found: {skill_name!r}]"
        try:
            skill_text = skill.path.read_text(encoding="utf-8")
        except OSError:
            skill_text = f"# {skill.name}\n\n{skill.description}"
        return (
            "You are executing the following Claude Code skill.\n\n"
            f"# Skill\n{skill_text}\n\n"
            f"# Project\n{project_id or '(none)'}\n\n"
            f"# Query\n{query}\n\n"
            "Follow the skill procedure exactly. Return the skill output using the skill's Output Format.\n"
        )

    def _persist_invocation_event(
        self,
        project_id: str,
        audit: dict,
        action_type: str,
        intent: str,
    ) -> None:
        if not project_id:
            return
        try:
            self.write_audit_entry(project_id, audit)
        except Exception as exc:
            logger.debug("skill audit write failed (non-blocking): %s", exc)
        try:
            from core.engine.event_recorder import EventRecorder
            EventRecorder().record_simple(
                project_id=project_id,
                actor=audit.get("agent_id", "unknown"),
                action_type=action_type,
                intent=intent,
                payload=audit,
            )
        except Exception as exc:
            logger.debug("skill audit event recording failed (non-blocking): %s", exc)

    def audit_handoff(self, handoff: dict) -> None:
        """
        Called by handoff_engine after every handoff creation.
        Checks if any artifact in the payload matches a registered skill output
        and appends a record to the project's skill_audit_log.yaml.
        Non-fatal — never raises.
        """
        project_id = handoff.get("project_id", "")
        if not project_id:
            return
        artifacts = handoff.get("payload", {}).get("artifacts_produced", [])
        if not artifacts:
            return
        skills = self.discover()
        skill_names = {s.name for s in skills}
        for artifact in artifacts:
            artifact_str = str(artifact)
            matched = [sn for sn in skill_names if sn in artifact_str]
            if matched:
                entry = self._make_audit(
                    agent_id=handoff.get("from_agent", "unknown"),
                    skill_name=matched[0],
                    query=artifact_str,
                    project_id=project_id,
                    outcome="artifact_match",
                    tokens_used=0,
                    timestamp=handoff.get("timestamp", ""),
                )
                entry["handoff_id"] = handoff.get("handoff_id", "")
                self.write_audit_entry(project_id, entry)

    def _audit_path(self, project_id: str) -> Path:
        return self.projects_root / project_id / "skill_audit_log.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Skill Bridge — MAS agent-to-skills gateway",
        epilog="uv run python mas/core/skill_bridge.py discover",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("discover", help="List all discovered skills")

    inv = sub.add_parser("invoke", help="Simulate a skill invocation")
    inv.add_argument("--agent", required=True, help="Agent ID")
    inv.add_argument("--skill", required=True, help="Skill name")
    inv.add_argument("--query", required=True, help="Query string")
    inv.add_argument("--project-id", default="", help="Project ID (for audit log)")

    auth = sub.add_parser("authorized", help="List skills authorized for an agent")
    auth.add_argument("--agent", required=True, help="Agent ID")

    check = sub.add_parser("check", help="Check if an agent can invoke a skill")
    check.add_argument("--agent", required=True)
    check.add_argument("--skill", required=True)

    ns = parser.parse_args()
    bridge = SkillBridge()

    if ns.command == "discover":
        skills = bridge.discover()
        if not skills:
            print("[info] No skills found.")
            return 0
        for s in skills:
            print(f"  {s.name:<30} {s.description[:80]}")
        print(f"\n{len(skills)} skill(s) found.")
    elif ns.command == "invoke":
        res = bridge.invoke(ns.agent, ns.skill, ns.query, ns.project_id)
        print(json.dumps(res.to_dict(), indent=2))
    elif ns.command == "authorized":
        skills = bridge.authorized_skills(ns.agent)
        for s in skills:
            print(f"  {s.name:<30} {s.description[:60]}")
    elif ns.command == "check":
        ok = bridge.is_skill_authorized(ns.agent, ns.skill)
        status = "AUTHORIZED" if ok else "DENIED"
        print(f"[{status}] agent='{ns.agent}' skill='{ns.skill}'")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
