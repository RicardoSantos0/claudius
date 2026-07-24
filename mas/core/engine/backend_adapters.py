"""
Backend adapter configuration helpers.

Purpose:
- Keep SQLite as the default active backend.
- Define stable config contracts for future PostgreSQL/ChromaDB adoption.
- Provide readiness checks without forcing optional dependencies.

Harvested from codex-mas (proj-YYYYMMDD-NNN) as part of the knowledge subsystem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from core.config import load_config

RelationalProvider = Literal["sqlite", "postgres"]
VectorProvider = Literal["sqlite", "chroma"]


class BackendReadinessError(RuntimeError):
    """Raised when configured backend dependencies are not ready."""


def _coalesce(*values: object) -> object | None:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _as_bool(value: object, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RelationalBackendConfig:
    provider: RelationalProvider
    url: str


@dataclass(frozen=True)
class VectorBackendConfig:
    provider: VectorProvider
    enabled: bool
    sqlite_url: str
    chroma_persist_directory: str
    chroma_collection: str


def _canonical_relational_provider(raw: str) -> RelationalProvider:
    value = (raw or "sqlite").strip().lower()
    if value in ("postgresql", "postgres", "pg"):
        return "postgres"
    return "sqlite"


def _canonical_vector_provider(raw: str) -> VectorProvider:
    value = (raw or "chromadb").strip().lower()
    if value in ("chroma", "chromadb"):
        return "chroma"
    return "sqlite"


def resolve_relational_backend(config: dict | None = None) -> RelationalBackendConfig:
    cfg = config or load_config()
    storage = cfg.get("storage", {})
    db_cfg = storage.get("database", {})

    provider_raw = _coalesce(
        os.getenv("MAS_DATABASE_PROVIDER"),
        storage.get("provider"),
        db_cfg.get("provider"),
        "sqlite",
    )
    provider = _canonical_relational_provider(str(provider_raw))
    url = str(_coalesce(os.getenv("MAS_SQLITE_FALLBACK_URL"), db_cfg.get("url"), "sqlite:///data/mas.db"))
    return RelationalBackendConfig(provider=provider, url=url)


def resolve_vector_backend(config: dict | None = None) -> VectorBackendConfig:
    cfg = config or load_config()
    storage = cfg.get("storage", {}) if isinstance(cfg.get("storage"), dict) else {}
    vector = storage.get("vector", {}) if isinstance(storage.get("vector"), dict) else {}
    mem = cfg.get("memory", {})
    provider_raw = _coalesce(os.getenv("MAS_VECTOR_PROVIDER"), vector.get("provider"), mem.get("provider"), "chromadb")
    provider = _canonical_vector_provider(str(provider_raw))

    enabled = _as_bool(_coalesce(os.getenv("MAS_VECTOR_ENABLED"), vector.get("enabled")), default=False)

    sqlite_cfg = mem.get("sqlite", {}) if isinstance(mem.get("sqlite"), dict) else {}
    chroma_cfg = mem.get("chroma", {}) if isinstance(mem.get("chroma"), dict) else {}
    chroma_dir = str(
        _coalesce(
            os.getenv("MAS_CHROMA_PATH"),
            vector.get("persist_directory"),
            chroma_cfg.get("persist_directory"),
            "mas/data/chromadb",
        )
    )
    chroma_collection = str(
        _coalesce(
            os.getenv("MAS_VECTOR_COLLECTION"),
            vector.get("collection"),
            chroma_cfg.get("collection"),
            "mas-agent-context",
        )
    )
    return VectorBackendConfig(
        provider=provider,
        enabled=enabled,
        sqlite_url=str(sqlite_cfg.get("url", "sqlite:///mas/data/vector_memory.db")),
        chroma_persist_directory=chroma_dir,
        chroma_collection=chroma_collection,
    )


def relational_backend_ready(config: dict | None = None) -> tuple[bool, str]:
    backend = resolve_relational_backend(config)
    if backend.provider == "sqlite":
        return True, "sqlite ready (active default)"

    try:
        import psycopg  # noqa: F401
    except Exception:
        return False, "postgres selected but psycopg is not installed (install claudius[postgres])"
    return True, "postgres readiness check passed"


def vector_backend_ready(config: dict | None = None) -> tuple[bool, str]:
    backend = resolve_vector_backend(config)
    if not backend.enabled:
        return True, "vector backend configured but disabled"

    if backend.provider == "sqlite":
        return True, "sqlite vector store ready (active default)"

    try:
        import chromadb  # noqa: F401
    except Exception:
        return False, "chroma selected but chromadb is not installed (install claudius[chroma])"
    return True, "chroma readiness check passed"


def require_ready_backends(config: dict | None = None) -> None:
    """Fail fast when configured backends are not ready for runtime use."""
    rel = resolve_relational_backend(config)
    vec = resolve_vector_backend(config)
    rel_ok, rel_msg = relational_backend_ready(config)
    vec_ok, vec_msg = vector_backend_ready(config)

    failures: list[str] = []
    if not rel_ok:
        failures.append(f"relational backend '{rel.provider}' not ready: {rel_msg}")
    if vec.enabled and not vec_ok:
        failures.append(f"vector backend '{vec.provider}' not ready: {vec_msg}")

    if failures:
        raise BackendReadinessError("; ".join(failures))
