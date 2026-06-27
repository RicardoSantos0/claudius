# MAS Developer Tooling

Quick reference for the auxiliary tools that ship with the MAS repository (under `mas/core/tools/` and `scripts/`).

## `mas/core/tools/naming_convention_check.py`

Inspects a proposed file path against its sibling-directory naming convention.

```bash
# Library
from core.tools.naming_convention_check import check_path
result = check_path("docs/providers/opencode.md")

# CLI
uv run python -m mas.core.tools.naming_convention_check docs/providers/opencode.md
# → exit 0 (ok) or 1 (mismatch) or 2 (usage)
```

Use during planning (product_manager_agent, project_manager_agent) before committing a path proposal to `product_plan.yaml`. See [conventions/naming.md](../conventions/naming.md).

Reference: prop-TP-044 / proj-YYYYMMDD-NNN.

## `scripts/validate_agents.py`

Validates that every `agents/*.md` file has complete frontmatter (`name`, `description`, `tools`) and every registry entry has a corresponding file on disk.

```bash
uv run python scripts/validate_agents.py
```

Wired as a pre-commit hook (`.pre-commit-config.yaml` → `validate-agents`). Runs automatically on changes to `agents/*.md`, `mas/roster/registry_canonical.yaml`, or the validator itself.

Reference: ip-001 / proj-YYYYMMDD-NNN.

## `scripts/check_mas_discipline.py`

Validates commit messages for MAS evidence.

```bash
uv run python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG
uv run python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG --skip-project-state
```

Local strict mode requires a `MAS: proj-YYYYMMDD-NNN-slug` marker and verifies the
local project has governed handoff history, accepted intake, token accounting, and
closed-project artifacts. `--skip-project-state` is for CI and public mirrors where
gitignored `mas/projects/` state is unavailable; it still rejects commits without a
MAS marker or explicit `MAS-BYPASS:` rationale.

Wired as a `commit-msg` hook (`.pre-commit-config.yaml` ->
`mas-discipline-commit-msg`) and as a push-time CI marker check.

Reference: proj-20260626-001-codex-behavioral-discipline-enforcement.

## `mas/tools/roster_sync.py`

Syncs `registry_canonical.yaml` into the `mas_agents` table in `mas/data/episodic.db`. Run after any edit to the canonical registry.

```bash
uv run python mas/tools/roster_sync.py            # apply
uv run python mas/tools/roster_sync.py --dry-run  # preview
```

Reference: ip-002 / proj-YYYYMMDD-NNN.
