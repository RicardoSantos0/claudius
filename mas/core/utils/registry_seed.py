"""
MAS Registry Seed
Populates mas_agents, mas_skills, mas_commands, mas_templates, mas_domains,
mas_codebase, and mas_policies from the current filesystem state. Idempotent — safe to re-run.
"""
import json
import re
import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# mas/core/utils/registry_seed.py
# parents[0] = mas/core/utils
# parents[1] = mas/core
# parents[2] = mas
# parents[3] = repo root (claude-config)
DB_PATH = Path(__file__).parents[2] / "data" / "episodic.db"
ROOT = Path(__file__).parents[3]

# Hardcoded tier map derived from agent model fields / known structure.
# Agents with model=claude-opus-* are T0; others default based on known groupings.
_TIER_MAP: dict[str, str] = {
    "master_orchestrator": "T0",
    "scribe_agent": "T0",
    "hr_agent": "T1-established",
    "inquirer_agent": "T1-established",
    "product_manager_agent": "T1-established",
    "project_manager_agent": "T1-established",
    "evaluator_agent": "T1-established",
    "trainer_agent": "T1-established",
    "risk_advisor": "T1-consultant",
    "quality_advisor": "T1-consultant",
    "devils_advocate": "T1-consultant",
    "domain_expert": "T1-consultant",
    "efficiency_advisor": "T1-consultant",
    "canonical_engineer": "T1-delivery",
    "analysis_engineer": "T1-delivery",
    "integration_engineer": "T1-delivery",
    "reliability_engineer": "T1-delivery",
    "spawner_agent": "T2-supervised",
    "librarian_agent": "T2-supervised",
    "session_scheduler": "infrastructure",
}

_DOMAINS: list[dict] = [
    {
        "domain_id": "core-orchestration",
        "name": "Core Orchestration",
        "description": "Central coordination and record-keeping agents.",
        "related_agents": ["master_orchestrator", "scribe_agent"],
    },
    {
        "domain_id": "project-delivery",
        "name": "Project Delivery",
        "description": "Engineering agents responsible for sprint delivery.",
        "related_agents": [
            "canonical_engineer",
            "analysis_engineer",
            "integration_engineer",
            "reliability_engineer",
        ],
    },
    {
        "domain_id": "governance",
        "name": "Governance",
        "description": "Agents that govern agent lifecycle and quality gates.",
        "related_agents": [
            "hr_agent",
            "evaluator_agent",
            "spawner_agent",
            "trainer_agent",
        ],
    },
    {
        "domain_id": "advisory",
        "name": "Advisory",
        "description": "Consultant agents providing specialist advice.",
        "related_agents": [
            "risk_advisor",
            "quality_advisor",
            "devils_advocate",
            "domain_expert",
            "efficiency_advisor",
        ],
    },
    {
        "domain_id": "infrastructure",
        "name": "Infrastructure",
        "description": "Infrastructure and library management agents.",
        "related_agents": ["session_scheduler", "librarian_agent"],
    },
]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Returns ({}, text) if no frontmatter."""
    if not text.startswith("---"):
        return {}, text
    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].strip()
    data: dict = {}
    # Simple key: value YAML parser (no nested structures needed here)
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw_val = line.partition(":")
        key = key.strip()
        val = raw_val.strip()
        # Strip surrounding quotes
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        # Parse lists: [a, b, c] or comma-separated after key
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            data[key] = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
        else:
            data[key] = val
    return data, body


def _get_connection(db_path: Path) -> sqlite3.Connection:
    # Reuse the closing-connection factory so `with _get_connection(...)` blocks
    # close the connection on exit (plain sqlite3 only commits) — avoids the
    # ResourceWarning: unclosed database leak under Python 3.13.
    from core.utils.log_helpers import _ClosingConnection
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), factory=_ClosingConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _seed_agents(conn: sqlite3.Connection, root: Path) -> int:
    agents_dir = root / "agents"
    count = 0
    if not agents_dir.exists():
        return count
    for md_file in sorted(agents_dir.glob("*.md")):
        stem = md_file.stem
        # Skip utility files that aren't real agents
        if stem.startswith("_"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            name = fm.get("name") or stem
            description = fm.get("description", "")
            tools_raw = fm.get("tools", "")
            if isinstance(tools_raw, list):
                tools_json = json.dumps(tools_raw)
            elif tools_raw:
                tools_json = json.dumps([t.strip() for t in tools_raw.split(",") if t.strip()])
            else:
                tools_json = json.dumps([])
            tier = _TIER_MAP.get(stem, "unknown")
            template_path = f"agents/{stem}.md"
            conn.execute(
                """INSERT OR REPLACE INTO mas_agents
                   (agent_id, name, tier, description, template_path, tools, status, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
                (stem, name, tier, description, template_path, tools_json, json.dumps({})),
            )
            count += 1
        except Exception as exc:
            warnings.warn(f"registry_seed: skipping agent {md_file.name}: {exc}")
    return count


