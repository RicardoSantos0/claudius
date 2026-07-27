# Release Checklist

Run these before publishing or sharing the repo. The first four checks mirror what
CI enforces on every push/PR to `main`.

Activate the venv first (see [operation-modes.md](operation-modes.md)).

## 1. Validators (CI-enforced)

```bash
python scripts/validate_agents.py    # agent frontmatter + registry coverage
python scripts/validate_skills.py    # SKILL.md + skills registry consistency
```

Both must exit `0`. If you changed agents or `registry_canonical.yaml`, run
`uv run python mas/tools/roster_sync.py` first so the runtime DB index matches.

## 2. Fresh-install smoke suite

```bash
pytest mas/tests/
```

This repo ships the fresh-install smoke suite only (`mas/tests/test_smoke.py`):
imports, `mas doctor`, and an `init → status → prompt` round-trip. There is **no
coverage gate** — `pyproject.toml` sets `addopts = ""` deliberately, because the
full internal suite (unit, integration, governance, prompts) stays in the
upstream development repo. CI runs the smoke suite with `-q` on Python 3.11,
3.12, and 3.13, and additionally runs an installed-wheel smoke job that builds
the wheel, asserts it bundles the framework assets, and exercises
`init-workspace → doctor → init` from a clean venv outside the repo.

## 3. Archive cleanliness

The source-export path must contain no private or generated files:

```bash
# Build a source-only archive (git archive, honours export-ignore)
scripts/export_source.sh             # bash
.\scripts\export_source.ps1          # PowerShell

# Verify it contains no blocked paths
python scripts/check_archive_clean.py claude-config-source.zip
```

`check_archive_clean.py` fails (non-zero exit) if the archive contains any blocked
path — `.env`, `.venv/`, `mas/data/`, `mas/projects/`, `__pycache__/`, `*.sqlite`,
notebooklm browser state, etc. CI runs the equivalent check on a `git archive` of HEAD.

## 4. MAS discipline marker

Every tracked change should be tied to a governed MAS project:

```bash
python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG
```

Local strict mode verifies the referenced `MAS: proj-...` project has handoff
history, accepted intake, token accounting, and close artifacts. CI uses marker-only
mode because `mas/projects/` is gitignored; direct pushes still need either a
`MAS:` marker or a user-authorized `MAS-BYPASS:` rationale.

## 5. Runtime diagnostics

```bash
mas doctor
```

Confirms the runtime environment, SQLite integrity, state/event reconciliation,
registry capability projection, project layout, templates, and provider
configuration are healthy.

## 6. Setup smoke check

On a fresh machine, confirm the symlink setup still works:

```powershell
.\setup.ps1     # Windows (as Administrator)
```
```bash
./setup.sh      # macOS / Linux
```

Verify `agents/`, `commands/`, and `skills/` resolve under `~/.claude/`.

## 7. Docs current

Confirm the docs under `docs/` and the top-level `README.md` still match reality:
command names, agent count (16), skill count (11), the declared version in
`pyproject.toml`, the shipped test policy (smoke suite, no coverage gate), and
the current MAS discipline behavior.

## Pre-release summary

| Check | Command | Pass condition |
|-------|---------|----------------|
| Agents | `python scripts/validate_agents.py` | exit 0 |
| Skills | `python scripts/validate_skills.py` | exit 0 |
| Smoke suite | `pytest mas/tests/` | green |
| Archive | `python scripts/check_archive_clean.py <archive>` | exit 0 |
| MAS discipline | `python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG` | local project evidence present, or authorized bypass |
| Diagnostics | `mas doctor` | healthy |
| Setup | `setup.ps1` / `setup.sh` | symlinks resolve |
| Docs | manual review | current |
