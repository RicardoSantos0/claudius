"""Privacy-safe, aggregate route telemetry for the SQLite runtime store.

Only the fixed metadata columns declared here are persisted. Caller-provided
prompts, responses, credentials, governed content, and arbitrary payloads are
discarded by construction.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.utils.log_helpers import DB_PATH


ROUTE_TELEMETRY_COLUMNS: tuple[str, ...] = (
    "project_id",
    "agent_id",
    "route_action_id",
    "dispatch_id",
    "provider_catalog",
    "provider",
    "model",
    "provider_reported_model",
    "provider_request_id",
    "profile",
    "phase",
    "source",
    "verification_source",
    "stable_prefix_sha256",
    "retry_count",
    "escalated",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "billable_input_tokens",
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
    dispatch_id      TEXT,
    provider_catalog TEXT,
    provider         TEXT,
    model            TEXT,
    provider_reported_model TEXT,
    provider_request_id TEXT,
    profile          TEXT,
    phase            TEXT,
    source           TEXT,
    verification_source TEXT,
    stable_prefix_sha256 TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    escalated        INTEGER NOT NULL DEFAULT 0,
    latency_ms       REAL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    cached_input_tokens INTEGER,
    cache_creation_input_tokens INTEGER,
    billable_input_tokens INTEGER,
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

_MIGRATION_COLUMNS: dict[str, str] = {
    "dispatch_id": "TEXT",
    "provider_reported_model": "TEXT",
    "provider_request_id": "TEXT",
    "verification_source": "TEXT",
    "stable_prefix_sha256": "TEXT",
    "cached_input_tokens": "INTEGER",
    "cache_creation_input_tokens": "INTEGER",
    "billable_input_tokens": "INTEGER",
}
_INTEGER_COLUMNS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "billable_input_tokens",
}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_identifier(value: Any) -> str | None:
    """Keep provider identifiers bounded and token-shaped, never free-form content."""
    if value in (None, ""):
        return None
    candidate = str(value)
    return candidate if _IDENTIFIER_RE.fullmatch(candidate) else None


def sanitize_route_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Project caller metadata onto the strict, typed telemetry allowlist."""
    values = {name: metadata.get(name) for name in ROUTE_TELEMETRY_COLUMNS}
    values["retry_count"] = max(0, int(values["retry_count"] or 0))
    values["escalated"] = bool(values["escalated"])
    values["success"] = bool(values["success"])
    for name in _INTEGER_COLUMNS:
        value = values[name]
        values[name] = None if value is None else max(0, int(value))
    for name in (
        "project_id",
        "agent_id",
        "route_action_id",
        "dispatch_id",
        "provider_catalog",
        "provider",
        "model",
        "provider_reported_model",
        "provider_request_id",
        "profile",
        "phase",
        "source",
        "verification_source",
        "error_type",
    ):
        values[name] = _safe_identifier(values[name])
    fingerprint = str(values["stable_prefix_sha256"] or "").lower()
    values["stable_prefix_sha256"] = (
        fingerprint if _SHA256_RE.fullmatch(fingerprint) else None
    )
    return values


