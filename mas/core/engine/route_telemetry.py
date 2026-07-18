"""Privacy-safe, aggregate route telemetry for the SQLite runtime store.

Only the fixed metadata columns declared here are persisted. Caller-provided
prompts, responses, credentials, governed content, and arbitrary payloads are
discarded by construction.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.utils.log_helpers import DB_PATH


ROUTE_TELEMETRY_COLUMNS: tuple[str, ...] = (
    "project_id",
    "agent_id",
    "route_action_id",
    "provider_catalog",
    "provider",
    "model",
    "profile",
    "phase",
    "source",
    "retry_count",
    "escalated",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "quality_score",
    "success",
    "error_type",
)

ROUTE_TELEMETRY_SQL = """
CREATE TABLE IF NOT EXISTS route_telemetry (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    project_id       TEXT,
    agent_id         TEXT,
    route_action_id  TEXT,
    provider_catalog TEXT,
    provider         TEXT,
    model            TEXT,
    profile          TEXT,
    phase            TEXT,
    source           TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    escalated        INTEGER NOT NULL DEFAULT 0,
    latency_ms       REAL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    cost_usd         REAL,
    quality_score    REAL,
    success          INTEGER NOT NULL DEFAULT 0,
    error_type       TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_telemetry_catalog_profile
    ON route_telemetry(provider_catalog, profile);
CREATE INDEX IF NOT EXISTS idx_route_telemetry_timestamp
    ON route_telemetry(timestamp);
"""


class RouteTelemetryStore:
    """Store and aggregate a strict allowlist of route-call metadata."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _ensure_schema(self) -> None:
        # Deliberately migrate only this table. Legacy databases may have older
        # event-table shapes that the general runtime initializer must not touch.
        with self._connect() as conn:
            conn.executescript(ROUTE_TELEMETRY_SQL)

    def record(self, metadata: Mapping[str, Any]) -> int:
        values = {name: metadata.get(name) for name in ROUTE_TELEMETRY_COLUMNS}
        values["retry_count"] = max(0, int(values["retry_count"] or 0))
        values["escalated"] = int(bool(values["escalated"]))
        values["success"] = int(bool(values["success"]))
        placeholders = ", ".join("?" for _ in ROUTE_TELEMETRY_COLUMNS)
        columns = ", ".join(ROUTE_TELEMETRY_COLUMNS)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO route_telemetry (timestamp, {columns}) "
                f"VALUES (?, {placeholders})",
                (
                    str(metadata.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    *(values[name] for name in ROUTE_TELEMETRY_COLUMNS),
                ),
            )
            return int(cursor.lastrowid)

    def aggregate(
        self,
        *,
        provider_catalog: str | None = None,
        profile: str | None = None,
    ) -> dict[str, int | float | None]:
        clauses: list[str] = []
        params: list[Any] = []
        if provider_catalog is not None:
            clauses.append("provider_catalog = ?")
            params.append(provider_catalog)
        if profile is not None:
            clauses.append("profile = ?")
            params.append(profile)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS route_count,
                       COALESCE(SUM(success), 0) AS success_count,
                       AVG(success) AS success_rate,
                       COALESCE(SUM(retry_count), 0) AS retry_count,
                       COALESCE(SUM(escalated), 0) AS escalation_count,
                       SUM(cost_usd) AS total_cost_usd,
                       COUNT(cost_usd) AS priced_route_count,
                       AVG(latency_ms) AS average_latency_ms,
                       AVG(quality_score) AS average_quality_score,
                       COUNT(quality_score) AS quality_scored_count
                FROM route_telemetry
                {where}
                """,
                params,
            ).fetchone()
        assert row is not None
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
