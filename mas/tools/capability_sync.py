#!/usr/bin/env python3
"""
capability_sync.py — Sync agents, skills, and commands into episodic.db.

Sources of truth:
  - agents   → mas/roster/registry_canonical.yaml
  - skills   → skills/*/SKILL.md  (frontmatter: name, description)
  - commands → commands/*.md      (frontmatter: name, description)

Usage:
    python mas/tools/capability_sync.py [--db-path <path>] [--dry-run]

Exit codes:
    0  — success
    1  — error
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_MAS_ROOT = Path(__file__).parents[1]
_REPO_ROOT = _MAS_ROOT.parent
DEFAULT_DB_PATH = _MAS_ROOT / "data" / "episodic.db"
DEFAULT_REGISTRY_PATH = _MAS_ROOT / "roster" / "registry_canonical.yaml"
DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"
DEFAULT_COMMANDS_DIR = _REPO_ROOT / "commands"

# ── DDL ──────────────────────────────────────────────────────────────────────

CREATE_AGENTS_TABLE = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id        TEXT    PRIMARY KEY,
    claude_name     TEXT    NOT NULL,
    file_path       TEXT,
    trust_tier      TEXT,
    status          TEXT    NOT NULL DEFAULT 'active',
    model           TEXT,
    tools           TEXT,
    domains         TEXT,
    roles           TEXT,
    risk_level      TEXT,
    can_spawn       INTEGER NOT NULL DEFAULT 0,
    can_write_state INTEGER NOT NULL DEFAULT 0,
    human_invocable INTEGER NOT NULL DEFAULT 0,
    synced_at       TEXT    NOT NULL
);
"""

CREATE_SKILLS_TABLE = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id             TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    description          TEXT,
    file_path            TEXT,
    has_supporting_files INTEGER NOT NULL DEFAULT 0,
    synced_at            TEXT NOT NULL
);
"""

CREATE_COMMANDS_TABLE = """
CREATE TABLE IF NOT EXISTS commands (
    command_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    file_path   TEXT,
    synced_at   TEXT NOT NULL
);
"""

UPSERT_AGENT = """
INSERT INTO agents (
    agent_id, claude_name, file_path, trust_tier, status,
    model, tools, domains, roles, risk_level,
    can_spawn, can_write_state, human_invocable, synced_at
) VALUES (
    :agent_id, :claude_name, :file_path, :trust_tier, :status,
    :model, :tools, :domains, :roles, :risk_level,
    :can_spawn, :can_write_state, :human_invocable, :synced_at
)
ON CONFLICT(agent_id) DO UPDATE SET
    claude_name     = excluded.claude_name,
    file_path       = excluded.file_path,
    trust_tier      = excluded.trust_tier,
    status          = excluded.status,
    model           = excluded.model,
    tools           = excluded.tools,
    domains         = excluded.domains,
    roles           = excluded.roles,
    risk_level      = excluded.risk_level,
    can_spawn       = excluded.can_spawn,
    can_write_state = excluded.can_write_state,
    human_invocable = excluded.human_invocable,
    synced_at       = excluded.synced_at;
"""

UPSERT_SKILL = """
INSERT INTO skills (skill_id, name, description, file_path, has_supporting_files, synced_at)
VALUES (:skill_id, :name, :description, :file_path, :has_supporting_files, :synced_at)
ON CONFLICT(skill_id) DO UPDATE SET
    name                 = excluded.name,
    description          = excluded.description,
    file_path            = excluded.file_path,
    has_supporting_files = excluded.has_supporting_files,
    synced_at            = excluded.synced_at;
"""

UPSERT_COMMAND = """
INSERT INTO commands (command_id, name, description, file_path, synced_at)
VALUES (:command_id, :name, :description, :file_path, :synced_at)
ON CONFLICT(command_id) DO UPDATE SET
    name        = excluded.name,
    description = excluded.description,
    file_path   = excluded.file_path,
    synced_at   = excluded.synced_at;
