"""
MAS DB Init

Initializes the configured SQL backend. ChromaDB is optional and not required
for basic runtime startup.
"""

from .log_helpers import init_db, DB_PATH


def main() -> None:
    from core.runtime_config import get_database_backend, get_vector_backend
    backend = get_database_backend()
    print(f"Initialising MAS SQL backend: {backend['active_provider']} -> {backend['url']}")
    init_db(db_url=backend["url"])
    print("[ok] SQL backend ready")
    vector = get_vector_backend()
    if vector["enabled"]:
        print(f"[ok] ChromaDB enabled ({vector['provider']}) -> {vector['persist_directory']}")
    else:
        print("[ok] Vector backend disabled")
    print("DB init complete.")


if __name__ == "__main__":
    main()
