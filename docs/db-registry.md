# DB Registry — Architecture Reference

Introduced in proj-YYYYMMDD-NNN-db-registry (2026-05-09).

`mas/data/episodic.db` contains 6 registry tables that index MAS architecture
artifacts. These tables are the primary lookup source at runtime; the filesystem
is the fallback when the DB is unavailable or a row is missing.

---

## Tables

### `mas_agents`

Indexes every agent template under `agents/*.md`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Agent identifier (e.g. `canonical_engineer`) |
| `tier` | TEXT | Trust tier: T0, T1, T2, infrastructure |
| `template_path` | TEXT | Absolute path to the `.md` file |
| `tools` | TEXT | Comma-separated tool list from frontmatter |
| `status` | TEXT | `active` or `retired` |
| `last_score` | REAL | Most recent evaluator score (0.0–1.0) |
| `evaluation_count` | INTEGER | Number of evaluation cycles completed |
| `last_evaluated_at` | TEXT | ISO-8601 timestamp of last evaluation |
| `evaluation_summary` | TEXT | Short human-readable summary of last eval |

### `mas_skills`

Indexes every skill package under `skills/*/SKILL.md`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Skill identifier |
| `skill_path` | TEXT | Absolute path to `SKILL.md` |
| `package_dir` | TEXT | Parent directory of the skill package |
| `description` | TEXT | First paragraph from `SKILL.md` |

### `mas_commands`

Indexes every slash command under `commands/*.md`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Command name (without leading `/`) |
| `command_path` | TEXT | Absolute path to the `.md` file |
| `description` | TEXT | First paragraph from the file |

### `mas_templates`

Indexes YAML templates under `mas/templates/*.yaml`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Template identifier (filename without extension) |
| `template_path` | TEXT | Absolute path to the `.yaml` file |
| `template_type` | TEXT | Inferred type: `handoff`, `spawn`, `eval`, `other` |

### `mas_domains`

Indexes domain context files under `mas/domains/*.md`.

| Column | Type | Notes |
|--------|------|-------|
| `name` | TEXT PK | Domain name (e.g. `software_engineering`) |
| `domain_path` | TEXT | Absolute path to the `.md` file |
| `description` | TEXT | First paragraph from the file |

### `mas_codebase`

Indexes MAS Python source files under `mas/core/` (114 rows at initial seed).

| Column | Type | Notes |
|--------|------|-------|
| `file_path` | TEXT PK | Absolute path to the `.py` file |
| `module_name` | TEXT | Dotted module name (e.g. `mas.core.engine.handoff_engine`) |
| `project_id` | TEXT | Always `mas_core` for engine files |
| `file_type` | TEXT | `engine`, `utility`, `cli`, `test`, `other` |
| `last_modified` | TEXT | ISO-8601 mtime at seed time |

---

## Seed Script

**File**: `mas/core/utils/registry_seed.py`

The seed script is idempotent — safe to run repeatedly. It walks the filesystem,
upserts rows into all 6 tables, and skips rows whose paths have not changed.

### Usage

```bash
# CLI
uv run mas registry seed

# Library
from mas.core.utils.registry_seed import seed
seed()
```

Auto-sync: `seed()` is called automatically when `mas close <project-id>` runs,
keeping the registry current after each project cycle.

---

## DB-First Runtime Pattern

Three engine modules query the registry before falling back to the filesystem:

| Module | Table queried | Fallback |
|--------|---------------|---------|
| `prompt_assembler.py` (`_db_template_path`) | `mas_agents` | `agents/*.md` glob |
| `skill_bridge.py` (`_db_skills`) | `mas_skills` | `skills/*/SKILL.md` glob |
| `capability_registry.py` (`_db_agents`, `list_agents`) | `mas_agents` | `registry_index.yaml` |

The pattern keeps runtime lookups fast (indexed SQL) while ensuring the system
degrades gracefully if the DB file is absent or stale.

---

## CLI Inspection

```bash
# Re-seed all 6 tables from filesystem
uv run mas registry seed

# List all registry contents (all tables)
uv run mas registry list

# Inspect a specific table
uv run mas registry list --table mas_agents
uv run mas registry list --table mas_skills
uv run mas registry list --table mas_commands
uv run mas registry list --table mas_templates
uv run mas registry list --table mas_domains
uv run mas registry list --table mas_codebase
```
