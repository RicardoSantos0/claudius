# Claudius — Provider-Neutral Agent & Skill Repository

This repository provides shared agents, skills, commands, and the provider-neutral
Multi-Agent System (MAS) to Claude Code, Codex, OpenCode, GitHub Copilot, and other
compatible clients.

## Canonical Cross-Provider Memory

- `AGENTS.md` is the sole content-bearing repository instruction file. Codex,
  OpenCode, and compatible GitHub Copilot agent surfaces read it directly.
- `CLAUDE.md` is only a Claude Code import shim for `AGENTS.md`.
- `.github/copilot-instructions.md` is only a GitHub Copilot bootstrap shim that
  points to `AGENTS.md`; do not duplicate instructions there.
- `mas/AGENTS.md` is the sole content-bearing MAS-specific instruction file.
  `mas/CLAUDE.md` is only its Claude Code import shim.
- Provider-local auto-memory is not MAS memory. Never store the only copy of MAS
  decisions, project state, registry data, or operational history in a client's
  private memory.
- `mas close` is the synchronization boundary: it preserves or creates
  `PROJECT_SUMMARY.md` and embeds the same summary in the `project_closed` SQL
  event so every surface retrieves one shared project memory.
- Before closing, route any durable provider-local learning into governed shared
  state or a curated `PROJECT_SUMMARY.md`; MAS intentionally does not scrape
  private client memory stores.
- Provider-local memory may remain enabled as an optional recall cache. Durable
  MAS knowledge belongs only in the governed project workspace and configured
  MAS SQL store.

## Structure

```
agents/          Custom Claude Code agents   → symlinked to ~/.claude/agents/
commands/        Custom slash commands       → symlinked to ~/.claude/commands/
skills/          Skill packages              → symlinked to ~/.claude/skills/
mas/             Multi-Agent System engine   → see mas/AGENTS.md
pyproject.toml   Python package config (MAS)
setup.ps1        One-time setup — Windows (run as Administrator)
setup.sh         One-time setup — macOS / Linux
```

## First-Time Setup (per machine)

```powershell
# Windows (PowerShell as Administrator)
.\setup.ps1

# macOS / Linux
./setup.sh
```

This creates symlinks so agents, commands, and skills are globally available in Claude Code.

## Running the MAS

All `uv run` commands must be executed from this repo root (where `pyproject.toml` lives).

```bash
uv run mas init    <slug-or-id>      # Start a new project (e.g. 'session-scheduler')
uv run mas status  <project-id>     # Show project status and phase
uv run mas state   <project-id>     # Dump full shared state
uv run mas pending <project-id>     # List unresolved handoffs
uv run mas roster                   # Show all registered agents
uv run pytest mas/tests/            # Run the full test suite
```

### Two execution modes

| Mode | How | When |
|------|-----|------|
| **Manual surface orchestration** | Use `uv run mas prompt <project-id> [agent]` with Claude Code, Codex, OpenCode, Copilot, or another compatible client, then apply the response through `mas ingest` | Primary no-API workflow |
| **`mas run` CLI** | `uv run mas run <project-id>` drives the live loop autonomously | Requires the selected catalog's credential and adapter extra |

The Python engine handles state, handoffs, governance, and shared memory; the
selected provider surface supplies model execution and returns the response.

To get the assembled prompt for any manual surface:
```bash
uv run mas prompt <project-id>                # next agent auto-detected
uv run mas prompt <project-id> inquirer_agent # specific agent
uv run mas prompt <project-id> product_manager_agent --surface claude --json
uv run mas prompt <project-id> --surface codex --json
```

`mas prompt` records a non-billable preview estimate, not an observed model call.
`mas ingest` records the observed manual response with heuristic counting; use
`mas log-tokens` for exact provider/cache fields. Prompt envelopes expose a
provider-neutral stable-prefix fingerprint for adapter-level caching. See
`docs/architecture/prompt-token-contract.md`.

Apply the envelope model when invoking every manual agent, then return its
`dispatch_id`, actual provider, and actual model through `mas ingest`. Planning
roles use Fable first on Claude and Opus 4.8 only when Fable is
excluded/unavailable or refuses. A client/operator receipt is an attestation;
selection alone is not execution proof. See
`docs/architecture/model-routing.md`.

## Agent Network

The MAS has 16 agents across 5 trust tiers (including delivery and infrastructure agents):

| Tier | Agents |
|------|--------|
| T0 Core | `master_orchestrator`, `scribe_agent` |
| T1 Established | `hr_agent`, `inquirer_agent`, `product_manager_agent`, `project_manager_agent`, `evaluator_agent`, `trainer_agent` |
| T1 Consultants | `risk_advisor`, `quality_advisor`, `devils_advocate`, `domain_expert`, `efficiency_advisor` |
| T1 Delivery | `canonical_engineer`, `analysis_engineer`, `integration_engineer`, `reliability_engineer` |
| T2 Supervised | `spawner_agent`, `librarian_agent` |
| T3 Provisional | `nlp_taxonomy_specialist` |
| Infrastructure | `session_scheduler` |

Invoke `master_orchestrator` to start a project. It coordinates all other agents.

## Adding New Agents or Skills

- New agent: `agents/{name}.md` with frontmatter `name`, `description`, `tools`
- New skill: `skills/{name}/SKILL.md`
- New command: `commands/{name}.md`
- Push to GitHub — other machines pull to sync

