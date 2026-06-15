# Multi-Agent System (MAS)

A governed agent network for project delivery. Coordinates 16 specialized agents through
a formal handoff protocol, shared state with access control, and a full evaluation +
improvement loop.

All commands must be run from the **repo root** (the directory containing `pyproject.toml`).

## Memory Sources

- `mas/data/episodic.db` is the durable, queryable event memory for MAS runtime history.
- `mas/projects/<project_id>/shared_state.yaml` is the compact current-state projection used for orchestration.
- `mas/roster/registry_index.yaml` is the design-time source of truth for agents, capabilities, and skill registry data.
- Human docs (`CHECKPOINT.md`, `CLOSED.md`, summaries) are generated reports, not canonical event storage.

## DB Registry Index (proj-YYYYMMDD-NNN-db-registry)

`mas/data/episodic.db` now contains 7 registry tables that index MAS architecture artifacts:

| Table | Indexed artifact |
|-------|------------------|
| `mas_agents`    | `agents/*.md` |
| `mas_skills`    | `skills/*/SKILL.md` |
| `mas_commands`  | `commands/*.md` |
| `mas_templates` | `mas/templates/*.yaml` |
| `mas_domains`   | `mas/domains/*.md` |
| `mas_codebase`  | source modules under `mas/core/` |
| `mas_policies`  | `mas/policies/*.yaml` (11 governance policy files) |

**Runtime query order**: DB registry first → filesystem fallback. The query-first pattern is implemented in:
- `prompt_assembler.py` (agent template lookup)
- `skill_bridge.py` (skill resolution)
- `capability_registry.py` (agent + skill discovery)

**CLI**:
```bash
mas registry seed              # idempotent re-seed of all tables from filesystem
mas registry list              # list all registry contents
mas registry list --table mas_agents
```

Auto-sync: registry tables are refreshed automatically on project close. The DB stores the index (paths + metadata) only — file content remains on the filesystem.

**Recommended: activate the venv once per session, then use bare commands.**

```powershell
# Windows — activate venv (run once per shell session, from the repo root):
.venv\Scripts\activate

# After activation, all bare commands work:
mas init session-scheduler
mas init --mode=lite quick-task
mas status proj-YYYYMMDD-NNN-true-mas-integration
pytest mas/tests/
```

`uv run` also works but rebuilds the wheel each time, which is slower and fails
if Windows App Store Python is active. Prefer the activated venv.

---

## Execution Modes

### Mode 1 — Claude Code (Claude Pro, no API credits needed) Primary

Invoke `master_orchestrator` from Claude Code. Use `mas prompt` to assemble the
next agent prompt, then run the agent manually in Claude Code — no
`ANTHROPIC_API_KEY` required.

The Python engine handles all state, handoffs, and governance. Claude Code is the runtime.

```bash
# 1. Initialize project
mas init my-project

# 2. Invoke master_orchestrator in Claude Code and continue manually with `mas prompt` as needed

# 3. Get the assembled prompt for any agent (useful for debugging or manual spawning)
mas prompt proj-YYYYMMDD-NNN-my-project               # next agent auto-detected
mas prompt proj-YYYYMMDD-NNN-my-project inquirer_agent

# 4. Check status
mas status proj-YYYYMMDD-NNN-my-project
```

### Mode 2 — `mas run` CLI (requires ANTHROPIC_API_KEY with credits)

```powershell
# 1. Set your API key
$env:ANTHROPIC_API_KEY = "<your-api-key>"
# Or: add ANTHROPIC_API_KEY=<your-api-key> to .env at repo root

# 2. Run the loop
mas run proj-YYYYMMDD-NNN-my-project

# 3. Token usage
mas tokens proj-YYYYMMDD-NNN-my-project

# 4. Maintenance
mas db rebuild-fts
mas db migrate-postgres
```

---

## Quick Reference

