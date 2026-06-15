# Claude Config — Global Agent & Skill Repository

This repository is the global Claude Code configuration synced across all machines.
It provides custom agents, skills, slash commands, and the Multi-Agent System (MAS).

## Structure

```
agents/          Custom Claude Code agents   → symlinked to ~/.claude/agents/
commands/        Custom slash commands       → symlinked to ~/.claude/commands/
skills/          Skill packages              → symlinked to ~/.claude/skills/
mas/             Multi-Agent System engine   → see mas/CLAUDE.md
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
| **Claude Code manual orchestration** | Use `uv run mas prompt <project-id> [agent]` plus Claude Code agents / manual wire application | Primary no-API workflow |
| **`mas run` CLI** | `uv run mas run <project-id>` drives the live loop autonomously | Requires `ANTHROPIC_API_KEY` with credits |

Claude Code is the primary workflow for this environment. The Python engine handles state, handoffs, and governance; Claude Code is the manual agent invoker.

To get the assembled prompt for any agent (useful in Claude Code mode):
```bash
uv run mas prompt <project-id>                # next agent auto-detected
uv run mas prompt <project-id> inquirer_agent # specific agent
```

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
defaults to parallel for such entries; in manual Claude Code mode, spawn multiple agents
in one turn via the Agent tool.

**Safety invariants (non-negotiable — learned the hard way in proj-YYYYMMDD-NNN, where
parallel agents ran `git stash` and scrambled the working tree):**

- **Parallel sub-agents must work on a disjoint file set and run NO git commands** (never `stash`/`checkout`/`reset`/`commit`). They verify via targeted tests or imports only; the parent runs the integrated green gate and the single commit. The shared working tree + git index has no isolation — concurrent git state-mutation is a race.
- **Do not parallelize a task that inherently touches git or needs whole-tree test runs.** Do it sequentially yourself. (This is the one case where the parallel default does NOT apply.)
- **Scheduled/background agents (e.g. `session_scheduler`) must defer on a dirty tree they did not create.** The hazard is not only parallel sub-agents — a cron/RemoteTrigger run sharing the working tree can revert an interactive session's uncommitted work. Such agents run a `git status --porcelain` preflight and **abort** if non-gitignored changes exist; they never run `stash`/`reset`/`checkout`/`restore`/`clean`/`commit`. (Learned in proj-YYYYMMDD-NNN: a concurrent process reverted ~60 uncommitted files to HEAD mid-session.)
- **Broad mechanical refactors** (e.g. converting many call sites): audit/classify first as a doc artifact, then change **one module at a time**, with a **green gate + commit per module** — durable and recoverable at every step, unlike a single big sweep.
