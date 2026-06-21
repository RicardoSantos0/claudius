"""Knowledge synchronization for DB-first reference retrieval.

Indexes governance docs and code files into ``knowledge_index`` with a
``codebase`` dimension so agents can resolve references by repository/project.

Harvested from codex-mas (proj-YYYYMMDD-NNN) and adapted to claude-config:
the index lives in the SQLite episodic store (``mas/data/episodic.db``) alongside
the other registry tables, written through ``log_helpers._get_connection``. The
default codebase label is derived from the repo directory name, so the same code
labels rows ``claude-config`` in the private repo and ``claudius`` in the public
one without any per-repo edits.
"""

from __future__ import annotations

import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, Iterable, Tuple

from core.config import REPO_ROOT, load_config
from core.utils.log_helpers import _get_connection, DB_PATH

#: Codebase label for rows that belong to this repository's own source/docs.
LOCAL_CODEBASE = REPO_ROOT.name


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ensure_knowledge_schema(conn) -> None:
    """Create/migrate knowledge_index so codebase-scoped rows are supported (SQLite)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info('knowledge_index')")
    cols = cursor.fetchall()
    if not cols:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_index (
                codebase TEXT NOT NULL,
                path_id TEXT NOT NULL,
                category TEXT,
                content TEXT,
                metadata TEXT,
                last_synced_at TEXT,
                PRIMARY KEY (codebase, path_id)
            )
            """
        )
        conn.commit()
        return

    has_codebase = any(r[1] == "codebase" for r in cols)
    has_composite_pk = any(r[1] == "codebase" and r[5] > 0 for r in cols)
    if has_codebase and has_composite_pk:
        return

    # Migrate a pre-codebase table to the codebase-scoped shape.
    cursor.execute("ALTER TABLE knowledge_index RENAME TO knowledge_index_old")
    cursor.execute(
        """
        CREATE TABLE knowledge_index (
            codebase TEXT NOT NULL,
            path_id TEXT NOT NULL,
            category TEXT,
            content TEXT,
            metadata TEXT,
            last_synced_at TEXT,
            PRIMARY KEY (codebase, path_id)
        )
        """
    )
    if has_codebase:
        cursor.execute(
            f"""
            INSERT OR REPLACE INTO knowledge_index
                (codebase, path_id, category, content, metadata, last_synced_at)
            SELECT
                COALESCE(codebase, '{LOCAL_CODEBASE}'),
                path_id, category, content, metadata, last_synced_at
            FROM knowledge_index_old
            """
        )
    else:
        cursor.execute(
            f"""
            INSERT OR REPLACE INTO knowledge_index
                (codebase, path_id, category, content, metadata, last_synced_at)
            SELECT
                '{LOCAL_CODEBASE}',
                path_id, category, content, metadata, last_synced_at
            FROM knowledge_index_old
            """
        )
    cursor.execute("DROP TABLE knowledge_index_old")
    conn.commit()


def _iter_files(root: Path, exts: tuple[str, ...]) -> Iterable[Path]:
    if not root.exists():
        return []
    for dirpath, _, filenames in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for filename in filenames:
            if filename.endswith(exts):
                yield Path(dirpath) / filename


def _doc_targets() -> Dict[str, Path]:
    mas = REPO_ROOT / "mas"
    return {
        "policies": mas / "policies",
        "foundation": mas / "foundation",
        "domains": mas / "domains",
        "roster": mas / "roster",
    }


def _code_targets() -> Iterable[Tuple[str, Path]]:
    # This repo's own engine code + scripts/tools/tests as one codebase.
    local_roots = [
        REPO_ROOT / "mas" / "core",
        REPO_ROOT / "scripts",
        REPO_ROOT / "mas" / "tools",
        REPO_ROOT / "mas" / "tests",
    ]
    for root in local_roots:
        if root.exists():
            yield (LOCAL_CODEBASE, root)

    # Prior projects each treated as their own codebase id.
    projects_dir = REPO_ROOT / "mas" / "projects"
    if not projects_dir.exists():
        return
    for child in projects_dir.iterdir():
        if child.is_dir() and child.name.startswith("proj-"):
            yield (child.name, child)


def sync_all_knowledge() -> Dict[str, int]:
    """Sync docs + code into the SQLite knowledge_index with codebase-aware rows."""
    load_config()  # touch config so misconfiguration surfaces early
    conn = _get_connection(DB_PATH)
    try:
        _ensure_knowledge_schema(conn)
        cursor = conn.cursor()

        stats = {"policies": 0, "foundation": 0, "domains": 0, "roster": 0, "code": 0}
        now = _utc_now_iso()
        upsert_sql = (
            "INSERT OR REPLACE INTO knowledge_index "
            "(codebase, path_id, category, content, metadata, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )

        # Governance/knowledge docs (this repo's codebase)
        for category, root in _doc_targets().items():
            for full_path in _iter_files(root, (".md", ".yaml", ".yml")):
                rel_path = full_path.relative_to(REPO_ROOT)
                path_id = str(rel_path).replace("\\", "/")
                try:
                    content = full_path.read_text(encoding="utf-8")
                    metadata = {
                        "filename": full_path.name,
                        "extension": full_path.suffix,
                        "size": full_path.stat().st_size,
                        "kind": "document",
                    }
                    cursor.execute(
                        upsert_sql,
                        (LOCAL_CODEBASE, path_id, category, content, json.dumps(metadata), now),
                    )
                    stats[category] += 1
                except Exception as exc:
                    print(f"Error syncing {path_id}: {exc}")

        # Code files for this repo + prior projects
        code_exts = (".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".sh", ".ps1", ".sql")
        for codebase, root in _code_targets():
            for full_path in _iter_files(root, code_exts):
                if codebase == LOCAL_CODEBASE:
                    rel_path = full_path.relative_to(REPO_ROOT)
                else:
                    rel_path = full_path.relative_to(root)
                path_id = str(rel_path).replace("\\", "/")
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    metadata = {
                        "filename": full_path.name,
                        "extension": full_path.suffix,
                        "size": full_path.stat().st_size,
                        "kind": "code",
                        "root": str(root.relative_to(REPO_ROOT)).replace("\\", "/"),
                    }
                    cursor.execute(
                        upsert_sql,
                        (codebase, path_id, "code", content, json.dumps(metadata), now),
                    )
                    stats["code"] += 1
                except Exception as exc:
                    print(f"Error syncing {codebase}/{path_id}: {exc}")

        conn.commit()
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    results = sync_all_knowledge()
    print(f"Sync complete: {results}")
