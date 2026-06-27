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

## 2. Full test suite + coverage gate

```bash
pytest mas/tests/
```

The coverage gate is **70%** (`--cov-fail-under=70` in `pyproject.toml`), staged
toward a future 80% target. The run fails if coverage drops below 70%. CI runs the
suite with `-x -q` (stop on first failure).

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

Confirms the runtime environment (DB, templates, API key presence, etc.) is healthy.

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
command names, agent count (16), skill count (11), the coverage gate (70%), and
the current MAS discipline behavior.

## Pre-release summary

| Check | Command | Pass condition |
|-------|---------|----------------|
| Agents | `python scripts/validate_agents.py` | exit 0 |
| Skills | `python scripts/validate_skills.py` | exit 0 |
| Tests + coverage | `pytest mas/tests/` | green, coverage ≥ 70% |
| Archive | `python scripts/check_archive_clean.py <archive>` | exit 0 |
| MAS discipline | `python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG` | local project evidence present, or authorized bypass |
| Diagnostics | `mas doctor` | healthy |
| Setup | `setup.ps1` / `setup.sh` | symlinks resolve |
| Docs | manual review | current |
