"""
Minimal PostgreSQL adapter for MAS event and shared-state storage.

This is intentionally small and optional. It activates only when a PostgreSQL
URL is configured and `psycopg` is installed.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional


def is_postgres_url(db_url: str | None) -> bool:
    return bool(db_url and db_url.startswith(("postgresql://", "postgres://")))


@contextmanager
def connect(db_url: str) -> Iterator[object]:
    try:
        import psycopg  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency-gated
        raise RuntimeError("psycopg is required for PostgreSQL backend support") from exc

    conn = psycopg.connect(db_url)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_url: str) -> None:
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_events (
                    id BIGSERIAL PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    intent TEXT,
                    result_shape TEXT,
                    payload JSONB
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_project ON agent_events(project_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_agent ON agent_events(agent_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_action ON agent_events(action_type)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_states (
                    project_id TEXT PRIMARY KEY,
                    state JSONB NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_graph (
                    id TEXT PRIMARY KEY,
                    type TEXT,
                    label TEXT,
                    meta JSONB
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_graph_edges (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    target TEXT,
                    relation TEXT,
                    meta JSONB
                )
                """
            )
        conn.commit()


def append_event(
    db_url: str,
    *,
    project_id: str,
    agent_id: str,
    action_type: str,
    timestamp: str,
    intent: str,
    result_shape: str,
    payload: dict,
) -> str:
    init_db(db_url)
    action_id = payload.get("id") or ""
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_events(project_id, agent_id, action_type, timestamp, intent, result_shape, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (project_id, agent_id, action_type, timestamp, intent, result_shape, json.dumps(payload)),
            )
        conn.commit()
    return action_id


def query_events(
    db_url: str,
    *,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    init_db(db_url)
    clauses = []
    params: list[object] = []
    if project_id:
        clauses.append("project_id = %s")
        params.append(project_id)
    if agent_id:
        clauses.append("agent_id = %s")
        params.append(agent_id)
    if action_type:
        clauses.append("action_type = %s")
        params.append(action_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT id, project_id, agent_id, action_type, timestamp, intent, result_shape, payload "
                f"FROM agent_events {where} ORDER BY id DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
    normalized = []
    for row in rows:
        item = dict(row)
        payload = item.get("payload")
        if payload is not None and not isinstance(payload, str):
            item["payload"] = json.dumps(payload)
        normalized.append(item)
    return normalized


def query_by_action_id(db_url: str, action_id: str) -> Optional[dict]:
    init_db(db_url)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, project_id, agent_id, action_type, timestamp, intent, result_shape, payload
                FROM agent_events
                WHERE payload->>'id' = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (action_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get("payload") is not None and not isinstance(item["payload"], str):
        item["payload"] = json.dumps(item["payload"])
    return item


def upsert_shared_state(db_url: str, project_id: str, state: dict) -> None:
    init_db(db_url)
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shared_states(project_id, state, updated_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (project_id)
                DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at
                """,
                (project_id, json.dumps(state), now),
            )
        conn.commit()


def get_shared_state(db_url: str, project_id: str) -> Optional[dict]:
    init_db(db_url)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT state FROM shared_states WHERE project_id = %s",
                (project_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    state = row.get("state")
    if isinstance(state, str):
        return json.loads(state)
    return state


def query_graph_node(db_url: str, node_id: str) -> Optional[dict]:
    init_db(db_url)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, type, label, meta FROM agent_graph WHERE id = %s",
                (node_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    item = dict(row)
    if item.get("meta") is not None and not isinstance(item["meta"], str):
        item["meta"] = json.dumps(item["meta"])
    return item


def query_graph_edges(db_url: str, node_id: str, limit: int = 10) -> list[dict]:
    init_db(db_url)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, source, target, relation, meta
                FROM agent_graph_edges
                WHERE source = %s OR target = %s
                LIMIT %s
                """,
                (node_id, node_id, limit),
            )
            rows = cur.fetchall()
    normalized = []
    for row in rows:
        item = dict(row)
        if item.get("meta") is not None and not isinstance(item["meta"], str):
            item["meta"] = json.dumps(item["meta"])
        normalized.append(item)
    return normalized


def semantic_search(db_url: str, query: str, project_id: str | None = None, limit: int = 5) -> list[dict]:
    init_db(db_url)
    if not query or not query.strip():
        return []
    clauses = ["(intent ILIKE %s OR CAST(payload AS TEXT) ILIKE %s)"]
    params: list[object] = [f"%{query}%", f"%{query}%"]
    if project_id:
        clauses.append("project_id = %s")
        params.append(project_id)
    params.append(limit)
    where = " AND ".join(clauses)
    with connect(db_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT id, project_id, agent_id, action_type, timestamp, intent, result_shape, payload
                FROM agent_events
                WHERE {where}
                ORDER BY id DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    normalized = []
    for row in rows:
        item = dict(row)
        if item.get("payload") is not None and not isinstance(item["payload"], str):
            item["payload"] = json.dumps(item["payload"])
        normalized.append(item)
    return normalized


def dict_row(cursor):  # pragma: no cover - tiny adapter
    columns = [col.name for col in cursor.description]
    def _make(row):
        return dict(zip(columns, row))
    return _make
