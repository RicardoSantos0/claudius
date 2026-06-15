# Architecture

How the parts of this repository fit together. This is a map, not a tutorial —
for run instructions see [operation-modes.md](operation-modes.md).

## Two layers

The repo is two cooperating layers that share one tree:

1. **Claude Code config** — `agents/`, `skills/`, `commands/`. These are markdown
   definitions symlinked into `~/.claude/` by the setup scripts, making them
   globally available to Claude Code.
2. **MAS engine** — `mas/`. A governed Python system that coordinates 16 agents
   through formal handoffs, access-controlled shared state, and policy enforcement.

The two layers reference the same agents: a file under `agents/` is both a Claude
Code agent definition *and* a MAS roster member (registered in `mas/roster/`).

## The four planes

A useful way to read the system is as four planes — it makes clear *where* each
responsibility lives:

| Plane | What it does | Where it lives |
|-------|--------------|----------------|
| **Control** | Orchestration, phase management, delegation, governance decisions | `master_orchestrator` + the policies in `mas/policies/` |
| **Execution** | The actual work — specialized/delivery agents acting on tasks | the rest of `agents/` |
| **State** | Durable, inspectable project memory and the event/audit trail | `shared_state.yaml` per project + `mas/data/episodic.db` |
| **Capability** | The tools and the "who can do what" — skills and the registry | `skills/` + `mas/roster/` |

Two properties fall out of this split and are the system's main selling points:
**every action is recorded** (State plane → full audit trail), and **phases only
advance when their exit artifact exists** (Control plane → governance gates). The
evaluation phase scores the project as a whole, not agents in isolation.

## Top-level map

```
claude-config/
├── agents/        16 agent definitions (+ _utilities.md)   → ~/.claude/agents/
├── skills/        11 skill packages (SKILL.md each)        → ~/.claude/skills/
├── commands/      slash commands (resume-mas.md)           → ~/.claude/commands/
├── standards/     engineering standards (frontmatter, wire protocol, …)
├── scripts/       validators + source-export tooling
└── mas/           Multi-Agent System engine
    ├── core/          Python engine (cli.py, db.py, wire_protocol.py, config.py)
    │   └── engine/    engine subpackage (shared_state, handoff, consultation, …)
    ├── roster/        agent registry + version history + training backlog
    ├── policies/      governance YAML (governance, spawn, trust tiers, eval, …)
    ├── templates/     handoff / spawn / eval-report YAML templates
    ├── foundation/    schema + folder-structure + memory-type specs
    ├── domains/       domain context injected into domain_expert
    ├── tools/         registry→DB sync + maintenance scripts (roster_sync.py, capability_sync.py)
    ├── data/          episodic.db (SQLite, gitignored runtime state)
    └── projects/      per-project workspaces (gitignored runtime state)
```

## Component interactions

```mermaid
flowchart TD
    CC[Claude Code] -->|invokes| MO[master_orchestrator]
    MO -->|delegates via handoff| AG[other agents]
    MO --> CLI[mas CLI]

    CLI --> CORE[mas/core/cli.py]
    CORE --> ENG[mas/core/engine/*]

    ENG --> SS[shared_state.yaml<br/>per project]
    ENG --> DB[(episodic.db)]
    ENG --> POL[mas/policies/*.yaml]
    ENG --> TPL[mas/templates/*.yaml]

    AGENTS[agents/*.md] -. registered in .-> ROSTER[mas/roster/]
    ROSTER -. synced by roster_sync.py .-> DB
    FOUND[mas/foundation/] -. schemas/folder spec .-> ENG
    DOM[mas/domains/*.md] -. auto-injected .-> ENG
```

- **`mas/core/`** holds always-importable modules (`cli.py`, `db.py`,
  `wire_protocol.py`, `config.py`). `cli.py` is the `mas` entry point.
- **`mas/core/engine/`** holds the orchestration modules: `shared_state_manager.py`
  (state + access control), `handoff_engine.py` (delegation records),
  `consultation_engine.py`, `capability_registry.py`, `task_board.py`,
  `metrics_engine.py`, `spawn_policy.py`, `prompt_assembler.py`, and others.
- **`mas/roster/`** is the design-time source of truth for agents.
  `registry_canonical.yaml` is the normalized, schema-conformant registry that
  `scripts/validate_agents.py` checks; `registry_index.yaml` carries the MAS-engine
  view (agent capabilities + the active skills registry).
- **`mas/policies/`** encodes the rules the engine enforces (governance, handoff
  protocol, trust tiers, spawn limits, evaluation weights, training authority,
  plus lifecycle/artifact/consultation/skill triggers).
- **`mas/templates/`** supplies the structured shapes for handoffs, spawn requests,
  and evaluation reports.
- **`mas/foundation/`** defines the shared-state schema, memory types, and the
  per-project `folder_structure.yaml` Scribe creates at init.
- **`mas/projects/`** is gitignored runtime state — one subdirectory per project.

## Project lifecycle

`mas init` defaults to **lite mode** (3 phases). Pass `--mode=standard` for the full
governed pipeline.

**Lite mode (default, 3 phases):**

```
intake → execution → closed
```

Lite skips specification, planning, capability discovery, consultation, and review.
Spawning is blocked in lite projects. `mas status` shows `[lite]` next to the phase.

**Standard mode (`mas init --mode=standard <slug>`, 9 phases):**

```
intake → specification → planning → capability_discovery → execution
       → review → evaluation → improvement → closed
```

Each standard phase transition requires: exit criteria verified by Master, a shared
state snapshot, and the phase recorded in `workflow.completed_phases`.

Project IDs follow `proj-{YYYYMMDD}-{NNN}-{slug}`, and the project folder name
matches the ID.

## Memory sources

| Source | Role | Lifetime |
|--------|------|----------|
| `mas/projects/<id>/shared_state.yaml` | Compact current-state projection used for orchestration | Per project (archived at phase end) |
| `mas/data/episodic.db` | Durable, queryable SQLite event store (handoff/runtime events, FTS5 index, registry index tables) | Durable, local fallback |
| `mas/roster/registry_index.yaml` | Design-time source of truth for agents, capabilities, and the skills registry | Durable |

The episodic DB also holds registry index tables (`mas_agents`, `mas_skills`, etc.)
that index architecture artifacts. Runtime lookups query the DB registry first, then
fall back to the filesystem. After editing `agents/*.md` or
`mas/roster/registry_canonical.yaml`, run `roster_sync.py` to refresh the DB index
(see [authoring-agents.md](authoring-agents.md)).
