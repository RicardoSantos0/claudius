# Security and Permissions Standard

**Type:** Normative
**Applies to:** All agents, skills, scripts, and repository operations
**Validated by:** `scripts/check_archive_clean.py`, `.gitignore`, `.gitattributes`

---

## Never Do These Things

- Do not zip the working tree directly — use `scripts/export_source.sh`
- Do not commit `.env` or `.env.*` files
- Do not commit `.claude/settings.local.json`
- Do not commit `mas/projects/`, `mas/data/`, `mas/logs/`
- Do not commit browser state (`skills/notebooklm/data/browser_state/`)
- Do not read or log secret values, even in debug mode
- Do not hard-code credentials or API keys in any file
- Do not use `rm -rf` in scripts without an explicit confirmation prompt

---

## Source-Only Export Process

```bash
# Step 1: Export tracked files only
scripts/export_source.sh
# or: .\scripts\export_source.ps1

# Step 2: Verify the archive is clean
python scripts/check_archive_clean.py claude-config-source.zip
```

The export scripts use `git archive` which only includes tracked files. The `.gitattributes` export-ignore rules provide a second layer of protection.

---

## Blocked Paths

These paths must never appear in a shared archive or be committed to the repo:

```text
.env
.env.*
.claude/settings.local.json
.venv/
__pycache__/
*.pyc
mas/data/
mas/projects/
mas/logs/
mas/working_state/
skills/notebooklm/data/browser_state/
skills/notebooklm/data/auth_info.json
*.sqlite
*.sqlite3
*.db
*.log
secrets/
logs/
```

---

## Claude Code Permission Baseline

`.claude/settings.json` must include deny rules for:

- `.env` and `.env.*` — credentials
- `secrets/` — credential store
- `skills/notebooklm/data/browser_state/` — browser auth state
- `mas/data/` — MAS runtime database
- `mas/projects/` — MAS project state (may contain sensitive project data)
- `.git/` — git internals

And ask-before-run rules for:

- `git push *` — remote write operations
- `gh repo *` — GitHub API operations
- Destructive bash commands (`rm -rf *`)

---

## Credential Rotation

If a secret is suspected to have been exposed:
1. Immediately rotate the affected credential
2. Check git log for any commits that may have included the file: `git log --all --full-history -- .env`
3. If committed, remove with `git filter-branch` or `git-filter-repo`
4. Notify affected services

---

## Agent Rules

Agents must never:
- Read `.env` or `secrets/` files
- Read `mas/projects/` (runtime project state not needed in agent prompts)
- Log or print secret values
- Make outbound network calls without explicit authorization (`Bash(curl *)` is denied by default)
