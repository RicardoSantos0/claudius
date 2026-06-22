---
name: mas-examine
description: Analyze code, docs, state, policies, or architecture by type, scope, diff, or governance mode. Surfaces findings without modifying state.
---

# MAS Examine

Use this skill to analyze any aspect of the `claude-config` repository or a MAS project without modifying anything.

## Trigger

Invoke when:
- Investigating a policy, agent, skill, or module before making changes
- Reviewing current governance state before a phase transition
- Analyzing a git diff before committing
- Auditing agent registry or frontmatter
- Understanding failure patterns in evaluation reports

## Inputs

- `target` — what to examine (see modes below)
- `scope` — optional additional scope narrowing
- `project_id` — optional; required for state/governance modes

## Modes

| Mode | Description | Example |
|------|-------------|---------|
| `type` | Analyze all files of a type/category | `/mas-examine agents` |
| `scope` | Analyze a bounded area, module, or policy | `/mas-examine scope mas/core/config.py` |
| `hybrid` | Combine type and scope | `/mas-examine scope mas/core/ python` |
| `diff` | Analyze changed files only | `/mas-examine diff` |
| `state` | Analyze MAS runtime state and handoffs | `/mas-examine state project proj-001` |
| `governance` | Analyze policies, trust tiers, spawn, escalation | `/mas-examine governance spawn policy` |

## Suggested Delegation Map

| Examination target | Preferred MAS role |
|------|------|
| policy / governance | `risk_advisor`, `master_orchestrator` |
| code quality | `quality_advisor` |
| architecture | `domain_expert` |
| documentation | `scribe_agent` |
| data / state | `analysis_engineer` (if available) |
| failure patterns | `evaluator_agent`, `trainer_agent` |

## Reads

Depends on mode:
- `agents` → `agents/*.md`
- `scope <path>` → specified file or directory
- `diff` → `git diff`, `git status`
- `state <project_id>` → `mas/projects/<project_id>/shared_state.yaml`, handoffs
- `governance` → `mas/policies/*.yaml`, `mas/roster/registry_index.yaml`

## Output Format

```markdown
# MAS Examine — <target>

## Summary
<one-paragraph summary of what was examined and key findings>

## Findings

### <finding category>
<detailed findings>

## Issues Found
<list of problems, inconsistencies, or risks — with file paths>

## Recommendations
<list of suggested changes — not executed, just suggested>
```

## Rules

- Read-only: never modify state, files, or handoffs during examination.
- Reference `mas/policies/` for governance constraints.
- Reference `standards/` for applicable conventions.
- Report what you found, not what you assumed.
- If a finding requires action, recommend it — do not act.
- Distinguish confirmed findings from hypotheses; label uncertain items explicitly.
