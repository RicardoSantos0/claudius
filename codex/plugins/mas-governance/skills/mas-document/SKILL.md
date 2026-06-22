---
name: mas-document
description: Update human-readable and MAS-native documentation after work — checkpoints, decision logs, artifact indexes, and progress summaries.
---

# MAS Document

Use this skill after completing work to update documentation in both human-readable and MAS-native formats.

## Trigger

Invoke when:
- Completing a phase or milestone and need to record what happened
- Creating a session note at end-of-session
- Writing a checkpoint after significant work
- Updating artifact indexes or decision logs

## Inputs

- `project_id` — required
- `mode` — `brief` | `standard` (default) | `full`
- `scope` — what to document: `phase`, `session`, `decision`, `artifact`, `all`

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/decisions/decision_log.yaml
mas/projects/<project_id>/CHECKPOINT.md (if present)
git diff (changes since last checkpoint)
mas/projects/<project_id>/handoffs/ (completed handoffs, if directory exists)
```

## Writes (depending on mode and scope)

```text
brief:    mas/projects/<project_id>/logs/session_<timestamp>.md
standard: mas/projects/<project_id>/CHECKPOINT.md (or checkpoints/<timestamp>_checkpoint.yaml when required)
          mas/projects/<project_id>/decisions/decision_log.yaml (append)
full:     all of the above + artifact index + handoff-ready summary
```

## Procedure

1. Read current project state and git diff.
2. Identify completed work, decisions made, and artifacts produced.
3. Select output mode based on scope and depth.
4. Write documentation files — prefer facts from MAS state and git diff over prose.
5. Separate completed work from planned work clearly.
6. Record unresolved questions explicitly.
7. Do not overwrite previous decisions — append only.

## Output Format (standard)

```markdown
# Session / Phase Documentation

## Summary
<what was accomplished>

## Completed Work
<list of completed tasks with evidence>

## Decisions Made
<list of decisions with rationale>

## Artifacts Produced
<list of files created or modified with paths>

## Open Questions
<list of unresolved questions>

## Risks
<identified risks>

## Next Actions
<what needs to happen next>
```

## Rules

- Prefer facts from `git diff`, MAS state, and completed handoffs.
- Separate completed work from planned work.
- Record unresolved questions explicitly — never silently drop them.
- Do not overwrite previous decisions; append with timestamp and context.
- If expected documentation paths are missing, report missing evidence explicitly and continue with available sources.
- Follow ownership boundaries: this skill prepares documentation content and updates only when invoked by an authorized owning role.
- Reference `standards/documentation-format.md` for format conventions.
- Reference `mas/policies/` for documentation requirements at phase boundaries.
