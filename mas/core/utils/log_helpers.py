"""
MAS Log Helpers — utils copy

Copied into `core.utils` as part of the incremental refactor. Adjusted
DB_PATH calculation to remain correct when the module lives under
`mas/core/utils/`.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.adapters import postgres_store

# Resolve DB path to <mas>/data/episodic.db in both source-tree and installed modes.
from core.paths import mas_root
DB_PATH = mas_root() / "data" / "episodic.db"


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 envelope
# ---------------------------------------------------------------------------

def make_log_entry(
    agent_id: str,
    action_type: str,
    intent: str,
    inputs: Optional[dict] = None,
    result_shape: Optional[str] = None,
    artifacts: Optional[list] = None,
    decisions: Optional[list] = None,
    error: Optional[str] = None,
    action_id: Optional[str] = None,
) -> dict:
    """Build a JSON-RPC 2.0 structured log entry."""
    return {
        "_v": "1.0",
        "jsonrpc": "2.0",
        "id": action_id or str(uuid.uuid4()),
        "method": f"{agent_id}.{action_type}",
        "params": {
            "intent": intent,
            "inputs": inputs or {},
        },
        "result": {
            "result_shape": result_shape or "",
            "artifacts": artifacts or [],
            "decisions": decisions or [],
        },
        "error": error,
    }


# ---------------------------------------------------------------------------
# SQLite episodic log
# ---------------------------------------------------------------------------

class _ClosingConnection(sqlite3.Connection):
    """A ``sqlite3.Connection`` that also CLOSES itself on context-manager exit.

    Plain sqlite3 connections only commit/roll back in ``__exit__`` and stay
    *open* — so every ``with _get_connection(...) as conn:`` leaked the
    connection until garbage collection, surfacing as ``ResourceWarning:
    unclosed database`` under Python 3.13 (and breaking ``-W error`` runs).
    Closing here makes the dominant ``with``-based access pattern leak-free
    without touching every call site.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), factory=_ClosingConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _active_db_url(db_path: Path | None = None, db_url: str | None = None) -> str | None:
    if db_url:
        return db_url
    if db_path is not None and db_path != DB_PATH:
        return f"sqlite:///{db_path}"
    from core.runtime_config import get_database_backend
    return get_database_backend().get("url")


