# Operation Modes

This repo is **source-tree-only**. There is no pip-installable distributable —
you run it from a checkout, either via an activated virtualenv or via `uv run`
from the repo root. Do **not** try to `pip install mas`; `pyproject.toml` builds a
wheel for the `core` package only as a build target, not a published artifact.

There are two ways the repo is used. They are independent and can both be active.

## Mode 1 — Claude Code config mode

The `agents/`, `commands/`, and `skills/` directories become globally available to
Claude Code through symlinks created by the setup scripts:

```powershell
# Windows (PowerShell as Administrator)
.\setup.ps1
```

```bash
# macOS / Linux
./setup.sh
```

| Local path | Symlink target |
|------------|----------------|
| `agents/`   | `~/.claude/agents/`   |
| `commands/` | `~/.claude/commands/` |
| `skills/`   | `~/.claude/skills/`   |

After setup, Claude Code picks up the agents, slash commands, and skills directly.
No Python or venv is required for this mode. Run setup once per machine; pull from
GitHub to sync changes across machines.

## Mode 2 — source-tree MAS mode

The MAS engine runs from the repo root (the directory containing `pyproject.toml`).
Activate the venv once per session, then use the bare `mas` command:

```powershell
# Windows — activate the repo venv (run once per shell)
.\.venv\Scripts\activate
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Then:

```bash
mas init quick-fix                  # lite project (3 phases) — DEFAULT
mas init --mode=standard big-effort # standard project (9 phases, full governance)
mas doctor                          # runtime/env diagnostics
mas status   <project-id>           # current phase [lite], owner, pending handoffs
mas roster                          # list all registered agents
```

`uv run mas ...` also works from the repo root but rebuilds the wheel each time, so
it is slower. Prefer the activated venv.

### Verified top-level commands

These are the real `mas` subcommands (from `mas/core/cli.py`):

```
init            close           consultation-status
doctor          rebuild-state   reopen
resume          roster          run
status          events          prompt
state           tokens          check-artifacts
pending         rollup          check-config
snapshot        sync            skill-usage
                explain
```

Grouped maintenance commands:

```
mas registry seed            # re-seed registry index tables from filesystem
mas registry list            # list registry contents (--table mas_agents)
mas db rebuild-fts           # rebuild FTS5 index from agent_events
mas db migrate-postgres      # copy local SQLite tables into configured PostgreSQL
mas db migrate-graph         # one-time legacy graph import
```

### Two MAS execution sub-modes

Within Mode 2 there are two ways to drive a project:

| Sub-mode | How | When |
|----------|-----|------|
| **Claude Code manual orchestration** (primary) | `mas prompt <project-id> [agent]` assembles the next agent prompt; run the agent manually in Claude Code | No API credits needed |
| **`mas run` CLI** | `mas run <project-id>` drives the live loop autonomously | Requires `ANTHROPIC_API_KEY` with credits |

For the manual flow, get the assembled prompt for the next agent:

```bash
mas prompt <project-id>                # next agent auto-detected
mas prompt <project-id> inquirer_agent # specific agent
```

## Testing (Mode 2)

```bash
pytest mas/tests/                 # full suite
pytest mas/tests/unit/            # unit tests only
pytest mas/tests/integration/     # integration tests only
pytest mas/tests/ --cov=mas/core  # with coverage
```

The coverage gate is **70%** (`--cov-fail-under=70` in `pyproject.toml`), staged
toward a future 80% target.