def aggregate_cache_economics(
    rows: list[Mapping[str, Any]],
) -> list[dict[str, int | float | str | None]]:
    """Aggregate provider-evidenced cache counters by opaque stable-prefix hash."""
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        fingerprint = str(row.get("stable_prefix_sha256") or "")
        if not _SHA256_RE.fullmatch(fingerprint):
            continue
        bucket = grouped.setdefault(
            fingerprint,
            {
                "route_count": 0,
                "cache_observation_count": 0,
                "cache_hit_count": 0,
                "billable_observation_count": 0,
                "total_input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "billable_input_tokens": 0,
                "billed_token_reduction": 0,
                "comparable_input_tokens": 0,
            },
        )
        bucket["route_count"] += 1
        input_tokens = row.get("input_tokens")
        cached_tokens = row.get("cached_input_tokens")
        cache_creation = row.get("cache_creation_input_tokens")
        billable_tokens = row.get("billable_input_tokens")
        if input_tokens is not None:
            bucket["total_input_tokens"] += max(0, int(input_tokens))
        if cached_tokens is not None:
            cached_value = max(0, int(cached_tokens))
            bucket["cache_observation_count"] += 1
            bucket["cached_input_tokens"] += cached_value
            if cached_value > 0:
                bucket["cache_hit_count"] += 1
        if cache_creation is not None:
            bucket["cache_creation_input_tokens"] += max(0, int(cache_creation))
        if billable_tokens is not None:
            billable_value = max(0, int(billable_tokens))
            bucket["billable_observation_count"] += 1
            bucket["billable_input_tokens"] += billable_value
            if input_tokens is not None:
                comparable = max(0, int(input_tokens))
                bucket["comparable_input_tokens"] += comparable
                bucket["billed_token_reduction"] += max(
                    0, comparable - billable_value
                )

    result: list[dict[str, int | float | str | None]] = []
    for fingerprint, bucket in sorted(grouped.items()):
        cache_observations = bucket["cache_observation_count"]
        comparable_input = bucket.pop("comparable_input_tokens")
        item: dict[str, int | float | str | None] = {
            "stable_prefix_sha256": fingerprint,
            **bucket,
            "cache_hit_rate": (
                bucket["cache_hit_count"] / cache_observations
                if cache_observations
                else None
            ),
            "billed_token_reduction_rate": (
                bucket["billed_token_reduction"] / comparable_input
                if comparable_input
                else None
            ),
        }
        result.append(item)
    return result


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
            existing = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(route_telemetry)")
            }
            for name, sql_type in _MIGRATION_COLUMNS.items():
                if name not in existing:
                    conn.execute(
                        f"ALTER TABLE route_telemetry ADD COLUMN {name} {sql_type}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_route_telemetry_stable_prefix "
                "ON route_telemetry(stable_prefix_sha256)"
            )

    def record(self, metadata: Mapping[str, Any]) -> int:
        values = sanitize_route_metadata(metadata)
        values["escalated"] = int(values["escalated"])
        values["success"] = int(values["success"])
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
    ) -> dict[str, Any]:
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
                       COUNT(quality_score) AS quality_scored_count,
                       COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                       COALESCE(SUM(
                           CASE WHEN verification_source = 'provider'
                                THEN cached_input_tokens ELSE 0 END
                       ), 0) AS cached_input_tokens,
                       COALESCE(SUM(
                           CASE WHEN verification_source = 'provider'
                                THEN cache_creation_input_tokens ELSE 0 END
                       ), 0) AS cache_creation_input_tokens,
                       COALESCE(SUM(
                           CASE WHEN verification_source = 'provider'
                                THEN billable_input_tokens ELSE 0 END
                       ), 0) AS billable_input_tokens,
                       COUNT(CASE
                           WHEN verification_source = 'provider'
                            AND cached_input_tokens IS NOT NULL THEN 1 END
                       ) AS cache_observation_count,
                       COUNT(CASE
                           WHEN verification_source = 'provider'
                            AND cached_input_tokens > 0 THEN 1 END
                       ) AS cache_hit_count,
                       COUNT(provider_request_id) AS provider_request_count
                FROM route_telemetry
                {where}
                """,
                params,
            ).fetchone()
            cache_rows = conn.execute(
                f"""
                SELECT stable_prefix_sha256, input_tokens, cached_input_tokens,
                       cache_creation_input_tokens, billable_input_tokens
                FROM route_telemetry
                {where + (' AND ' if where else 'WHERE ')}
                      verification_source = 'provider'
                  AND stable_prefix_sha256 IS NOT NULL
                """,
                params,
            ).fetchall()
        assert row is not None
        cache_observations = int(row[15])
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
            "total_input_tokens": int(row[10]),
            "total_output_tokens": int(row[11]),
            "cached_input_tokens": int(row[12]),
            "cache_creation_input_tokens": int(row[13]),
            "billable_input_tokens": int(row[14]),
            "cache_observation_count": cache_observations,
            "cache_hit_count": int(row[16]),
            "cache_hit_rate": (
                int(row[16]) / cache_observations
                if cache_observations
                else None
            ),
            "provider_request_count": int(row[17]),
            "cache_by_stable_prefix": aggregate_cache_economics(
                [
                    {
                        "stable_prefix_sha256": cache_row[0],
                        "input_tokens": cache_row[1],
                        "cached_input_tokens": cache_row[2],
                        "cache_creation_input_tokens": cache_row[3],
                        "billable_input_tokens": cache_row[4],
                    }
                    for cache_row in cache_rows
                ]
            ),
        }
