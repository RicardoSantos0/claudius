"""
MAS Database — Central SQL access layer.

Single import point for all database operations. Every module that needs
to read or write the event log imports from here, not from log_helpers directly.

Default local database: mas/data/episodic.db

Tables:
  agent_events      — every handoff, agent call, phase transition, consultation
  agent_events_fts  — FTS5 virtual table (intent + payload) for semantic search
  episodic_events   — migrated history (read-only, commsopt migration)

Public API:
  append_event(project_id, agent_id, action_type, intent, ...)  → action_id
  query_events(project_id, agent_id, action_type, limit) → list[dict]
  query_project_history(project_id, limit)  → list[dict]  (chronological)
  query_agent_context(project_id, agent_id, limit) → list[dict]
  semantic_search(query, project_id, limit) → list[dict]  (FTS5 ranked)
  query_token_usage(project_id) → dict  (summed token counts for agent_call events)
  format_events_for_prompt(events) → str
"""

import json as _json
import logging
from pathlib import Path
from typing import Optional

from core.utils.log_helpers import (
    DB_PATH,
    init_db,
    append_event,
    query_events,
    query_by_action_id,
    _get_connection,
)
from core.runtime_config import get_database_backend
from core.adapters import postgres_store

logger = logging.getLogger(__name__)

__all__ = [
    "DB_PATH",
    "init_db",
    "append_event",
    "query_events",
    "query_by_action_id",
    "query_project_history",
    "query_agent_context",
    "semantic_search",
    "query_token_usage",
    "record_manual_tokens",
    "query_graph_node",
    "query_graph_edges",
    "format_events_for_prompt",
    "upsert_shared_state",
    "get_shared_state",
    "migrate_sqlite_to_postgres",
]


def _resolved_db_url(db_path: Path = DB_PATH) -> str:
    if db_path != DB_PATH:
        return f"sqlite:///{db_path}"
    return get_database_backend()["url"]


