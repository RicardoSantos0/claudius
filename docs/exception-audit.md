# Broad-Exception Audit — `mas/core`

**Project:** proj-YYYYMMDD-NNN-mas-quality-hardening · **Requirement:** P1 / req-001
**Policy source:** consultation `consult-...-28613b8c` (core-three, no escalation)
**Date:** 2026-06-03

> **STATUS: COMPLETE (2026-06-03).** All 48 silent `except: pass` sites across 12
> modules resolved — 43 converted to `logger.debug(...)` (control flow unchanged),
> 5 kept as intentional commented passes (dotenv/stdout import-guards, audit-logger
> rotation infra, idempotent DDL column-add). Repo-wide: **0 bare silent-pass** in
> `mas/core`. Shipped: `cfb02dd`, `8a91ac7`, `fa8fa86`, `5518a23`. Suite 1346 passed
> @ 70.74% coverage. The "Follow-up" section below is the record of how it was done.

## Policy (approved)

| Class | Treatment |
|-------|-----------|
| **contract-critical** | Raise a typed error (governance/state/contract violations must fail loudly). |
| **optional / best-effort** | Keep non-blocking, but make it **observable** — `logger.debug/​warning` with context, never a silent `pass`. |
| **accidental / over-broad** | Narrow to the specific exception, or remove. |

Fix in small **per-module batches** with the full suite green between each.

## Census (live count — note the drift from the review)

The 2026-06-01 review estimated "broad swallowing" loosely; the live `mas/core` has:

| Metric | Count |
|--------|------:|
| Total broad `except Exception` / bare `except` | **124** |
| — silent `pass` (swallow, not observable) | **48** |
| — has a handler (logs / returns / re-raises) | **76** |

**By module:**

| Module | Broad catches | Dominant purpose |
|--------|--------------:|------------------|
| `cli.py` | 30 | CLI best-effort UX (optional imports, checkpoint/seed, display fallbacks) |
| `engine/orchestration_loop.py` | 29 | Loop resilience — a single agent step must not crash the loop (optional) |
| `engine/handoff_engine.py` | 9 | Telemetry around handoffs (episode/event recording, wire metrics) — optional |
| `db.py` | 8 | DB/driver best-effort (FTS, optional backends) — optional |
| `engine/shared_state_manager.py` | 7 | Mix: state-save guards (contract) + audit logging (optional) |
| `utils/registry_seed.py` | 7 | Best-effort DB index seeding — optional |
| `engine/prompt_assembler.py` | 6 | Context-projection fallbacks (graph/vector/event) — optional |
| `engine/capability_registry.py` | 4 | Registry read/write best-effort — optional + 1 contract |
| `engine/skill_bridge.py` | 4 | Skill discovery/auth fallbacks — optional |
| `agent_runner.py` (3), `metrics_engine.py` (3), `training_engine.py` (3) | 9 | Telemetry / optional SDK paths |
| 11 other modules | 1 each | Mostly optional telemetry |

## Classification

The overwhelming majority are **optional / best-effort** by design — most already carry an
in-code comment ("must never block", "best-effort", "non-fatal"). The concern from the review
(§6.8) is not that they catch broadly, but that **48 are silent `pass`** — i.e. not observable.

- **optional → make observable (48 silent-pass):** convert `except Exception: pass` to
  `except Exception as e: logger.debug("<context>: %s", e)` (or `warning` for the few that
  indicate degraded user-facing behaviour). This is mechanical but touches many files, so it is
  scheduled as **per-module batches** (see Follow-up), not a single sweep.
- **contract-critical (raise):** the genuine contract guards already live in `shared_state_manager`
  write/append/approve paths and `access_control`, which **return typed `WriteResult` reasons or
  raise** today — they are not silent. Net new typed-raise sites required: **few**; flagged
  per-module during the batches.
- **accidental / over-broad:** none found that *mask a contract violation*. A handful catch around
  code that effectively cannot raise (pure dict/string ops); these are candidates to narrow but are
  harmless. Listed for the batches.

## Follow-up (per-module batches, under this policy)

Recommended order (smallest blast radius first), each gated on a green suite:

1. `utils/registry_seed.py`, `audit_logger.py`, `event_recorder.py`, `checkpoint_writer.py`,
   `log_helpers.py` — pure telemetry → `logger.debug`.
2. `handoff_engine.py`, `metrics_engine.py`, `training_engine.py`, `skill_bridge.py` — telemetry.
3. `prompt_assembler.py`, `db.py`, `capability_registry.py` — fallbacks → debug + a typed raise
   where a missing required input is a contract error.
4. `shared_state_manager.py` — separate state-save guards (raise typed `StateError`) from audit
   logging (debug).
5. `orchestration_loop.py` (29) and `cli.py` (30) — largest; loop-resilience stays optional but
   logged; CLI display fallbacks stay optional but logged.

**Risk note:** converting silent swallows to observable logging can surface previously-hidden
failures. Each batch runs the full suite before/after; optional catches become **logged warnings,
not raises**, so runtime flow is unchanged — only visibility improves.

## Appendix — the 48 silent-`pass` sites

cli.py: 32, 40, 666, 953, 1054, 1604
db.py: 175
agent_runner.py: 35, 194
audit_logger.py: 27
handoff_engine.py: 243, 293, 308, 327, 334, 419, 432, 453, 483
metrics_engine.py: 1245
orchestration_loop.py: 405, 451, 708, 719, 735, 796, 847, 859, 889, 897, 967, 992, 1200, 1279, 1345
prompt_assembler.py: 217, 519
shared_state_manager.py: 210, 213, 288, 396, 563, 610
skill_bridge.py: 341, 352
spawn_policy.py: 444
training_engine.py: 57
log_helpers.py: 221
