# MAS Developer Onboarding

This guide covers what a new contributor needs to set up locally to safely modify MAS agents, engine code, or governance policies.

## 1. Repository Setup

```bash
git clone <repo>
cd claude-config
# Windows
.\setup.ps1
# macOS / Linux
./setup.sh
```

`setup.*` creates the symlinks that make `agents/`, `commands/`, and `skills/` globally available to Claude Code.

## 2. Python Environment

```bash
# Recommended: use uv (the project's package manager)
uv sync
# Activate the venv for fast bare commands (run from the repo root):
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

## 3. Local Hooks (recommended)

Install once per clone so that agent-definition changes are validated before they
reach the registry and tracked work carries MAS evidence.

```bash
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

What runs:

| Hook | Trigger | What it checks |
|---|---|---|
| `validate-agents` | edit to `agents/*.md`, `mas/roster/registry_canonical.yaml`, or `scripts/validate_agents.py` | Frontmatter completeness (`name`, `description`, `tools`), registry coverage (every file listed, every entry exists), claude_name format. |
| `mas-discipline-commit-msg` | every commit message | Requires `MAS: proj-YYYYMMDD-NNN-slug` or an explicit `MAS-BYPASS: <rationale>`; local strict mode also checks the referenced project has governed handoffs, token accounting, and close artifacts. |

The hook runs `uv run python scripts/validate_agents.py` so it resolves PyYAML and other deps from the project venv. Exit code 0 = pass, 1 = validation failure, 2 = usage error.
The discipline hook is documented in [governance/behavioral-discipline.md](governance/behavioral-discipline.md).

Reference: ip-001 / proj-YYYYMMDD-NNN-mas-improvements (added 2026-05-31).

## 4. Run the Test Suite

```bash
pytest mas/tests/                 # full suite
pytest mas/tests/unit/            # unit only
pytest mas/tests/integration/     # integration only
```

The suite enforces ~70% coverage on individual runs; the full suite typically clears it.

## 5. Common Pitfalls

- **Edited an agent file?** Run `python mas/tools/roster_sync.py` (or rely on `mas registry seed` at project close) so the `mas_agents` DB index stays consistent with the filesystem.
- **Forgot the local hooks?** Run `uv run python scripts/validate_agents.py` manually before pushing, and verify your commit message with `uv run python scripts/check_mas_discipline.py --message-file <commit-message-file>`. Registry drift produces silent agent-not-found errors at runtime; missing MAS evidence makes governance bypasses hard to audit.
- **Working across Claude Code, Codex, OpenCode, Copilot, or local models?** Drive the work through `mas prompt` / `mas ingest` or the MCP `mas_prompt` / `mas_ingest` tools, then close the MAS project before committing.
- **File writes:** agents must use the `Write` tool with **absolute paths** (in your platform's native form); bash heredocs can write to the wrong location.

## 6. Where Next

- `mas/CLAUDE.md` — MAS architecture, project lifecycle, governance gates.
- `docs/db-registry.md` — DB-backed registry tables and the seed pattern.
- `docs/governance/behavioral-discipline.md` — commit discipline for Claude Code, Codex, OpenCode, Copilot, and package/manual surfaces.
- `docs/governance/` — per-policy reference docs (added incrementally).
- `agents/master_orchestrator.md` — the orchestration contract.
