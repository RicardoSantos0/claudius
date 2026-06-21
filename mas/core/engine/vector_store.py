"""
Vector memory adapters.

SQLite is the active default. Chroma is supported as an optional backend when
`chromadb` is installed and selected in config.

Harvested from codex-mas (proj-YYYYMMDD-NNN) as part of the knowledge subsystem.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.engine.backend_adapters import resolve_vector_backend
from core.config import REPO_ROOT


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _normalize_sqlite_url(sqlite_url: str) -> Path:
    if sqlite_url.startswith("sqlite:///"):
        rel = sqlite_url.replace("sqlite:///", "")
        p = Path(rel)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()
    p = Path(sqlite_url)
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


@dataclass
class VectorHit:
    id: str
    project_id: str
    text: str
    metadata: dict[str, Any]
    score: float


class SqliteVectorStore:
    def __init__(self, sqlite_url: str):
        self.db_path = _normalize_sqlite_url(sqlite_url)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vector_memory (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    text_content TEXT NOT NULL,
                    metadata_json TEXT,
                    embedding_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vector_memory_project
                ON vector_memory(project_id);
                """
            )

    def upsert(
        self,
        *,
        id: str,
        project_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        meta_raw = json.dumps(metadata or {}, ensure_ascii=False)
        emb_raw = json.dumps(embedding or [], ensure_ascii=False)
        now = _utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vector_memory
                    (id, project_id, text_content, metadata_json, embedding_json, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id=excluded.project_id,
                    text_content=excluded.text_content,
                    metadata_json=excluded.metadata_json,
                    embedding_json=excluded.embedding_json,
                    updated_at=excluded.updated_at
                """,
                (id, project_id, text, meta_raw, emb_raw, now, now),
            )
            conn.commit()

    def query(self, *, project_id: str, text_query: str, limit: int = 5) -> list[VectorHit]:
        pattern = f"%{text_query.strip()}%" if text_query else "%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, text_content, metadata_json
                FROM vector_memory
                WHERE project_id = ? AND text_content LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (project_id, pattern, limit),
            ).fetchall()

        hits: list[VectorHit] = []
        for r in rows:
            try:
                metadata = json.loads(r["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            score = 1.0 if text_query and text_query.lower() in r["text_content"].lower() else 0.5
            hits.append(
                VectorHit(
                    id=str(r["id"]),
                    project_id=str(r["project_id"]),
                    text=str(r["text_content"]),
                    metadata=metadata if isinstance(metadata, dict) else {},
                    score=score,
                )
            )
        return hits


class ChromaVectorStore:
    def __init__(self, persist_directory: str, collection: str):
        try:
            import chromadb
        except Exception as exc:
            raise RuntimeError(
                "Chroma backend selected but chromadb is not installed. Install claudius[chroma]."
            ) from exc
        self._client = chromadb.PersistentClient(path=str((REPO_ROOT / persist_directory).resolve()))
        self._collection = self._client.get_or_create_collection(name=collection)

    def upsert(
        self,
        *,
        id: str,
        project_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        md = {"project_id": project_id, **(metadata or {})}
        kwargs: dict[str, Any] = {
            "ids": [id],
            "documents": [text],
            "metadatas": [md],
        }
        if embedding:
            kwargs["embeddings"] = [embedding]
        self._collection.upsert(**kwargs)

    def query(self, *, project_id: str, text_query: str, limit: int = 5) -> list[VectorHit]:
        res = self._collection.query(
            query_texts=[text_query or ""],
            n_results=limit,
            where={"project_id": project_id},
        )
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        hits: list[VectorHit] = []
        for i, doc, md, dist in zip(ids, docs, metas, dists):
            score = 1.0 / (1.0 + float(dist or 0.0))
            hits.append(
                VectorHit(
                    id=str(i),
                    project_id=str((md or {}).get("project_id", project_id)),
                    text=str(doc or ""),
                    metadata=md or {},
                    score=score,
                )
            )
        return hits


def build_vector_store(config: dict | None = None):
    backend = resolve_vector_backend(config)
    if backend.provider == "chroma":
        return ChromaVectorStore(
            persist_directory=backend.chroma_persist_directory,
            collection=backend.chroma_collection,
        )
    return SqliteVectorStore(sqlite_url=backend.sqlite_url)
