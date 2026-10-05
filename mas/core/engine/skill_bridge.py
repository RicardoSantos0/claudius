"""
Skill Bridge (migrated)

Gateway between MAS agents and the skills/ repository.
"""

from __future__ import annotations

import sys
import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.utils.registry_seed import load_frontmatter, skill_identity, split_frontmatter

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
# Restore store for the third-party skills pinned in skills-lock.json. Setup links each
# one into skills/, so a folder of skills/ may be a junction that resolves in here.
SKILL_STORE_DIRNAME = Path(".agents") / "skills"
# First line of a skill prompt that render_skill_prompt built from a readable SKILL.md.
# Every refusal it returns starts with "[" instead, so the prompt assembler can tell a
# delivered skill from a denial when the orchestration loop hands it on.
SKILL_PROMPT_PREAMBLE = "You are executing the following skill."

# Attribution decided in proj-YYYYMMDD-NNN-skill-attribution (lite MAS).
# See mas/projects/proj-YYYYMMDD-NNN-skill-attribution/planning/product_plan.yaml
# for per-agent rationale.
SKILL_ACCESS: dict[str, list[str]] = {
    "master_orchestrator": ["*"],
    "scribe_agent": [
        "research-extract", "research-sync", "mas-document", "mas-handoff",
        "writing-guidelines", "prose-craft", "bencium-aeo", "find-skills",
    ],
    "inquirer_agent": [
        "research-extract", "mas-clarify", "notebooklm",
        "adaptive-communication", "find-skills",
    ],
    "product_manager_agent": [
        "research-extract", "research-sync", "mas-clarify",
        "adaptive-communication", "writing-guidelines", "prose-craft",
        "human-architect-mindset",
        "bencium-aeo", "insurgent-campaign", "find-skills",
    ],
    # graphify = recon: navigate/query the target folder before decomposing execution.
    "project_manager_agent": [
        "research-extract", "mas-plan", "mas-examine", "graphify",
        "human-architect-mindset", "negentropy-lens", "find-skills",
    ],
    "hr_agent": ["find-skills"],
    "evaluator_agent": [
        "research-extract", "mas-postmortem",
        "vanity-engineering-review", "negentropy-lens",
        "find-skills",
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
        "negentropy-lens", "find-skills",
    ],
    "quality_advisor": [
        "mas-examine",
        "writing-guidelines", "prose-craft", "vanity-engineering-review",
        "design-audit", "ui-typography", "web-design-guidelines",
        "bencium-controlled-ux-designer",
        "impeccable", "find-skills",
    ],
    "devils_advocate": [
        "vanity-engineering-review", "negentropy-lens", "find-skills",
    ],
    # graphify = grounded codebase/architecture comprehension for domain reasoning.
    # NotebookLM grounding for this agent stays brokered via master_orchestrator (see
    # domain_expert.md "Knowledge Retrieval"), so no direct notebooklm grant here.
    # agentic-ux-design-relationship-centric-interfaces was granted here and removed: the
    # registry marks it provisioning: external but it is absent from skills-lock.json, so
    # nothing can restore it and the grant could only ever produce a denial. The registry
    # entry is left in place — the mismatch between it and the lock file is the owner's to
    # resolve, by adding the source to the lock or retiring the entry. Restore the grant
    # in the same change that makes the skill restorable.
    "domain_expert": [
        "research-extract", "mas-examine", "graphify",
        "human-architect-mindset", "renaissance-architecture", "adaptive-communication",
        "find-skills",
    ],
    "efficiency_advisor": [
        "vanity-engineering-review", "negentropy-lens", "find-skills",
    ],
    "session_scheduler": ["mas-review", "mas-handoff", "mas-logwork", "find-skills"],

    # ---- Delivery engineers (previously omitted -> silently denied all) ----
    "canonical_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "find-skills",
    ],
    "analysis_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "find-skills",
    ],
    "integration_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "find-skills",
    ],
    "reliability_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "find-skills",
    ],
    "ml_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "find-skills",
    ],
    "nlp_taxonomy_specialist": [
        "mas-examine", "graphify", "mas-logwork", "find-skills",
    ],
    "librarian_agent": [
        "mas-examine", "find-skills",
    ],

    # ---- Specialist agents added 2026-07-01 ----
    "appsec_specialist_agent": [
        "mas-examine", "graphify", "vanity-engineering-review", "find-skills",
    ],
    "backend_platform_engineer": [
        "mas-examine", "graphify", "mas-logwork", "vanity-engineering-review",
        "webapp-delivery", "frontend-design", "impeccable",
        "deploy-to-vercel", "vercel-cli-with-tokens", "vercel-optimize",
        "vercel-composition-patterns", "vercel-react-best-practices",
        "vercel-react-view-transitions", "vercel-react-native-skills",
        "bencium-code-conventions", "bencium-controlled-ux-designer",
        "bencium-impact-designer", "bencium-innovative-ux-designer",
        "find-skills",
    ],
}