def query_project_history(
    project_id: str,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return the most recent N events for a project, in chronological order.
    Use this in agent context injection — agents see what happened before them.
    """
    rows = query_events(project_id=project_id, limit=limit, db_path=db_path)
    return list(reversed(rows))  # query_events returns newest-first; reverse for agents


def query_agent_context(
    project_id: str,
    agent_id: str,
    limit: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return the most recent N events for a specific agent on a project.
    Use to give an agent its own recent history.
    """
    rows = query_events(project_id=project_id, agent_id=agent_id,
                        limit=limit, db_path=db_path)
    return list(reversed(rows))


def semantic_search(
    query: str,
    project_id: str | None = None,
    limit: int = 5,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Full-text search over agent_events using the FTS5 index (agent_events_fts).
    Results are ranked by BM25 relevance (best match first).

    Falls back to [] gracefully if:
      - The FTS5 table doesn't exist yet (call init_db() first)
      - The query is empty or causes a syntax error
      - Any SQLite error

    Args:
        query:      Search term(s) — plain text, FTS5 syntax supported.
        project_id: Optional filter to scope results to one project.
        limit:      Maximum results to return.
        db_path:    Path to the SQLite database (default: mas/data/episodic.db).

    Returns:
        List of event dicts (same shape as query_events results), newest-first.
    """
    if not query or not query.strip():
        return []
    resolved_url = _resolved_db_url(db_path)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.semantic_search(resolved_url, query, project_id=project_id, limit=limit)
    try:
        with _get_connection(db_path) as conn:
            if project_id:
                sql = """
                    SELECT ae.id, ae.project_id, ae.agent_id, ae.action_type,
                           ae.timestamp, ae.intent, ae.result_shape, ae.payload
                    FROM agent_events_fts
                    JOIN agent_events ae ON agent_events_fts.rowid = ae.id
                    WHERE agent_events_fts MATCH ?
                      AND ae.project_id = ?
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (query, project_id, limit)).fetchall()
            else:
                sql = """
                    SELECT ae.id, ae.project_id, ae.agent_id, ae.action_type,
                           ae.timestamp, ae.intent, ae.result_shape, ae.payload
                    FROM agent_events_fts
                    JOIN agent_events ae ON agent_events_fts.rowid = ae.id
                    WHERE agent_events_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (query, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def query_token_usage(
    project_id: str,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Sum token usage across all agent_call events for a project.
    Token fields are stored in the JSON payload as:
      {"tokens_prompt": N, "tokens_completion": N, "tokens_total": N}

    Returns:
        {"total_prompt": int, "total_completion": int, "total": int, "calls": int}
    """
    try:
        rows = query_events(project_id=project_id, action_type="agent_call", db_path=db_path)
        total_prompt = total_completion = total = calls = 0
        for row in rows:
            try:
                data = _json.loads(row["payload"] or "{}")
                # Support both flat payload (new format) and nested params.inputs (old format)
                params = data.get("params", {}).get("inputs", data)
                total_prompt     += params.get("tokens_prompt", 0)
                total_completion += params.get("tokens_completion", 0)
                total            += params.get("tokens_total", 0)
                calls += 1
            except Exception as exc:
                logger.debug("skipping malformed token-usage row: %s", exc)
        return {
            "total_prompt":     total_prompt,
            "total_completion": total_completion,
            "total":            total,
            "calls":            calls,
        }
    except Exception:
        return {
            "total_prompt": 0, "total_completion": 0, "total": 0,
            "calls": 0,
        }


def record_manual_tokens(
    project_id: str,
    agent_id: str,
    tokens_prompt: int = 0,
    tokens_completion: int = 0,
    note: str = "",
    db_path: Path = DB_PATH,
) -> str:
    """Record manual-mode (Claude Code) token usage as an agent_call event.

    Manual mode burns real tokens that the engine never sees (it only auto-records
    tokens when *it* calls the API in `mas run`). This writes the same agent_call
    event shape `query_token_usage` reads, so `mas tokens` and the comms-efficiency
    metric stop treating manual-mode work as zero-cost. Returns the action_id.
    """
    tokens_total = int(tokens_prompt) + int(tokens_completion)
    return append_event(
        project_id=project_id,
        agent_id=agent_id,
        action_type="agent_call",
        intent=(note or "manual-mode token usage")[:120],
        result_shape=f"tokens={tokens_total}",
        payload={
            "model": "claude-code-manual",
            "tokens_prompt": int(tokens_prompt),
            "tokens_completion": int(tokens_completion),
            "tokens_total": tokens_total,
            "source": "manual",
        },
        db_path=db_path,
    )


def query_graph_node(
    node_id: str,
    db_path: Path = DB_PATH,
) -> dict | None:
    """
    Retrieve a single node from the agent_graph table by its ID.
    Returns None if the table doesn't exist or the node is not found.

    Args:
        node_id: The node's primary key (e.g. an agent_id like 'master_orchestrator').
        db_path: Path to the SQLite database.
    """
    resolved_url = _resolved_db_url(db_path)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.query_graph_node(resolved_url, node_id)
    try:
        with _get_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id, type, label, meta FROM agent_graph WHERE id = ?",
                (node_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def query_graph_edges(
    node_id: str,
    limit: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Retrieve all edges where node_id is the source OR target.
    Returns [] if the table doesn't exist or no edges found.

    Args:
        node_id: The node whose edges to retrieve.
        limit:   Max edges to return.
        db_path: Path to the SQLite database.
    """
    resolved_url = _resolved_db_url(db_path)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.query_graph_edges(resolved_url, node_id, limit=limit)
    try:
        with _get_connection(db_path) as conn:
            rows = conn.execute(
                """SELECT id, source, target, relation, meta
                   FROM agent_graph_edges
                   WHERE source = ? OR target = ?
                   LIMIT ?""",
                (node_id, node_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def format_events_for_prompt(events: list[dict]) -> str:
    """
    Format a list of DB event rows as a compact string for prompt injection.
    Returns at most the last 5 events to stay within token budget.
    """
    if not events:
        return "(no prior events recorded)"
    lines = []
    for e in events[-5:]:
        ts = (e.get("timestamp") or "")[:16]       # YYYY-MM-DDTHH:MM
        agent = e.get("agent_id") or "?"
        action = e.get("action_type") or "?"
        intent = (e.get("intent") or "")[:80]      # cap to keep prompts short
        lines.append(f"[{ts}] {agent} / {action}: {intent}")
    return "\n".join(lines)


def upsert_shared_state(project_id: str, state: dict, db_path: Path = DB_PATH) -> None:
    resolved_url = _resolved_db_url(db_path)
    if postgres_store.is_postgres_url(resolved_url):
        postgres_store.upsert_shared_state(resolved_url, project_id, state)
        return
    try:
        from core.adapters.sqlite_shared_state import upsert_shared_state as _sqlite_upsert
    except Exception:
        _sqlite_upsert = None
    if _sqlite_upsert:
        _sqlite_upsert(resolved_url, project_id, state)


def get_shared_state(project_id: str, db_path: Path = DB_PATH) -> dict | None:
    resolved_url = _resolved_db_url(db_path)
    if postgres_store.is_postgres_url(resolved_url):
        return postgres_store.get_shared_state(resolved_url, project_id)
    try:
        from core.adapters.sqlite_shared_state import get_shared_state as _sqlite_get
    except Exception:
        _sqlite_get = None
    if _sqlite_get:
        return _sqlite_get(resolved_url, project_id)
    return None


def migrate_sqlite_to_postgres(sqlite_path: Path, postgres_url: str) -> dict:
    if not postgres_store.is_postgres_url(postgres_url):
        raise ValueError("A PostgreSQL database URL is required for migration.")
    postgres_store.init_db(postgres_url)
    stats = {"agent_events": 0, "shared_states": 0, "agent_graph": 0, "agent_graph_edges": 0}
    if not sqlite_path.exists():
        return stats

    with _get_connection(sqlite_path) as conn:
        event_rows = conn.execute(
            "SELECT project_id, agent_id, action_type, timestamp, intent, result_shape, payload FROM agent_events"
        ).fetchall()
        for row in event_rows:
            payload = _json.loads(row["payload"] or "{}")
            postgres_store.append_event(
                postgres_url,
                project_id=row["project_id"],
                agent_id=row["agent_id"],
                action_type=row["action_type"],
                timestamp=row["timestamp"],
                intent=row["intent"] or "",
                result_shape=row["result_shape"] or "",
                payload=payload,
            )
            stats["agent_events"] += 1

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "shared_states" in tables:
            rows = conn.execute("SELECT project_id, state FROM shared_states").fetchall()
            for row in rows:
                try:
                    state = _json.loads(row["state"] or "{}")
                except Exception:
                    state = {}
                postgres_store.upsert_shared_state(postgres_url, row["project_id"], state)
                stats["shared_states"] += 1

        if "agent_graph" in tables:
            rows = conn.execute("SELECT id, type, label, meta FROM agent_graph").fetchall()
            with postgres_store.connect(postgres_url) as pg_conn:
                with pg_conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            """
                            INSERT INTO agent_graph(id, type, label, meta)
                            VALUES (%s, %s, %s, %s::jsonb)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (row["id"], row["type"], row["label"], row["meta"] or "{}"),
                        )
                        stats["agent_graph"] += 1
                pg_conn.commit()

        if "agent_graph_edges" in tables:
            rows = conn.execute("SELECT id, source, target, relation, meta FROM agent_graph_edges").fetchall()
            with postgres_store.connect(postgres_url) as pg_conn:
                with pg_conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            """
                            INSERT INTO agent_graph_edges(id, source, target, relation, meta)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (row["id"], row["source"], row["target"], row["relation"], row["meta"] or "{}"),
                        )
                        stats["agent_graph_edges"] += 1
                pg_conn.commit()
    return stats