def _seed_skills(conn: sqlite3.Connection, root: Path) -> int:
    skills_dir = root / "skills"
    count = 0
    if not skills_dir.exists():
        return count
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_id = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            name = fm.get("name") or skill_id
            description = fm.get("description", "")
            trigger_pattern = fm.get("triggers") or fm.get("trigger_pattern") or fm.get("trigger", "")
            skill_path = f"skills/{skill_id}/SKILL.md"
            conn.execute(
                """INSERT OR REPLACE INTO mas_skills
                   (skill_id, name, description, trigger_pattern, skill_path, status, metadata)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (skill_id, name, description, trigger_pattern, skill_path, json.dumps({})),
            )
            count += 1
        except Exception as exc:
            warnings.warn(f"registry_seed: skipping skill {skill_dir.name}: {exc}")
    return count


def _seed_commands(conn: sqlite3.Connection, root: Path) -> int:
    commands_dir = root / "commands"
    count = 0
    if not commands_dir.exists():
        return count
    for md_file in sorted(commands_dir.glob("*.md")):
        stem = md_file.stem
        if stem.startswith("_"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(text)
            name = fm.get("name") or stem
            description = fm.get("description", "")
            command_path = f"commands/{stem}.md"
            conn.execute(
                """INSERT OR REPLACE INTO mas_commands
                   (command_id, name, description, command_path, status, metadata)
                   VALUES (?, ?, ?, ?, 'active', ?)""",
                (stem, name, description, command_path, json.dumps({})),
            )
            count += 1
        except Exception as exc:
            warnings.warn(f"registry_seed: skipping command {md_file.name}: {exc}")
    return count


def _seed_templates(conn: sqlite3.Connection, root: Path) -> int:
    """Insert one template record per agent whose template_path exists on disk."""
    cur = conn.execute("SELECT agent_id, name, template_path FROM mas_agents")
    rows = cur.fetchall()
    count = 0
    for agent_id, name, template_path in rows:
        if template_path and (root / template_path).exists():
            template_id = f"tmpl-{agent_id}"
            conn.execute(
                """INSERT OR REPLACE INTO mas_templates
                   (template_id, name, agent_id, template_path, version, metadata)
                   VALUES (?, ?, ?, ?, '1.0', ?)""",
                (template_id, name, agent_id, template_path, json.dumps({})),
            )
            count += 1
    return count


def _seed_domains(conn: sqlite3.Connection) -> int:
    count = 0
    for domain in _DOMAINS:
        conn.execute(
            """INSERT OR REPLACE INTO mas_domains
               (domain_id, name, description, related_agents, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                domain["domain_id"],
                domain["name"],
                domain.get("description", ""),
                json.dumps(domain.get("related_agents", [])),
                json.dumps({}),
            ),
        )
        count += 1
    return count


def _file_type_for_mas_path(rel_path: str) -> str:
    """Determine file_type label from a relative path under mas/."""
    parts = rel_path.replace("\\", "/").split("/")
    # parts[0] = 'mas', parts[1] = subdir
    if len(parts) > 2:
        subdir = parts[1]
        if subdir == "core" and len(parts) > 3 and parts[2] == "engine":
            return "engine"
        if subdir == "core" and len(parts) > 3 and parts[2] == "adapters":
            return "adapter"
        if subdir == "tests":
            return "test"
    return "core"


def _module_name_from_path(rel_path: str) -> str:
    """Convert a relative .py path to a dotted module name."""
    p = rel_path.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _seed_codebase(conn: sqlite3.Connection, root: Path) -> int:
    """Seed mas_codebase with all .py files under mas/ (excluding pycache/venv/tests)."""
    mas_dir = root / "mas"
    if not mas_dir.exists():
        return 0
    count = 0
    exclude_dirs = {"__pycache__", ".venv", "venv"}
    for py_file in sorted(mas_dir.rglob("*.py")):
        # Skip excluded directories
        if any(part in exclude_dirs for part in py_file.parts):
            continue
        try:
            rel_path = py_file.relative_to(root).as_posix()
            mtime = datetime.fromtimestamp(py_file.stat().st_mtime, tz=timezone.utc).isoformat()
            file_type = _file_type_for_mas_path(rel_path)
            module_name = _module_name_from_path(rel_path)
            conn.execute(
                """INSERT OR REPLACE INTO mas_codebase
                   (file_id, file_path, module_name, description, project_id,
                    language, file_type, last_modified, metadata)
                   VALUES (?, ?, ?, NULL, NULL, 'python', ?, ?, ?)""",
                (rel_path, rel_path, module_name, file_type, mtime, json.dumps({})),
            )
            count += 1
        except Exception as exc:
            warnings.warn(f"registry_seed: skipping codebase file {py_file}: {exc}")
    return count