**After editing `mas/roster/registry_canonical.yaml` (or any `agents/*.md` registry entry)** run `roster_sync.py` so the runtime DB index (`mas/data/episodic.db` → `mas_agents` table) reflects the change. Otherwise capability discovery and prompt assembly may resolve a stale agent set.

```bash
uv run python mas/tools/roster_sync.py            # apply
uv run python mas/tools/roster_sync.py --dry-run  # preview
uv run python mas/core/engine/capability_registry.py sync-db-from-yaml
```

`mas registry seed` (auto-run on project close) also refreshes registry tables, but `roster_sync.py` is the targeted, fast path when only agents have changed. Reference: ip-002 / proj-YYYYMMDD-NNN.

## Key Policies (enforced by the MAS engine)

- Every delegation goes through a formal handoff (`handoff_engine.py`)
- Shared state has access control — agents can only write fields they own
- Spawning new agents requires: gap certificate + master approval + consultant review
- Max 3 spawns per project, 1 per phase, no recursive spawning
- All training proposals are advisory — nothing changes without Master approval

## MAS Workflow Enforcement

**Master Orchestrator is strictly prohibited from bypassing the MAS structure for any project ordered to MAS.**

- The Master Orchestrator must always follow the MAS workflow and protocols for all project phases and delegations.
- It is not authorized to delegate work outside the MAS, including direct delegation to Claude Code or any agent/process not governed by the MAS system.
- Any attempt to override or circumvent the MAS workflow is a governance violation and must be escalated for review.

### Active Project Check (enforced on every implementation request)

Before writing code, modifying files, running calibration/grading tools, or spawning agents, check for an active MAS project:

```bash
uv run mas status <project-id>   # or scan mas/projects/ for status=active
```

If a project with `status: active` governs the request:

1. **State the active project** — name it explicitly before proceeding.
2. **Route through MAS** — get the next agent's prompt and delegate:
   ```bash
   uv run mas prompt <project-id>   # auto-selects next agent
   ```
3. **Authorized bypass** — if the user explicitly says "bypass MAS" or "implement directly", record it in the project decision log *before* acting:
   ```bash
   # Log the user-authorized bypass first, then implement
   ```
   Authorized bypasses are recorded as `source: user_authorized_bypass` and are graded as WARNING (not FAIL) by the evaluator.

**Exception:** Read-only operations (file reads, grep, status checks) do not require routing.

## Four Engineering Principles

These principles apply to all code changes made in this repository and any project governed by the MAS.

### 1. Think Before Coding
State assumptions explicitly. Present multiple interpretations when ambiguity exists. Push back when a simpler approach exists. Stop and ask when confused — do not silently pick an interpretation and run with it.

### 2. Simplicity First
Minimum code that solves the problem. No features beyond what was asked. No abstractions for single-use code. No "flexibility" that wasn't requested. No error handling for impossible scenarios. If 200 lines could be 50, rewrite it. Test: would a senior engineer say this is overcomplicated? If yes, simplify.

### 3. Surgical Changes
Touch only what you must. Don't improve adjacent code, comments, or formatting. Don't refactor things that aren't broken. Match existing style. When your changes create orphaned imports/variables/functions, remove them. Don't remove pre-existing dead code unless asked. Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
Define success criteria before starting. Transform imperative tasks into verifiable goals. For multi-step tasks, state a brief plan with explicit verify steps: `[Step] → verify: [check]`. Loop until verified — weak criteria require constant clarification; strong criteria let you work independently.

## Delegation & Parallelism

**Parallel sub-agents are the DEFAULT for independent work.** When sub-tasks are
independent — no data dependency (one doesn't consume another's output) AND a disjoint
file set (no shared files) — dispatch them in parallel rather than sequentially. This is
the expected case for read-only/analysis fan-out (audits, recon, multi-file search) and
for delivery on separate files. The MAS orchestrator (`master_orchestrator` + `hr_agent`)
defaults to parallel for such entries; on manual surfaces with a governed subagent
tool, dispatch the independent entries in one turn.

**Safety invariants (non-negotiable — learned the hard way in proj-YYYYMMDD-NNN, where
parallel agents ran `git stash` and scrambled the working tree):**

- **Parallel sub-agents must work on a disjoint file set and run NO git commands** (never `stash`/`checkout`/`reset`/`commit`). They verify via targeted tests or imports only; the parent runs the integrated green gate and the single commit. The shared working tree + git index has no isolation — concurrent git state-mutation is a race.
- **Do not parallelize a task that inherently touches git or needs whole-tree test runs.** Do it sequentially yourself. (This is the one case where the parallel default does NOT apply.)
- **Scheduled/background agents (e.g. `session_scheduler`) must defer on a dirty tree they did not create.** The hazard is not only parallel sub-agents — a cron/RemoteTrigger run sharing the working tree can revert an interactive session's uncommitted work. Such agents run a `git status --porcelain` preflight and **abort** if non-gitignored changes exist; they never run `stash`/`reset`/`checkout`/`restore`/`clean`/`commit`. (Learned in proj-YYYYMMDD-NNN: a concurrent process reverted ~60 uncommitted files to HEAD mid-session.)
- **Broad mechanical refactors** (e.g. converting many call sites): audit/classify first as a doc artifact, then change **one module at a time**, with a **green gate + commit per module** — durable and recoverable at every step, unlike a single big sweep.
