# MAS — Multi-Agent Delivery System

A governed, phase-driven multi-agent system for software delivery. Agents hand off work
through a formal protocol; every decision, artifact, and phase transition is recorded.

---

## Current State (as of 2026-04-25)

| Metric | Value |
|--------|-------|
| Active agents | 20 |
| Trust tiers | T0 Core (2), T1 Established (15), T2 Supervised (2), Infrastructure (1) |
| Projects run | 34 (project folders) |
| Projects in DB | 25 |
| Agent events logged | 1,302 |
| Knowledge graph nodes | 728 |
| Knowledge graph edges | 2,379 |
| Shared state snapshots | 75 |
| Training proposals (all time) | 16 (TP-001 – TP-016) |
| Proposals applied | TP-001–TP-011, TP-012–TP-016 |

### Agent Roster

| Agent | Tier | Performance Score | Role |
|-------|------|------------------|------|
| master_orchestrator | T0_core | — | Workflow coordination, governance, delegation |
| scribe_agent | T0_core | — | Documentation, decision logging, artifact tracking |
| hr_agent | T1_established | — | Capability discovery, roster management, gap certs |
| inquirer_agent | T1_established | — | Intake, requirements elicitation |
| product_manager_agent | T1_established | — | Product plan, MoSCoW requirements, acceptance criteria |
| project_manager_agent | T1_established | — | Execution planning, task decomposition |
| evaluator_agent | T1_established | — | Performance evaluation, metric scoring |
| trainer_agent | T1_established | — | Improvement proposals, backlog management |
| canonical_engineer | T1_established | 0.94 | Pydantic v2 models, schema hardening |
| analysis_engineer | T1_established | 0.97 | DataFrame flattening, CLI reports |
| integration_engineer | T1_established | 0.95 | API connectors, dry-run diffing |
| reliability_engineer | T1_established | 0.93 | Test suite, coverage gates |
| risk_advisor | T1_established | — | Risk analysis, failure modes (advisory) |
| quality_advisor | T1_established | — | Completeness, testability (advisory) |
| devils_advocate | T1_established | — | Assumption challenging (advisory) |
| domain_expert | T1_established | — | Domain knowledge, best practices (advisory) |
| efficiency_advisor | T1_established | — | Simplicity, resource efficiency (advisory) |
| spawner_agent | T2_supervised | — | New agent design (draft-only, needs approval) |
| librarian_agent | T2_supervised | — | DB maintenance: FTS rebuild, vacuum, graph migration |
| session_scheduler | Infrastructure | — | Cron-based session resume |


---

## Architecture

### Lifecycle

```
intake → specification → planning → capability_discovery → execution → review → evaluation → improvement → closed
```

Every phase transition requires a Scribe handoff to be accepted before `current_phase` advances (blocking gate, TP-011).

### Formal Handoff Protocol

All agent-to-agent delegation goes through `handoff_engine.py`. A handoff record contains:
- `from_agent` / `to_agent` / `phase`
- `payload`: summary, artifacts_produced, decisions_made, open_questions, constraints_for_next
- `acceptance`: pending → accepted / rejected

Handoffs are stored in `shared_state.workflow.handoff_history` and mirrored to the SQLite event log.

### Access Control

Each agent owns specific shared state fields. The `shared_state_manager.py` enforces field ownership — agents cannot write fields they don't own. Master Orchestrator owns `core_identity.*`.

### Memory Model

`mas/data/episodic.db` is MAS durable historical memory. It stores typed runtime events and queryable history of what happened across runs.

`mas/projects/<project_id>/shared_state.yaml` is the compact operational projection for current orchestration, not the long-term event ledger.

`mas/roster/registry_index.yaml` remains the design-time source of truth for capabilities and agent metadata.

Human-facing artifacts such as `CHECKPOINT.md` and `CLOSED.md` are generated summaries for operators and review, not authoritative event stores.

---

## Database Schema

SQLite file: `mas/data/episodic.db`

### `agent_events`
Event log for all handoff lifecycle events.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment row ID |
| project_id | TEXT | Project this event belongs to |
| agent_id | TEXT | Agent that triggered the event |
| action_type | TEXT | `handoff_created`, `handoff_accepted`, `handoff_rejected`, `agent_call` |
| timestamp | TEXT | ISO-8601 UTC |
| intent | TEXT | Human-readable task description |
| result_shape | TEXT | Shape of the outcome (e.g., `handoff`) |
| payload | TEXT | JSON blob with event-specific data |

Full-text search on `intent` and `payload` via the `agent_events_fts` FTS5 virtual table.

### `shared_states`
One row per project — stores the full project shared state as a YAML blob.

| Column | Type | Description |
|--------|------|-------------|
| project_id | TEXT PK | Project identifier |
| state | TEXT | Full YAML-serialised shared state |
| updated_at | TEXT | ISO-8601 UTC of last write |

### `agent_graph`
Nodes in the knowledge graph (project phases, agents, artifacts, decisions, findings, proposals).

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Node identifier (e.g., `proj-001:intake`, `agent:scribe_agent`) |
| type | TEXT | `project`, `phase`, `agent`, `artifact`, `decision`, `finding`, `proposal`, `handoff`, `related_to` |
| label | TEXT | Human-readable label |
| meta | TEXT | JSON blob with node attributes (includes `project_id` for filtering) |

### `agent_graph_edges`
Directed edges between graph nodes.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | Edge identifier |
| source | TEXT | Source node id |
| target | TEXT | Target node id |
| relation | TEXT | `handoff_to`, `produced`, `decided`, `completed`, `raised`, `related_to` |
| meta | TEXT | JSON blob with edge attributes |

---

## Cross-Project Memory

Agents **do** query the DB for memory of previous projects via `graph_memory.py`.

Every `write_episode()` call mirrors facts to a special `__global__` graph overlay stored in SQLite. When any agent calls `query(agent_id, context)`:

1. Local facts from the current project's graph are retrieved.
2. Global facts from the `__global__` overlay are retrieved and tagged `scope: global`.
3. Both sets are returned together as a prompt injection (≤300 tokens by default).

This means agents can recall decisions, findings, and proposals from past projects without being given the project ID explicitly. The `agent_events` FTS5 index also supports keyword search across all 25 recorded projects.

---

## Key Files

| Path | Purpose |
|------|---------|
| `mas/core/engine/handoff_engine.py` | Formal handoff create/accept/reject |
| `mas/core/engine/shared_state_manager.py` | Field-access-controlled shared state |
| `mas/core/engine/graph_memory.py` | Knowledge graph + cross-project memory |
| `mas/core/engine/capability_registry.py` | HR agent capability search and scoring |
| `mas/core/db.py` | SQLite event append and query layer |
| `mas/roster/registry_index.yaml` | Agent roster + capability vocabulary |
| `mas/roster/training_backlog.yaml` | Trainer proposals (TP-001 – TP-016) |
| `agents/_utilities.md` | Shared CLI reference for all agents |
| `agents/master_orchestrator.md` | Master workflow, governance rules |
| `agents/scribe_agent.md` | Scribe protocol, blocking gate rules |

---

## Governance Policies

- Scribe handoff is a **blocking gate** — `current_phase` cannot advance until Scribe confirms.
- Master must **verify artifacts on disk** (Glob/Read) before accepting any delivery handoff.
- File creation uses the **Write tool with Windows absolute paths** — never bash heredocs.
- Spawning new agents requires: gap certificate + Master approval + consultant review.
- Max 3 spawns per project, 1 per phase, no recursive spawning.
- All training proposals are advisory (L0) — nothing changes without Master approval.
