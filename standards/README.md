# Standards

This directory contains reusable conventions for agents, skills, documentation, code, SQL, governance, and security.

## Purpose

Standards reduce repeated prompt text, documentation drift, and inconsistency across agents and skills. Instead of duplicating rules in every agent prompt or skill, reference the relevant standard file.

## Normative vs Advisory

**Normative** standards are expected to be followed by agents and validated by checks where possible. Violations should be caught by validators or CI.

**Advisory** standards provide style and quality guidance. They are not enforced mechanically but should be followed in code review and documentation.

| Standard | Type | Description |
|----------|------|-------------|
| `agent-frontmatter.md` | Normative | Canonical Claude agent frontmatter fields and naming |
| `mas-governance.md` | Normative | Trust tiers, phase gates, handoff acceptance, escalation |
| `mas-project-lifecycle.md` | Normative | Full MAS project phases and exit artifacts |
| `wire-protocol.md` | Normative | MAS wire protocol v1.0 format |
| `security-and-permissions.md` | Normative | Secret hygiene, export rules, permission baseline |
| `commit-style.md` | Advisory | Commit message format and types |
| `documentation-format.md` | Advisory | Document section ordering and format |
| `python-standards.md` | Advisory | Python style, types, testing, CLI behavior |
| `sql-conventions.md` | Advisory | SQL/SQLite naming, migrations, parameterized queries |
| `knowledge-sources.md` | Advisory | Which knowledge source to use (graphify vs episodic DB vs notebooklm vs registry) |

## How Agents and Skills Reference Standards

In agent prompts and SKILL.md files, reference standards with:

```
Reference: standards/<filename>.md
```

Example from an agent prompt:
```
→ See standards/mas-governance.md for trust tier and phase gate requirements.
→ See standards/security-and-permissions.md for export hygiene rules.
```

## Maintenance

- Standards are source-controlled in this repo.
- Setup scripts symlink `~/.claude/standards` → `<repo>/standards` for global access.
- When a standard changes, update references in affected agents/skills.
- Machine-checkable standards should have a corresponding validator in `scripts/`.
