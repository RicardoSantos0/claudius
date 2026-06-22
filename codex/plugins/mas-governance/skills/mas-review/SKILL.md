---
name: mas-review
description: Review current MAS project state, stale context, branch status, pending handoffs, risks, and next actions before resuming work.
---

# MAS Review

Use this skill when starting or resuming work in a `claude-config` MAS project.

## Trigger

Invoke when:
- Starting a new session in an active MAS project
- Resuming interrupted work
- Uncertain what the current state or next action is
- Preparing for a handoff or phase transition

## Inputs

- `project_id` — optional; detected from context if not provided
- `scope` — `quick` | `standard` (default) | `deep`
- `branch` — optional; current git branch context

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/CHECKPOINT.md (if present)
mas/projects/<project_id>/checkpoints/  (latest file, fallback)
mas/projects/<project_id>/handoffs/     (pending files, if directory exists)
mas/projects/<project_id>/evaluation/   (latest report, if directory exists)
mas/roster/registry_index.yaml
mas/policies/*.yaml
git status
git log --oneline -n 10
```

## Procedure

1. Identify the active project ID from the user, current directory, or MAS state.
2. Read the project `shared_state.yaml` — extract: `current_phase`, `current_owner`, `acceptance_criteria`, `decision_log`, `completed_phases`.
3. Read CHECKPOINT.md if present; otherwise read the latest file under checkpoints/.
4. List pending handoffs from the handoffs directory when present; if absent, report missing handoff directory as missing evidence.
5. Read the latest evaluation report from evaluation/ when present.
6. Check `git status` and `git log --oneline -n 10` for recent changes.
7. Identify: stale context, missing artifacts, blocked handoffs, unresolved questions, policy concerns.
8. Return a concise review with the next recommended action.

## Output Format

```markdown
# MAS Review

## Project
- Project ID: <id>
- Current phase: <phase>
- Current owner: <agent>
- Branch: <branch>

## Current State
<summary of what is happening right now>

## Recent Work
<list of recent commits and changes>

## Pending Handoffs
<list of pending handoffs with their status>

## Open Questions
<list of unresolved questions from shared state>

## Risks / Stale Context
<identified risks, stale artifacts, missing files>

## Recommended Next Action
<concrete next step>
```

## Rules

- Do not modify MAS state unless explicitly asked.
- Do not invent missing state; mark it as missing.
- Treat MAS state (`shared_state.yaml`) as canonical.
- If expected folders are absent, report missing evidence explicitly and continue with available sources.
- Prefer specific file paths and concrete next actions over vague summaries.
- Reference `mas/policies/` for governance constraints.
- See `standards/mas-governance.md` for trust tier and phase gate rules.