# One path segment that cannot climb out of its parent: it starts with a letter or digit
# (so never "." or ".."), and has no separator, drive colon or whitespace. It never ends
# in a dot either, because Windows drops a trailing dot and would open the folder the
# name minus its dot names. Skill names and project ids both match it, and a
# caller-supplied name is checked against it before the name can reach the filesystem
# (IOP-22).
_PLAIN_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?")


def is_plain_name(value: str) -> bool:
    """True when value is a bare folder name such as 'mas-plan' and never a path."""
    return bool(_PLAIN_NAME.fullmatch(value or ""))


def strip_frontmatter(text: str) -> str:
    """Return a SKILL.md body without its leading YAML frontmatter block."""
    block, body = split_frontmatter(text)
    return text if block is None else body.lstrip()


class SkillMetadata:
    """One catalogue entry.

    ``key`` is the skill's folder name under skills/. It is the catalogue key, the name
    SKILL_ACCESS grants and the name mas_skill loads by. ``name`` is the top-level
    frontmatter name, which a few skills set to something else. ``installed`` is False
    for a skill the mas_skills table lists but whose folder holds no SKILL.md that MAS
    may read.
    """

    def __init__(self, name: str, description: str, path: Path, *,
                 key: str | None = None, installed: bool = True):
        self.name = name
        self.description = description
        self.path = path
        self.key = key or path.parent.name
        self.installed = installed

    def to_dict(self) -> dict:
        return {"name": self.name, "key": self.key, "description": self.description,
                "path": str(self.path)}


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
                 projects_root: Path | None = None, *,
                 use_db: bool | None = None,
                 store_dir: Path | None = None):
        self.skills_dir = skills_dir
        # Where per-project skill_audit_log.yaml files are written. Defaults to the
        # real mas/projects/ dir; tests inject a tmp_path so auditing never pollutes
        # the real projects tree (ip-rm-002).
        self.projects_root = projects_root or (ROOT / "projects")
        # The mas_skills table describes the real skills/ only, so a bridge over another
        # tree ignores it unless a caller (a test) asks for it.
        self.use_db = (skills_dir == SKILLS_DIR) if use_db is None else use_db
        # A folder of skills/ may be a junction into this store; nowhere else.
        self.store_dir = store_dir or (skills_dir.parent / SKILL_STORE_DIRNAME)
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

    def _readable_roots(self) -> tuple[str, ...]:
        """The two folders a readable SKILL.md may sit one folder below, resolved."""
        roots = []
        for root in (self.skills_dir, self.store_dir):
            try:
                roots.append(os.path.normcase(os.path.realpath(root)))
            except (OSError, ValueError):
                continue
        return tuple(roots)

    def _confined_skill_md(self, folder: str,
                           roots: tuple[str, ...] | None = None) -> Path | None:
        """skills/<folder>/SKILL.md when MAS may read it, otherwise None.

        The folder must be a plain name, and the file must resolve to a SKILL.md
        directly inside a folder of skills/ or of the restore store that third-party
        skill junctions point into. A link that leads anywhere else is refused, so
        nothing outside those two trees is ever read. The path returned is the one
        inside skills/, never the resolved one, so its folder name stays the key.
        """
        if not is_plain_name(folder):
            return None
        path = self.skills_dir / folder / "SKILL.md"
        try:
            if not path.is_file():
                return None
            real = os.path.realpath(path)
        except (OSError, ValueError):
            return None
        if os.path.normcase(os.path.basename(real)) != os.path.normcase("SKILL.md"):
            return None
        grandparent = os.path.normcase(os.path.dirname(os.path.dirname(real)))
        if grandparent not in (roots if roots is not None else self._readable_roots()):
            return None
        return path

    @staticmethod
    def _db_row_folder(row: dict) -> str | None:
        """The skills/ folder a mas_skills row names, or None when it names anything else.

        Only "skills/<folder>/SKILL.md" is accepted. A row naming another path is
        refused rather than corrected, and the file it names is never opened.
        """
        raw = str(row.get("skill_path") or "").replace("\\", "/")
        if not raw:
            skill_id = str(row.get("skill_id") or "")
            return skill_id if is_plain_name(skill_id) else None
        parts = raw.split("/")
        if (len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md"
                and is_plain_name(parts[1])):
            return parts[1]
        logger.debug("mas_skills row %r names %r, outside skills/<folder>/SKILL.md; ignored",
                     row.get("skill_id"), raw)
        return None

    def discover(self, force_refresh: bool = False) -> list[SkillMetadata]:
        """The skill catalogue: every skill folder on disk plus every mas_skills row.

        Keyed by folder name. A folder with a readable SKILL.md takes its name and
        description from that file's top-level frontmatter, even when a mas_skills row
        for the same folder says otherwise, because the row is a copy made by the last
        seed. A row whose folder holds no readable SKILL.md is kept as not installed,
        so the catalogue still lists it while nothing tries to read it.
        """
        if self._cache is not None and not force_refresh:
            return list(self._cache.values())

        found: dict[str, SkillMetadata] = {}
        roots = self._readable_roots()
        try:
            entries = sorted(self.skills_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for entry in entries:
            skill_md = self._confined_skill_md(entry.name, roots)
            if skill_md is None:
                continue
            try:
                meta = load_frontmatter(skill_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                meta = None
            name, description = skill_identity(meta, entry.name)
            found[entry.name] = SkillMetadata(name, description, skill_md, key=entry.name)

        for row in self._db_skills() if self.use_db else []:
            folder = self._db_row_folder(row)
            if folder is None or folder in found:
                continue
            found[folder] = SkillMetadata(
                name=str(row.get("name") or folder),
                description=str(row.get("description") or ""),
                path=self.skills_dir / folder / "SKILL.md",
                key=folder,
                installed=False,
            )

        self._cache = dict(sorted(found.items()))
        return list(self._cache.values())

    def get_skill(self, skill_name: str) -> SkillMetadata | None:
        """Look a skill up by folder name, or by the name its frontmatter declares.

        The folder name always wins, so a skill that declares another skill's folder
        name as its own cannot take that skill's place. A declared name that two skills
        share resolves to neither.
        """
        if self._cache is None:
            self.discover()
        catalogue = self._cache or {}
        skill = catalogue.get(skill_name)
        if skill is not None:
            return skill
        declared = [s for s in catalogue.values() if s.name == skill_name]
        return declared[0] if len(declared) == 1 else None

    def skill_in_folder(self, folder_name: str) -> SkillMetadata | None:
        """The skill whose folder under skills/ is folder_name, if any."""
        if self._cache is None:
            self.discover()
        return (self._cache or {}).get(folder_name)

    def read_skill_text(self, skill: SkillMetadata) -> str:
        """Read a catalogued skill's SKILL.md from skills/<key>/SKILL.md and nowhere else.

        The path is rebuilt from the key and checked again at read time, so neither a
        mas_skills row nor a link retargeted since discovery can point the read at
        another file. Raises OSError when the skill has no SKILL.md that MAS may read.
        """
        path = self._confined_skill_md(skill.key)
        if path is None:
            raise FileNotFoundError(
                f"Skill '{skill.key}' has no SKILL.md that MAS may read in "
                f"{self.skills_dir / skill.key}."
            )
        return path.read_text(encoding="utf-8")

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
        # SKILL_ACCESS grants folder names, so the folder key is what is compared.
        return [s for s in all_skills if s.key in allowed]

    def invoke(
        self,
        agent_id: str,
        skill_name: str,
        query: str,
        project_id: str = "",
        *,
        delivery: str = "",
    ) -> InvocationResult:
        """Authorize and audit one skill use.

        ``delivery`` names how the skill text reached the agent when MAS delivered it
        itself: "mas_skill" for the MCP tool, "inline" for text the assembler put in an
        API-runtime prompt. It is stored on the audit entry so `mas skill-usage` can
        tell those apart from a client's own skill command.

        A skill named by its declared frontmatter name is authorized and audited under
        its folder key, the name SKILL_ACCESS grants.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        tokens_used = _tc.count(query) if _tc else 0

        skill_meta = self.get_skill(skill_name)
        if skill_meta is not None:
            skill_name = skill_meta.key

        if not self.is_skill_authorized(agent_id, skill_name):
            audit = self._make_audit(
                agent_id, skill_name, query, project_id,
                outcome="denied", tokens_used=0, timestamp=timestamp,
                delivery=delivery,
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

        if skill_meta is None or not skill_meta.installed:
            audit = self._make_audit(
                agent_id, skill_name, query, project_id,
                outcome="skill_not_found", tokens_used=0, timestamp=timestamp,
                delivery=delivery,
            )
            self._persist_invocation_event(project_id, audit, "skill_skipped",
                                           "Skill not found")
            message = (
                f"Skill '{skill_name}' not found in {self.skills_dir}."
                if skill_meta is None else
                f"Skill '{skill_name}' is in the catalogue but not installed: "
                f"{self.skills_dir / skill_name} holds no SKILL.md that MAS may read."
            )
            return InvocationResult(
                success=False,
                skill_name=skill_name,
                agent_id=agent_id,
                outcome="skill_not_found",
                message=message,
                tokens_used=0,
                audit_entry=audit,
            )

        audit = self._make_audit(
            agent_id, skill_name, query, project_id,
            outcome="ok", tokens_used=tokens_used, timestamp=timestamp,
            delivery=delivery,
        )
        self._persist_invocation_event(
            project_id, audit, "skill_invoked",
            f"Skill text delivered ({delivery})" if delivery
            else "Skill invocation authorized",
        )

        return InvocationResult(
            success=True,
            skill_name=skill_name,
            agent_id=agent_id,
            outcome="ok",
            message=(
                f"Skill '{skill_name}' authorized for agent '{agent_id}'. "
                + (f"Text delivered via {delivery}." if delivery
                   else f"Invoke via: /{skill_name} {query}")
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

    def _make_audit(
        self,
        agent_id: str,
        skill_name: str,
        query: str,
        project_id: str,
        outcome: str,
        tokens_used: int,
        timestamp: str,
        delivery: str = "",
    ) -> dict:
        entry = {
            "timestamp": timestamp,
            "agent_id": agent_id,
            "skill_name": skill_name,
            "project_id": project_id or "unknown",
            "query_preview": query[:100] + ("..." if len(query) > 100 else ""),
            "outcome": outcome,
            "tokens_used": tokens_used,
        }
        if delivery:
            entry["delivery"] = delivery
        return entry

    def render_skill_prompt(self, agent_id: str, skill_name: str, query: str,
                            project_id: str = "") -> str:
        """
        Render a skill invocation as an executable prompt block.
        Returns a markdown block the agent can act on, or an error string.
        Never raises. When the SKILL.md cannot be read the result says so, rather than
        standing the description in for the procedure.
        """
        skill = self.get_skill(skill_name)
        key = skill.key if skill is not None else skill_name
        if not self.is_skill_authorized(agent_id, key):
            return f"[skill denied: {key!r} not authorized for {agent_id!r}]"
        if skill is None:
            return f"[skill not found: {skill_name!r}]"
        try:
            skill_text = self.read_skill_text(skill)
        except (OSError, UnicodeDecodeError) as exc:
            return f"[skill unreadable: {key!r}: {exc}]"
        return (
            f"{SKILL_PROMPT_PREAMBLE}\n\n"
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
            flag = "" if s.installed else " [not installed]"
            print(f"  {s.key:<30} {s.description[:80]}{flag}")
        print(f"\n{len(skills)} skill(s) found.")
    elif ns.command == "invoke":
        res = bridge.invoke(ns.agent, ns.skill, ns.query, ns.project_id)
        print(json.dumps(res.to_dict(), indent=2))
    elif ns.command == "authorized":
        skills = bridge.authorized_skills(ns.agent)
        for s in skills:
            print(f"  {s.key:<30} {s.description[:60]}")
    elif ns.command == "check":
        ok = bridge.is_skill_authorized(ns.agent, ns.skill)
        status = "AUTHORIZED" if ok else "DENIED"
        print(f"[{status}] agent='{ns.agent}' skill='{ns.skill}'")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
