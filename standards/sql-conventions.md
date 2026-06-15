# SQL Conventions

**Type:** Advisory
**Applies to:** All SQL and database code in `mas/`

---

## Migration Policy

- All schema changes go through migration files in `mas/data/migrations/`
- Migration files are numbered sequentially: `001_init.sql`, `002_add_episodes.sql`
- Migrations are applied in order; never modify an applied migration
- Every migration must be reversible (include a `-- rollback:` comment) unless a rollback is genuinely impossible

---

## Schema Naming

| Object | Convention | Example |
|--------|------------|---------|
| Tables | `snake_case`, plural | `episodes`, `handoff_records` |
| Columns | `snake_case` | `project_id`, `created_at` |
| Primary keys | `id` (integer autoincrement) or `<table_singular>_id` | `id`, `episode_id` |
| Foreign keys | `<referenced_table_singular>_id` | `project_id`, `agent_id` |
| Indexes | `idx_<table>_<column(s)>` | `idx_episodes_project_id` |
| Unique constraints | `uq_<table>_<column(s)>` | `uq_agents_agent_id` |

---

## Required Columns

Every table should include:

```sql
id          INTEGER PRIMARY KEY AUTOINCREMENT,
created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
```

Use `TEXT` for timestamps in SQLite (ISO 8601 format).

---

## Indexes

- Index every foreign key column
- Index columns used in frequent `WHERE` clauses
- Do not over-index — each index has write overhead

---

## Transactions

- Wrap multi-statement operations in explicit transactions
- Use `BEGIN IMMEDIATE` for write-heavy transactions in SQLite
- Rollback on any error; never leave partial writes committed

---

## Parameterized Queries

**Always** use parameterized queries. Never use string formatting for SQL:

```python
# CORRECT
cursor.execute("SELECT * FROM episodes WHERE project_id = ?", (project_id,))

# WRONG — SQL injection risk
cursor.execute(f"SELECT * FROM episodes WHERE project_id = '{project_id}'")
```

---

## SQLite/Postgres Compatibility

- The MAS uses SQLite by default
- Write SQL that is compatible with both SQLite and Postgres where possible
- Avoid SQLite-specific functions in shared code; use an abstraction layer if Postgres support is needed
- SQLite thread safety: use `check_same_thread=False` with connection pooling or per-request connections

---

## Backups

- Never write directly to the production database from scripts without a backup check
- Before destructive migrations: `cp mas/data/mas.db mas/data/mas.db.bak`
