"""
Runtime configuration helpers for live execution and SQL/vector backend selection.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.utils.config import ROOT, REPO_ROOT, load_config


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(raw_path: str | None, *, base: Path) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (base / candidate).resolve()
    return candidate


def get_database_backend() -> dict[str, Any]:
    cfg = load_config()
    storage = cfg.get("storage", {})
    section = storage.get("database", {})
    sqlite_url = _coalesce(
        os.getenv("MAS_SQLITE_FALLBACK_URL"),
        section.get("url"),
        "sqlite:///mas/data/episodic.db",
    )
    configured_provider = _coalesce(
        os.getenv("MAS_DATABASE_PROVIDER"),
        section.get("provider"),
        "sqlite",
    )
    target_provider = _coalesce(
        os.getenv("MAS_DATABASE_TARGET_PROVIDER"),
        section.get("target_provider"),
        "postgresql",
    )
    postgres_url = _coalesce(os.getenv("MAS_DATABASE_URL"), section.get("postgres_url"))

    if configured_provider in {"postgresql", "postgres"} and postgres_url:
        active_provider = "postgresql"
        active_url = postgres_url
    else:
        active_provider = "sqlite"
        active_url = sqlite_url

    return {
        "configured_provider": configured_provider,
        "target_provider": target_provider,
        "active_provider": active_provider,
        "url": active_url,
        "fallback_url": sqlite_url,
    }


def get_vector_backend() -> dict[str, Any]:
    cfg = load_config()
    storage = cfg.get("storage", {})
    section = storage.get("vector", {})
    persist_directory = _resolve_path(
        _coalesce(os.getenv("MAS_CHROMA_PATH"), section.get("persist_directory"), "mas/data/chromadb"),
        base=REPO_ROOT,
    ) or (ROOT / "data" / "chromadb")
    port_raw = _coalesce(os.getenv("MAS_CHROMA_PORT"), section.get("port"))
    port = int(port_raw) if port_raw not in (None, "") else None
    return {
        "provider": _coalesce(os.getenv("MAS_VECTOR_PROVIDER"), section.get("provider"), "chromadb"),
        "enabled": _as_bool(_coalesce(os.getenv("MAS_VECTOR_ENABLED"), section.get("enabled")), default=False),
        "collection": _coalesce(os.getenv("MAS_VECTOR_COLLECTION"), section.get("collection"), "mas-agent-context"),
        "persist_directory": persist_directory,
        "host": _coalesce(os.getenv("MAS_CHROMA_HOST"), section.get("host")),
        "port": port,
    }


def query_vector_context(
    project_id: str,
    agent_id: str,
    phase: str = "",
    limit: int = 5,
) -> str:
    """
    Query optional ChromaDB-backed context for an agent.

    Returns a formatted prompt section or "" when the backend is unavailable or
    not configured. This never raises.
    """
    if not project_id:
        return ""
    backend = get_vector_backend()
    if not backend["enabled"] or backend["provider"] != "chromadb":
        return ""

    try:
        import chromadb  # type: ignore
    except Exception:
        return ""

    try:
        if backend["host"]:
            client = chromadb.HttpClient(host=backend["host"], port=backend["port"] or 8000)
        else:
            backend["persist_directory"].mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(backend["persist_directory"]))
        collection = client.get_or_create_collection(backend["collection"])
        query_text = " ".join(part for part in (agent_id.replace("_", " "), phase) if part).strip() or project_id
        result = collection.query(
            query_texts=[query_text],
            n_results=max(1, limit),
            where={"project_id": project_id},
        )
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
    except Exception:
        return ""

    if not docs:
        return ""

    lines = ["## Relevant Context (from ChromaDB)"]
    for idx, doc in enumerate(docs):
        meta = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        label = meta.get("label") or meta.get("source") or f"hit-{idx + 1}"
        lines.append(f"- [{label}] {doc}")
    return "\n".join(lines)
