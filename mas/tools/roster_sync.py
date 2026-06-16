#!/usr/bin/env python3
"""
roster_sync.py — Sync registry_canonical.yaml into episodic.db agents table.

The YAML registry is the editable source of truth.
episodic.db is the queryable projection kept in sync by this module.

Usage:
    python mas/tools/roster_sync.py [--db-path <path>] [--registry <path>] [--dry-run]

Exit codes:
    0  — all agents upserted successfully
    1  — error (file not found, schema mismatch, etc.)
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Defaults relative to the mas/ root (resolved for clone or installed workspace)
from core.paths import mas_root
_MAS_ROOT = mas_root()
DEFAULT_DB_PATH = _MAS_ROOT / "data" / "episodic.db"
DEFAULT_REGISTRY_PATH = _MAS_ROOT / "roster" / "registry_canonical.yaml"

CREATE_AGENTS_TABLE = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id        TEXT    PRIMARY KEY,
    claude_name     TEXT    NOT NULL,
    file_path       TEXT,
    trust_tier      TEXT,
    status          TEXT    NOT NULL DEFAULT 'active',
    model           TEXT,
    tools           TEXT,    -- JSON array
    domains         TEXT,    -- JSON array
    roles           TEXT,    -- JSON array
    risk_level      TEXT,
    can_spawn       INTEGER  NOT NULL DEFAULT 0,
    can_write_state INTEGER  NOT NULL DEFAULT 0,
    human_invocable INTEGER  NOT NULL DEFAULT 0,
    synced_at       TEXT     NOT NULL
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


def _bool_to_int(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    return int(value) if value else 0


def load_registry(registry_path: Path) -> dict:
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found: {registry_path}")
    with open(registry_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    agents = data.get("agents")
    if not isinstance(agents, dict):
        raise ValueError("registry_canonical.yaml must have a top-level 'agents' mapping")
    return agents


def sync(
    db_path: Path = DEFAULT_DB_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    dry_run: bool = False,
) -> int:
    """Upsert all agents from registry into episodic.db. Returns count of upserted rows."""
    agents = load_registry(registry_path)
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for agent_id, entry in agents.items():
        row = {
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
        }
        rows.append(row)

    if dry_run:
        print(f"[dry-run] Would upsert {len(rows)} agent(s) into {db_path}")
        for r in rows:
            print(f"  {r['agent_id']:30s}  tier={r['trust_tier']:<15}  model={r['model']}")
        return len(rows)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute(CREATE_AGENTS_TABLE)
        conn.executemany(UPSERT_AGENT, rows)
        conn.commit()
    finally:
        conn.close()

    print(f"Synced {len(rows)} agent(s) into {db_path}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync registry_canonical.yaml into episodic.db")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to episodic.db")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH), help="Path to registry_canonical.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be synced without writing")
    args = parser.parse_args()

    try:
        count = sync(
            db_path=Path(args.db_path),
            registry_path=Path(args.registry),
            dry_run=args.dry_run,
        )
        print(f"Done. {count} agents {'would be ' if args.dry_run else ''}synced.")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