```bash
# Project lifecycle
mas init    <slug-or-id>               # Initialize new project
mas init    --mode=lite <slug>         # Lite mode: 3 phases, no consultation
mas doctor                             # Runtime diagnostics
mas status  <project-id>              # Current phase [lite], owner, pending handoffs
mas resume  <project-id>              # Resume summary + next action
mas state   <project-id>              # Full shared state dump
mas pending <project-id>              # Unresolved handoffs
mas snapshot <project-id>             # Snapshot state at current phase
mas roster                            # All registered agents
mas prompt  <project-id> [agent-id]   # Assemble agent prompt (Claude Code mode)

# Tests
pytest mas/tests/                     # Full suite
pytest mas/tests/unit/                # Unit tests only
pytest mas/tests/integration/         # Integration tests only
```

Resume across sessions is local to this repository via `/resume-mas <project-id>`.

---

## Claude Code Mode — Governance Checklist

When running projects in Claude Code manual mode (no API credits), enforce these
steps to keep evaluation metrics meaningful:

**Project memory file shapes (ip-001 / proj-YYYYMMDD-NNN):**
- `decisions/decision_log.yaml` is a **flat YAML list** of decision entries (`[]` on init, never a dict). The SSM auto-flush (`_flush_decisions_to_disk`) reads and rewrites this file as a flat list. Legacy dict-shape files are tolerated on read for backward compatibility, but writes always normalize to the flat list form.

**Before advancing to execution phase — HARD GATES (missing = 0 score):**
- [ ] `project_definition.success_criteria` — at least 1 entry (from inquirer spec)
- [ ] `project_definition.acceptance_criteria` — at least 1 entry per success criterion:
  ```python
  sm.append("master_orchestrator", "project_definition", "acceptance_criteria",
            {"criterion": "...", "met": False})
  ```
- [ ] `intake/clarified_spec.yaml` on disk — written by inquirer_agent (phase gate)
- [ ] Task board populated — at least 1 milestone + 1 task per deliverable (scope_adherence gate)

**During execution — HARD GATES:**
- [ ] Log each significant decision to `decisions.decision_log` (minimum 1 per phase):
  ```python
  sm.append("master_orchestrator", "decisions", "decision_log", {
      "decision_id": "d-NNN",
      "value": "chosen approach",
      "rationale": "why",
      "alternatives_considered": ["alt1", "alt2"],
      "recorded_at": datetime.now(timezone.utc).isoformat(),
      "source": "claude_code_manual",
  })
  ```

**After delivery — mark acceptance criteria met:**
  ```python
  sm.append("master_orchestrator", "project_definition", "acceptance_criteria",
            {"criterion": "...", "met": True, "evidence": "..."})
  ```

**For `global_graph_contribution`:** After closing a project, key facts (architecture decisions, agent performance patterns, lessons learned) persist to the SQL event store (`mas/data/episodic.db`) and the SQLite `agent_graph` tables read by `prompt_assembler._graph_context()`. (The legacy `graph_memory.py` YAML writer was removed — req-005 / decision d-003.)

**Note:** `handoff_quality` scores 0 in Claude Code mode (no rejection cycle).
This is expected — the metric is structurally not applicable in manual mode.
See `evaluation_policy.yaml` → `claude_code_mode_metrics` for scoring guidance.

---

## Project Naming Convention

Project IDs follow the format: `proj-{YYYYMMDD}-{NNN}-{slug}`

- **Date**: UTC date of creation (auto-generated)
- **Sequence**: 3-digit, zero-padded, auto-incremented per day
- **Slug**: human-readable identifier (lowercase, hyphens, max 40 chars)

Examples:
- `mas init session-scheduler` → `proj-YYYYMMDD-NNN-session-scheduler`
- `mas init proj-YYYYMMDD-NNN-my-project` → accepted as-is

Folder name matches project ID: `mas/projects/proj-YYYYMMDD-NNN-session-scheduler/`

---

## Project Lifecycle

```
intake → specification → planning → capability_discovery → execution
       → review → evaluation → improvement → closed
```

Each phase transition requires:
1. Exit criteria met (verified by Master)
2. Shared state snapshot (`uv run mas snapshot <id>`)
3. Phase recorded in `workflow.completed_phases`

### Review-driven projects — re-baseline before planning (proj-008 / prop-008-004)