def _seed_project_codebases(conn: sqlite3.Connection, root: Path) -> int:
    """Seed mas_codebase with .py files under mas/projects/, tagged by project_id."""
    projects_dir = root / "mas" / "projects"
    if not projects_dir.exists():
        return 0
    count = 0
    exclude_dirs = {"__pycache__", ".venv", "venv"}
    from core.utils.config import iter_project_dirs
    for project_dir in iter_project_dirs(projects_root=projects_dir):  # flat + nested
        project_id = project_dir.name
        for py_file in sorted(project_dir.rglob("*.py")):
            if any(part in exclude_dirs for part in py_file.parts):
                continue
            try:
                rel_path = py_file.relative_to(root).as_posix()
                mtime = datetime.fromtimestamp(py_file.stat().st_mtime, tz=timezone.utc).isoformat()
                module_name = _module_name_from_path(rel_path)
                conn.execute(
                    """INSERT OR REPLACE INTO mas_codebase
                       (file_id, file_path, module_name, description, project_id,
                        language, file_type, last_modified, metadata)
                       VALUES (?, ?, ?, NULL, ?, 'python', 'project', ?, ?)""",
                    (rel_path, rel_path, module_name, project_id, mtime, json.dumps({})),
                )
                count += 1
            except Exception as exc:
                warnings.warn(f"registry_seed: skipping project file {py_file}: {exc}")
    return count


def _seed_policies(conn: sqlite3.Connection, root: Path) -> int:
    """Seed mas_policies from mas/policies/*.yaml files."""
    policies_dir = root / "mas" / "policies"
    count = 0
    if not policies_dir.exists():
        return count
    for yaml_file in sorted(policies_dir.glob("*.yaml")):
        stem = yaml_file.stem
        try:
            text = yaml_file.read_text(encoding="utf-8")
            description = ""
            for line in text.splitlines():
                stripped = line.strip()
                # Skip banner lines (all = or - chars) and blank comment lines
                if stripped.startswith("# "):
                    candidate = stripped[2:].strip()
                    if candidate and set(candidate) - {"=", "-", " "}:
                        description = candidate
                        break
                elif stripped and not stripped.startswith("#"):
                    break
            name = description or stem.replace("_", " ").title()
            mtime = datetime.fromtimestamp(yaml_file.stat().st_mtime, tz=timezone.utc).isoformat()
            policy_path = f"mas/policies/{stem}.yaml"
            conn.execute(
                """INSERT OR REPLACE INTO mas_policies
                   (policy_id, name, policy_path, description, status, last_modified, metadata)
                   VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                (stem, name, policy_path, description, mtime, json.dumps({})),
            )
            count += 1
        except Exception as exc:
            warnings.warn(f"registry_seed: skipping policy {yaml_file.name}: {exc}")
    return count


def _seed_agent_evaluations(conn: sqlite3.Connection, root: Path) -> int:
    """Update mas_agents with evaluation data from roster YAML if score fields exist."""
    roster_path = root / "mas" / "roster" / "registry_canonical.yaml"
    if not roster_path.exists():
        return 0
    try:
        import yaml
        with open(roster_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        agents = data.get("agents", {})
        if not isinstance(agents, dict):
            return 0
        count = 0
        for agent_id, entry in agents.items():
            if not isinstance(entry, dict):
                continue
            score = entry.get("score")
            if score is not None:
                conn.execute(
                    "UPDATE OR IGNORE mas_agents SET last_score = ? WHERE agent_id = ?",
                    (float(score), agent_id),
                )
                count += 1
        return count
    except Exception as exc:
        warnings.warn(f"registry_seed: could not seed agent evaluations: {exc}")
        return 0


def _prune_codebase(conn: sqlite3.Connection, root: Path) -> int:
    """Remove mas_codebase rows whose file_path no longer exists on disk.

    Seeding is INSERT OR REPLACE (additive) — without this prune, files that were
    deleted/renamed leave phantom rows in the registry (registry drift). Returns the
    number of stale rows removed.
    """
    rows = [r[0] for r in conn.execute("SELECT file_path FROM mas_codebase")]
    stale = [p for p in rows if p and not (root / p).exists()]
    for p in stale:
        conn.execute("DELETE FROM mas_codebase WHERE file_path = ?", (p,))
    return len(stale)


def seed(db_path: Path = DB_PATH, root: Path = ROOT) -> dict:
    """Seed all registry tables. Returns counts per table."""
    from core.utils.log_helpers import init_db
    init_db(db_path=db_path)
    with _get_connection(db_path) as conn:
        agents_count = _seed_agents(conn, root)
        skills_count = _seed_skills(conn, root)
        commands_count = _seed_commands(conn, root)
        templates_count = _seed_templates(conn, root)
        domains_count = _seed_domains(conn)
        codebase_count = _seed_codebase(conn, root)
        codebase_count += _seed_project_codebases(conn, root)
        pruned = _prune_codebase(conn, root)  # drop phantom paths (registry drift)
        codebase_count -= pruned
        policies_count = _seed_policies(conn, root)
        _seed_agent_evaluations(conn, root)
        conn.commit()
    return {
        "mas_agents": agents_count,
        "mas_skills": skills_count,
        "mas_commands": commands_count,
        "mas_templates": templates_count,
        "mas_domains": domains_count,
        "mas_codebase": codebase_count,
        "mas_policies": policies_count,
    }


if __name__ == "__main__":
    counts = seed()
    for table, n in counts.items():
        print(f"  {table}: {n} rows seeded")
