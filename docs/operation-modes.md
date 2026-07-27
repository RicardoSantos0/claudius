# Operation Modes

claudius can be used three independent ways:

- **Pip install** — install the package (distribution `claudius`, CLI `mas`), then
  `mas init-workspace` to create a writable workspace (`$MAS_HOME`, default `~/.mas`).
  The wheel bundles the framework files; the workspace holds your editable copy. Not
  yet on PyPI — install from the repo or a built wheel.
- **Claude Code config mode** (Mode 1) and **source-tree MAS mode** (Mode 2), run
  from a clone and described below. Both can be active alongside a pip install.

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

## Mode 3 — MCP / provider-neutral manual surfaces

The package exposes `mas-server`, an MCP stdio server. MCP-capable clients can use
the same governed tools (`mas_prompt`, `mas_ingest`, `mas_roster`, `mas_status`,
and related `mas_*` tools) instead of inventing a separate workflow. Clients that
do not support MCP can still use the paste loop with `mas prompt` and `mas ingest`.

### Three MAS execution sub-modes

Across source-tree, package, and MCP usage there are three ways to drive a project:

| Sub-mode | How | When |
|----------|-----|------|
| **Manual orchestration** (primary) | `mas prompt <project-id> [agent]` assembles the next agent prompt; run it in Claude Code, Codex, ChatGPT, Gemini, GitHub Copilot chat, OpenCode, LM Studio, Ollama, or another LLM surface; feed replies through `mas ingest` | No provider keys needed |
| **MCP tool orchestration** | Client calls envelope-first `mas_prompt_envelope`, then `mas_ingest` and related tools through `mas-server`; `mas_prompt` is prompt-only compatibility | Claude Code, Codex, OpenCode, and other MCP-capable clients |
| **`mas run` CLI** | `mas run <project-id>` drives the live loop autonomously through `MAS_PROVIDER` | API-backed or local OpenAI-compatible providers |

For the manual flow, get the assembled prompt for the next agent:

```bash
mas prompt <project-id>                # next agent auto-detected
mas prompt <project-id> inquirer_agent # specific agent
mas prompt <project-id> product_manager_agent --surface claude --json
mas ingest <project-id> --agent product_manager_agent \
  --dispatch-id <id> --reported-provider anthropic \
  --reported-model claude-fable-5 --verification-source client < reply.txt
```

Manual mode records telemetry even though MAS is not calling the model API
directly. `mas prompt` records a non-billable preview estimate from the assembled
prompt; repeated previews do not become observed calls. `mas ingest` records an
observed response with heuristic token counting. Use `mas log-tokens` for exact
provider/surface counts or corrections, including `--cached-input`,
`--cache-write`, and `--billable-input`. Dispatch receipts can carry the same
counts plus a bounded provider request ID. Only `verification-source=provider`
contributes to verified cache-hit and billed-token-reduction metrics;
client/operator values remain labelled attestations.

Selection is not execution proof. Planning roles retain `reasoning`; Claude
selects Fable first and Opus 4.8 only for exclusion/unavailability or refusal,
while other clients resolve through their catalog. Reasoning state changes
require a matching receipt. Client/operator receipts are attestations;
autonomous provider-reported model identities are checked directly.

For behaviorally disciplined work, each surface should let MAS choose the next
agent instead of manually skipping phases: start with `mas prompt <project-id>`,
ingest each response, close the project, then commit with `MAS: <project-id>`.
Local commit hooks enforce the project evidence; CI enforces the marker or explicit
bypass record.

## Testing (Mode 2)

```bash
pytest mas/tests/                     # fresh-install smoke suite
python scripts/validate_agents.py     # agent frontmatter + registry coverage
python scripts/validate_skills.py     # SKILL.md validity + registry consistency
```

This repo ships the fresh-install smoke suite only (`mas/tests/test_smoke.py`),
which verifies imports, `mas doctor`, and an `init → status → prompt` round-trip.
There is no coverage gate (`addopts = ""` in `pyproject.toml`); the full internal
suite stays in the upstream development repo. The two validators are the same
checks CI runs, so run them alongside the smoke suite before opening a PR.