When a project's brief is an external review, audit, or bug report, treat each
finding as a **hypothesis, not ground truth** — reviews are point-in-time
snapshots and the live repo may have moved on. Before committing the execution
plan, verify every finding against the current `HEAD` (run the suite, the
validators, `mas doctor`, and check the specific files named). Skip or downscope
findings that are already resolved, and surface the re-baseline as a scope
decision. In proj-YYYYMMDD-NNN ~70% of a fresh review was already fixed;
verifying first avoided writing tests for failures that no longer existed.

---

## Agent Network

The diagram below shows the current live network used by orchestration, delivery,
consultation, supervised work, and session automation.

### Invoking the Network
Always start by invoking `master_orchestrator`. It reads the project brief, initializes
state, and coordinates the rest of the network.

```
User → master_orchestrator
  ├── scribe_agent          (project memory, folder init)
  ├── hr_agent              (capability discovery, gap certs)
  ├── inquirer_agent        (intake, clarification Q&A)
  ├── product_manager_agent (requirements, product plan)
  ├── project_manager_agent (milestones, tasks, execution)
  ├── evaluator_agent       (metrics, evaluation report)
  ├── trainer_agent         (improvement proposals — L0 advisory)
  ├── delivery_engineers
  │   ├── canonical_engineer   (schemas, provenance, validation)
  │   ├── analysis_engineer    (flattening, reports, QA views)
  │   ├── integration_engineer (connectors, dry-run diffs)
  │   └── reliability_engineer (tests, gates, coverage)
  ├── supervised_agents
  │   ├── spawner_agent        (draft agent packages — T2 supervised)
  │   └── librarian_agent      (FTS, vacuum, graph migration)
  ├── consultant_panel
  │   ├── risk_advisor
  │   ├── quality_advisor
  │   ├── devils_advocate
  │   ├── domain_expert
  │   └── efficiency_advisor
  └── infrastructure
      └── session_scheduler    (scheduled resume + project locks)
```

### Consultation Triggers
**All 5 consultants** (`spawn`, `scope_change`):
These are mandatory types — the full panel is always convened.

**Core-three consultants** (`governance`, `escalation`, `architecture`):
Scoped panel: `risk_advisor`, `quality_advisor`, `efficiency_advisor`.

If **all responding** consultants return `high` risk → `human_escalation_required = true` (hard stop).

---

## Core Modules

Modules in `mas/core/` (top-level, always use these paths):

| Module | CLI entry | Purpose |
|--------|-----------|---------|
| `cli.py` | `mas` / `uv run mas` | Top-level CLI entry point |
| `db.py` | — (library) | Central SQL access layer; `semantic_search()`, `query_token_usage()`, shared-state SQL helpers |
| `wire_protocol.py` | — (library) | Compact wire format for handoff payloads |
| `config.py` | — (library) | System configuration loader |

Modules in `mas/core/engine/` (engine subpackage — use full path):

| Module | CLI entry | Purpose |
|--------|-----------|---------|
| `shared_state_manager.py` | `python mas/core/engine/shared_state_manager.py` | Project state, access control, snapshots |
| `handoff_engine.py` | `python mas/core/engine/handoff_engine.py` | Handoff creation, acceptance, SQL logging |
| `intake_checker.py` | `python mas/core/engine/intake_checker.py` | Spec quality scoring (threshold ≥ 0.85) |
| `capability_registry.py` | `python mas/core/engine/capability_registry.py` | Roster, gap certificates, match scoring |
| `task_board.py` | `python mas/core/engine/task_board.py` | Milestones, tasks, dependency chains |
| `metrics_engine.py` | `python mas/core/engine/metrics_engine.py` | Project + agent scoring, eval reports |
| `spawn_policy.py` | `python mas/core/engine/spawn_policy.py` | Spawn validation; `LITE_MODE_NO_SPAWN` check |
| `training_engine.py` | `python mas/core/engine/training_engine.py` | Proposal generation, backlog management |
| `consultation_engine.py` | `python mas/core/engine/consultation_engine.py` | Consultation lifecycle, synthesis |
| `agent_runner.py` | — (library) | Anthropic SDK wrapper; gated on `ANTHROPIC_API_KEY`; logs token usage |
| `prompt_assembler.py` | — (library) | State projection + FTS5-aware prompt building |
| `access_control.py` | — (library) | Field-level write permissions matrix |
| `skill_bridge.py` | — (library) | Agent-to-skill gateway with auth matrix |
| `audit_logger.py` | — (library) | Structured YAML event logging |
| `checkpoint_writer.py` | — (library) | Human-readable project checkpoints |
| `output_linter.py` | — (library) | Wire protocol compliance scoring: `check_wire_compliance(payload)` → `(float, list[str])`; `wire_compliance_rate(payloads)` → `float` |
| `lifecycle_guard.py` | — (library) | Handoff count policy: `check_handoff_count(phase, count)` → ok/warn/flag; stale handoff expiry: `expire_stale_handoffs(pending, phase_closing)` → `(still_pending, expired)` |

