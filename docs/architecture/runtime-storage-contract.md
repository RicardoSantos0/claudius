# Runtime Storage Contract

This document separates canonical source from active MAS runtime state and
defines safe reconciliation and cleanup behavior.

## Active Store and Project Workspaces

`mas/data/episodic.db` is the active local SQL runtime database. It stores event
history, FTS search tables, shared-state projections, graph rows, and
registry-derived tables. It is runtime state, not source, and is gitignored.

`mas/projects/` contains per-project workspaces. A `shared_state.yaml` file is the
compact operational projection; `agent_events` is the queryable event ledger.
Projects may be flat (`projects/<id>/`) or grouped
(`projects/<family>/<id>/`). Do not keep two workspaces with the same project id,
and do not treat a state-less stub as a valid project.

The configured relational backend is authoritative for both shared-state and
event operations. With SQLite, `MAS_SQLITE_FALLBACK_URL` redirects default
initialization, append, and query operations together; an explicit `db_path`
still wins. Higher-level database helpers and `EventRecorder` must defer omitted
paths to runtime configuration rather than re-passing the repository default.

The pytest suite uses a session-scoped temporary SQLite URL. Tests needing their
own database override that URL or pass an explicit path. A clean suite must
leave operator event/state counts unchanged; the cleanup tool is a detector and
recovery path, not a substitute for isolation.

## Deterministic Reconciliation

`mas sync` reconciles file state into the event ledger with deterministic
per-project keys:

- `project_initialized`;
- `phase_transition:<phase>`;
- `decision_recorded:<decision>`; and
- `project_closed`.

A second reconciliation over unchanged state must add zero events. Use
`mas sync --dry-run` before and after repairs; the final preview must report zero
additions.

## Backups, Integrity, and Recovery

`mas/data/backups/` contains gitignored local recovery copies. Maintenance that
deletes runtime rows must create a consistent SQLite online backup before
mutation. Validate recovery copies with `PRAGMA quick_check`; copying a live WAL
database file directly is not an acceptable backup procedure.

`mas doctor <project-id>` reports:

- SQLite `PRAGMA quick_check` integrity;
- deterministic state-to-event reconciliation debt;
- split-brain, ungrouped, and stub workspace layout;
- disk/state decision consistency; and
- incomplete decision records plus probable plan/board identifier duplicates; and
- semantic capability drift between registry YAML and its SQLite projection.

`mas consistency <project-id> --repair-preview` shows the plan and board IDs,
descriptions, dependencies, statuses, and similarity scores for probable
duplicates. Exact IDs remain authoritative. Ambiguous candidates are never
merged automatically, and the preview does not write either store.

Repair registry drift with the roster/capability sync tools, not by editing
SQLite rows manually.

## Optional and Generated Stores

Optional vector storage belongs under `mas/data/`:

- `mas/data/chromadb`;
- `mas/data/vector_memory.db`.

Generated build, cache, and visualization output is not canonical runtime state.
Classify it before cleanup.

## Cleanup Rule

First classify, then archive or back up, then delete, then verify. No runtime DB,
project history, vector store, or build artifact should be removed solely because
it looks cluttered. `mas/tools/purge_test_noise.py` understands nested
family/project layouts, previews exact row classification, and creates an online
backup before `--apply`.