"""

# ── Helpers ──────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    """Return parsed YAML frontmatter dict, or empty dict if absent."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _bool_to_int(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    return int(value) if value else 0


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_agents(registry_path: Path, now: str) -> list[dict]:
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found: {registry_path}")
    with open(registry_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    agents = data.get("agents")
    if not isinstance(agents, dict):
        raise ValueError("registry_canonical.yaml must have a top-level 'agents' mapping")

    rows = []
    for agent_id, entry in agents.items():
        rows.append({
            "agent_id": agent_id,
            "claude_name": entry.get("claude_name", agent_id),
            "file_path": entry.get("file", ""),
            "trust_tier": entry.get("trust_tier", ""),
            "status": entry.get("status", "active"),
            "model": entry.get("model", ""),
            "tools": json.dumps(entry.get("tools", [])),
            "domains": json.dumps(entry.get("domains", [])),
            "roles": json.dumps(entry.get("roles", [])),
            "risk_level": entry.get("risk_level", ""),
            "can_spawn": _bool_to_int(entry.get("can_spawn", False)),
            "can_write_state": _bool_to_int(entry.get("can_write_state", False)),
            "human_invocable": _bool_to_int(entry.get("human_invocable", False)),
            "synced_at": now,
        })
    return rows


def load_skills(skills_dir: Path, now: str) -> list[dict]:
    rows = []
    if not skills_dir.exists():
        return rows
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        text = skill_md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        skill_id = skill_dir.name
        name = fm.get("name") or skill_id
        description = fm.get("description") or ""
        # count supporting files: all files in dir except SKILL.md
        supporting = [f for f in skill_dir.iterdir()
                      if f.is_file() and f.name != "SKILL.md"]
        rows.append({
            "skill_id": skill_id,
            "name": name,
            "description": description,
            "file_path": str(skill_md),
            "has_supporting_files": 1 if supporting else 0,
            "synced_at": now,
        })
    return rows


def load_commands(commands_dir: Path, now: str) -> list[dict]:
    rows = []
    if not commands_dir.exists():
        return rows
    for cmd_file in sorted(commands_dir.glob("*.md")):
        text = cmd_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        # command_id derived from filename without extension
        command_id = cmd_file.stem
        name = fm.get("name") or command_id
        description = fm.get("description") or ""
        rows.append({
            "command_id": command_id,
            "name": name,
            "description": description,
            "file_path": str(cmd_file),
            "synced_at": now,
        })
    return rows


# ── Main sync ────────────────────────────────────────────────────────────────

def sync(
    db_path: Path = DEFAULT_DB_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    commands_dir: Path = DEFAULT_COMMANDS_DIR,
    dry_run: bool = False,
) -> dict:
    """Sync agents, skills, and commands into episodic.db. Returns counts dict."""
    now = datetime.now(timezone.utc).isoformat()

    agent_rows = load_agents(registry_path, now)
    skill_rows = load_skills(skills_dir, now)
    command_rows = load_commands(commands_dir, now)

    if dry_run:
        print(f"[dry-run] DB path: {db_path}")
        print(f"\n  Agents ({len(agent_rows)}):")
        for r in agent_rows:
            print(f"    {r['agent_id']:30s}  tier={r['trust_tier']}")
        print(f"\n  Skills ({len(skill_rows)}):")
        for r in skill_rows:
            print(f"    {r['skill_id']:30s}  name={r['name']}")
        print(f"\n  Commands ({len(command_rows)}):")
        for r in command_rows:
            print(f"    {r['command_id']:30s}  name={r['name']}")
        return {"agents": len(agent_rows), "skills": len(skill_rows), "commands": len(command_rows)}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute(CREATE_AGENTS_TABLE)
        conn.execute(CREATE_SKILLS_TABLE)
        conn.execute(CREATE_COMMANDS_TABLE)
        conn.executemany(UPSERT_AGENT, agent_rows)
        conn.executemany(UPSERT_SKILL, skill_rows)
        conn.executemany(UPSERT_COMMAND, command_rows)
        conn.commit()
    finally:
        conn.close()

    print(f"Synced: {len(agent_rows)} agents, {len(skill_rows)} skills, {len(command_rows)} commands -> {db_path}")
    return {"agents": len(agent_rows), "skills": len(skill_rows), "commands": len(command_rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync capabilities into episodic.db")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--skills-dir", default=str(DEFAULT_SKILLS_DIR))
    parser.add_argument("--commands-dir", default=str(DEFAULT_COMMANDS_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        counts = sync(
            db_path=Path(args.db_path),
            registry_path=Path(args.registry),
            skills_dir=Path(args.skills_dir),
            commands_dir=Path(args.commands_dir),
            dry_run=args.dry_run,
        )
        print(f"Done. agents={counts['agents']}, skills={counts['skills']}, commands={counts['commands']}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
