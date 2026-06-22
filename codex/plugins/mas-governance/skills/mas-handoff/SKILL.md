---
name: mas-handoff
description: Produce human-facing handoff summaries from MAS state for session transitions, PRs, agent-to-agent handoffs, or incident responses.
---

# MAS Handoff

Use this skill to produce a human-readable handoff document from MAS project state. This is distinct from internal MAS `handoff_engine.py` handoffs — it produces a readable summary for a human developer or collaborator.

## Trigger

Invoke when:
- Ending a session and need to summarize context for the next session
- Preparing a pull request description
- Handing off a blocked project to another developer or agent
- Producing a summary after an incident or policy violation

## Inputs

- `project_id` — required
- `variant` — `session` (default) | `pr` | `agent` | `incident`
- `scope` — optional; which parts to include

## Variants

| Variant | Use case |
|---------|----------|
| `session` | End-of-session handoff to the next session |
| `pr` | Pull request description from MAS state |
| `agent` | Formal MAS agent-to-agent handoff context |
| `incident` | Handoff after failure, blocked state, or policy violation |

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/checkpoints/ (latest)
mas/projects/<project_id>/handoffs/ (pending)
git status, git log --oneline -n 10
git diff --stat HEAD
```

## Output Format

```markdown
# MAS Handoff

## Context
- Project: <id>
- Phase: <current_phase>
- Branch: <branch>
- Last checkpoint: <timestamp>

## Completed
<list of completed tasks and artifacts with file paths>

## In Progress
<list of tasks currently in flight>

## Changed Files
<git diff --stat summary>

## Pending MAS Handoffs
<list of pending handoffs with their status and to/from agents>

## Open Questions
<list of open questions from shared state>

## Risks
<identified risks>

## Validation Status
<which acceptance criteria have been met / not met>

## Next Recommended Action
<concrete next step>

## Resume Command
`uv run mas status <project_id>`
```

## Rules

- Produce concrete, specific information — not vague summaries.
- Prefer file paths and exact values over prose descriptions.
- For `incident` variant: include timeline, expected vs. actual behavior, root cause candidates.
- Do not modify MAS state; this skill is read-only.
- Reference `standards/mas-governance.md` for phase gate requirements.
