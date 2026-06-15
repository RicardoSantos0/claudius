# Commit Style Standard

**Type:** Advisory
**Applies to:** All commits in `claude-config`

---

## Format

```
<type>(<scope>): <summary>

<body>

<footer>
```

- **type** — required; see table below
- **scope** — optional; the area affected (e.g., `export`, `mas-review`, `spawn`)
- **summary** — required; present tense, max 72 characters, no period at end
- **body** — optional; explain *why*, not *what*; wrap at 80 characters
- **footer** — optional; reference issues, co-authors, breaking changes

---

## Types

| Type | Use when |
|------|----------|
| `feat` | Adding a new feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only changes |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `chore` | Build process, config, tooling changes |
| `security` | Security improvement, secret hygiene, permissions |
| `governance` | MAS governance policy or protocol change |
| `agent` | Agent prompt or frontmatter change |
| `skill` | New or updated skill |
| `policy` | Policy file change |

---

## Examples

```
security(export): add source-only archive scanner

scripts/check_archive_clean.py fails with exit 1 if any blocked path
(.env, .venv/, mas/projects/, browser state) appears in an archive.
Tested with dirty and clean archives.
```

```
skill(mas-review): add MAS project review workflow

Adds skills/mas-review/SKILL.md covering session start/resume
context review, pending handoff detection, and next-action recommendation.
```

```
policy(spawn): clarify provisional agent acceptance criteria

Provisional agents must produce verifiable output before trust promotion.
Updated spawn_policy.yaml to require evaluator_agent sign-off.
```

```
agent(master-orchestrator): add TP-017 pre-dispatch gate
```

---

## Co-authorship

When Claude Code or a MAS agent generates a commit:

```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
