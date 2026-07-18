"""
Minimal PostgreSQL adapter for MAS event and shared-state storage.

This is intentionally small and optional. It activates only when a PostgreSQL
URL is configured and `psycopg` is installed.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Optional


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
                CREATE TABLE IF NOT EXISTS route_telemetry (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    project_id TEXT,
                    agent_id TEXT,
                    route_action_id TEXT,
                    provider_catalog TEXT,
                    provider TEXT,
                    model TEXT,
                    profile TEXT,
                    phase TEXT,
                    source TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    escalated BOOLEAN NOT NULL DEFAULT FALSE,
                    latency_ms DOUBLE PRECISION,
                    input_tokens BIGINT,
                    output_tokens BIGINT,
                    cost_usd DOUBLE PRECISION,
                    quality_score DOUBLE PRECISION,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    error_type TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_route_telemetry_catalog_profile "
                "ON route_telemetry(provider_catalog, profile)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_route_telemetry_timestamp "
                "ON route_telemetry(timestamp)"
            )
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


def record_route_telemetry(db_url: str, metadata: Mapping[str, Any]) -> int:
    """Persist only the fixed route-telemetry metadata columns."""
    from core.engine.route_telemetry import ROUTE_TELEMETRY_COLUMNS

    init_db(db_url)
    values = {name: metadata.get(name) for name in ROUTE_TELEMETRY_COLUMNS}
    values["retry_count"] = max(0, int(values["retry_count"] or 0))
    values["escalated"] = bool(values["escalated"])
    values["success"] = bool(values["success"])
    columns = ", ".join(ROUTE_TELEMETRY_COLUMNS)
    placeholders = ", ".join("%s" for _ in ROUTE_TELEMETRY_COLUMNS)
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO route_telemetry (timestamp, {columns}) "
                f"VALUES (%s, {placeholders}) RETURNING id",
                (
                    str(metadata.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    *(values[name] for name in ROUTE_TELEMETRY_COLUMNS),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row[0])


def aggregate_route_telemetry(
    db_url: str,
    *,
    provider_catalog: str | None = None,
    profile: str | None = None,
) -> dict[str, int | float | None]:
    """Aggregate the same privacy-safe route columns exposed by SQLite."""
    init_db(db_url)
    clauses: list[str] = []
    params: list[object] = []
    if provider_catalog is not None:
        clauses.append("provider_catalog = %s")
        params.append(provider_catalog)
    if profile is not None:
        clauses.append("profile = %s")
        params.append(profile)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS route_count,
                       COALESCE(SUM(CASE WHEN success THEN 1 ELSE 0 END), 0)
                           AS success_count,
                       AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) AS success_rate,
                       COALESCE(SUM(retry_count), 0) AS retry_count,
                       COALESCE(SUM(CASE WHEN escalated THEN 1 ELSE 0 END), 0)
                           AS escalation_count,
                       SUM(cost_usd) AS total_cost_usd,
                       COUNT(cost_usd) AS priced_route_count,
                       AVG(latency_ms) AS average_latency_ms,
                       AVG(quality_score) AS average_quality_score,
                       COUNT(quality_score) AS quality_scored_count
                FROM route_telemetry
                {where}
                """,
                params,
            )
            row = cur.fetchone()
    return {
        "route_count": int(row[0]),
        "success_count": int(row[1]),
        "success_rate": float(row[2]) if row[2] is not None else None,
        "retry_count": int(row[3]),
        "escalation_count": int(row[4]),
        "total_cost_usd": float(row[5]) if row[5] is not None else None,
        "priced_route_count": int(row[6]),
        "average_latency_ms": row[7],
        "average_quality_score": row[8],
        "quality_scored_count": int(row[9]),
    }


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
