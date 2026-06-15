"""
Small SQLite shared-state adapter used as the SQL fallback path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _normalize_db_path(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        return db_url.replace("sqlite:///", "")
    return db_url


def _conn(db_url: str) -> sqlite3.Connection:
    # Closing-connection factory: `with _conn(...)` closes on exit (plain sqlite3
    # only commits) — avoids the ResourceWarning: unclosed database leak (Py 3.13).
    from core.utils.log_helpers import _ClosingConnection
    path = _normalize_db_path(db_url)
    conn = sqlite3.connect(path, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _init(db_url: str) -> None:
    with _conn(db_url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_states (
                project_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def upsert_shared_state(db_url: str, project_id: str, state: dict) -> None:
    _init(db_url)
    with _conn(db_url) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO shared_states (project_id, state, updated_at)
            VALUES (?, ?, ?)
            """,
            (project_id, json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )


def get_shared_state(db_url: str, project_id: str) -> dict | None:
    _init(db_url)
    with _conn(db_url) as conn:
        row = conn.execute(
            "SELECT state FROM shared_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["state"])