def init_db(db_path: Path = DB_PATH, db_url: str | None = None) -> None:
    """Initialise episodic log DB schema (idempotent)."""
    resolved_url = _active_db_url(db_path=db_path, db_url=db_url)
    if postgres_store.is_postgres_url(resolved_url):
        postgres_store.init_db(resolved_url)
        return
    with _get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id   TEXT    NOT NULL,
                agent_id     TEXT    NOT NULL,
                action_type  TEXT    NOT NULL,
                timestamp    TEXT    NOT NULL,
                intent       TEXT,
                result_shape TEXT,
                payload      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_project   ON agent_events(project_id);
            CREATE INDEX IF NOT EXISTS idx_agent     ON agent_events(agent_id);
            CREATE INDEX IF NOT EXISTS idx_action    ON agent_events(action_type);
            CREATE INDEX IF NOT EXISTS idx_timestamp ON agent_events(timestamp);

            -- FTS5 virtual table for semantic (full-text) search over intent + payload.
            -- content=agent_events keeps the FTS index in sync with the base table
            -- when rows are inserted via the trigger below.
            CREATE VIRTUAL TABLE IF NOT EXISTS agent_events_fts
                USING fts5(
                    intent,
                    payload,
                    content='agent_events',
                    content_rowid='id'
                );

            -- Trigger: keep FTS index current on every new event.
            CREATE TRIGGER IF NOT EXISTS agent_events_fts_insert
                AFTER INSERT ON agent_events
                BEGIN
                    INSERT INTO agent_events_fts(rowid, intent, payload)
                    VALUES (NEW.id, NEW.intent, NEW.payload);
                END;

            -- Graph tables: nodes and edges migrated from global_graph.yaml
            CREATE TABLE IF NOT EXISTS agent_graph (
                id      TEXT PRIMARY KEY,
                type    TEXT,
                label   TEXT,
                meta    TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_graph_edges (
                id          TEXT PRIMARY KEY,
                source      TEXT,
                target      TEXT,
                relation    TEXT,
                meta        TEXT
            );

            -- Registry tables
            CREATE TABLE IF NOT EXISTS mas_agents (
                agent_id           TEXT PRIMARY KEY,
                name               TEXT NOT NULL,
                tier               TEXT,
                description        TEXT,
                template_path      TEXT,
                tools              TEXT,
                status             TEXT DEFAULT 'active',
                metadata           TEXT,
                last_score         REAL,
                evaluation_count   INTEGER DEFAULT 0,
                last_evaluated_at  TEXT,
                evaluation_summary TEXT
            );

            CREATE TABLE IF NOT EXISTS mas_skills (
                skill_id         TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                description      TEXT,
                trigger_pattern  TEXT,
                skill_path       TEXT,
                status           TEXT DEFAULT 'active',
                metadata         TEXT
            );

            CREATE TABLE IF NOT EXISTS mas_commands (
                command_id   TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                description  TEXT,
                command_path TEXT,
                status       TEXT DEFAULT 'active',
                metadata     TEXT
            );

            CREATE TABLE IF NOT EXISTS mas_templates (
                template_id   TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                agent_id      TEXT,
                template_path TEXT,
                version       TEXT DEFAULT '1.0',
                metadata      TEXT
            );

            CREATE TABLE IF NOT EXISTS mas_domains (
                domain_id      TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT,
                related_agents TEXT,
                metadata       TEXT
            );

            CREATE TABLE IF NOT EXISTS mas_codebase (
                file_id       TEXT PRIMARY KEY,
                file_path     TEXT NOT NULL,
                module_name   TEXT,
                description   TEXT,
                project_id    TEXT,
                language      TEXT DEFAULT 'python',
                file_type     TEXT,
                last_modified TEXT,
                metadata      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_codebase_project ON mas_codebase(project_id);

            CREATE TABLE IF NOT EXISTS mas_policies (
                policy_id     TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                policy_path   TEXT,
                description   TEXT,
                status        TEXT DEFAULT 'active',
                last_modified TEXT,
                metadata      TEXT
            );
        """)

        # Add evaluation columns to mas_agents if they don't already exist
        # (handles databases created before these columns were introduced)
        _AGENT_EVAL_COLS = [
            ("last_score", "REAL"),
            ("evaluation_count", "INTEGER DEFAULT 0"),
            ("last_evaluated_at", "TEXT"),
            ("evaluation_summary", "TEXT"),
        ]
        for col_name, col_type in _AGENT_EVAL_COLS:
            try:
                conn.execute(f"ALTER TABLE mas_agents ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass  # column already exists


def append_event(
    project_id: str,
    agent_id: str,
    action_type: str,
    intent: str,
    result_shape: str = "",
    payload: Optional[dict] = None,
    db_path: Path = DB_PATH,
    db_url: str | None = None,
) -> str:
    """Append an event to the episodic log. Returns the action_id."""
    action_id = str(uuid.uuid4())
    entry = make_log_entry(
        agent_id=agent_id,
        action_type=action_type,
        intent=intent,
        result_shape=result_shape,
        action_id=action_id,
    )
    if payload:
        entry["params"]["inputs"] = payload

    ts = datetime.now(timezone.utc).isoformat()
    resolved_url = _active_db_url(db_path=db_path, db_url=db_url)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.append_event(
            resolved_url,
            project_id=project_id,
            agent_id=agent_id,
            action_type=action_type,
            timestamp=ts,
            intent=intent,
            result_shape=result_shape,
            payload=entry,
        )

    init_db(db_path)
    with _get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO agent_events
               (project_id, agent_id, action_type, timestamp, intent, result_shape, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, agent_id, action_type, ts, intent, result_shape,
             json.dumps(entry)),
        )
    return action_id


def query_by_action_id(action_id: str, db_path: Path = DB_PATH, db_url: str | None = None) -> Optional[dict]:
    """Retrieve a single event by its action_id — no full-scan."""
    resolved_url = _active_db_url(db_path=db_path, db_url=db_url)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.query_by_action_id(resolved_url, action_id)
    if not db_path.exists():
        return None
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM agent_events WHERE json_extract(payload, '$.id') = ?",
            (action_id,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
    return None


def query_events(
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 50,
    db_path: Path = DB_PATH,
    db_url: str | None = None,
) -> list[dict]:
    """Query events by project/agent/action with a row limit."""
    resolved_url = _active_db_url(db_path=db_path, db_url=db_url)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.query_events(
            resolved_url,
            project_id=project_id,
            agent_id=agent_id,
            action_type=action_type,
            limit=limit,
        )
    if not db_path.exists():
        return []
    clauses, params = [], []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if action_type:
        clauses.append("action_type = ?")
        params.append(action_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _get_connection(db_path) as conn:
        cur = conn.execute(
            f"SELECT * FROM agent_events {where} ORDER BY id DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