> **Note:** Always use the activated venv (`mas/core/engine/`) not `uv run python` (slower; fails with Windows App Store Python).

---

## Key File Locations

```
mas/
├── core/               Python engine
│   ├── cli.py          CLI entry point
│   ├── db.py           SQL access layer (semantic_search, query_token_usage)
│   ├── wire_protocol.py
│   ├── config.py
│   └── engine/         Engine subpackage (20 modules)
├── data/
│   └── episodic.db     Local SQLite fallback store
├── agents/             → see ../agents/ at repo root (symlinked globally)
├── policies/           Governance rules (YAML)
├── templates/          Handoff, spawn, eval report templates (YAML)
├── domains/            Domain context injected into domain_expert (Markdown)
├── foundation/         Shared state schema, memory types, folder structure
├── roster/
│   ├── registry_index.yaml     Active agent registry
│   ├── version_history.yaml    All roster changes (append-only)
│   ├── training_backlog.yaml   Created at runtime by training_engine
│   └── trust_tiers/            Tier definitions
├── tests/
│   ├── unit/           Per-module unit tests
│   ├── integration/    Per-phase integration tests
│   ├── governance/     Access control and immutability tests
│   └── prompts/        Agent prompt tests
├── projects/           Runtime project data (gitignored)
├── system_config.yaml  Master configuration
└── CLAUDE.md           ← you are here
```

---

## Governance Rules (summary)

| Rule | Detail |
|------|--------|
| Handoff protocol | Every delegation uses `handoff_engine.py` — no informal routing |
| Access control | Each shared state field has an owner; writes from non-owners fail |
| Approval authority | Only `master_orchestrator` can call `sm.approve()` |
| Spawn limits | Max 3/project · max 1/phase · no recursive spawn |
| Spawn prerequisites | Gap cert (master-approved) + consultant review + worthiness check |
| Training authority | L0 — proposals only; Trainer cannot apply changes |
| Trust promotion | Requires human approval — not automated |
| Unanimous risk | 5/5 consultants at `high` → human escalation required (hard stop) |
| Delivery handoff field | Delivery agents (`canonical_engineer`, `integration_engineer`, `reliability_engineer`) MUST include `tasks_completed` in their handoff payload; `handoff_engine.py` warns if missing |

---

## Adding a New Domain

Drop a Markdown file in `mas/domains/{domain_name}.md`.
The `domain_expert` consultant will inject it automatically when `decision_type` matches.

Current domains: `software_engineering` · `data_science` · `content_creation` · `research` · `learning_analytics`

---

## Running Tests

```bash
pytest mas/tests/                      # Full suite (activate venv first)
pytest mas/tests/ -x                   # Stop on first failure
pytest mas/tests/unit/                 # Unit tests only
pytest mas/tests/ --cov=mas/core       # With coverage

# Or via uv (slower, rebuilds wheel):
uv run pytest mas/tests/
```

The legacy graph-memory subsystem (`graph_memory.py` and its tests) has been removed; the repo uses the SQL-backed path — `mas/data/episodic.db` plus the SQLite `agent_graph` tables read by `prompt_assembler._graph_context()` (req-005 / decision d-003).
