---
name: mas-logwork
description: Track session work context, time, and artifacts and feed them into MAS evaluation and documentation.
---

# MAS Logwork

Use this skill to track session work and maintain a structured log of what was done, when, and why — feeding into MAS evaluation and post-session documentation.

## Trigger

Invoke when:
- Starting a work session (`start`)
- Pausing work (`pause`)
- Resuming work (`resume`)
- Ending a work session (`stop`)
- Generating a session summary (`summary`)

## Inputs

- `command` — `start` | `pause` | `resume` | `stop` | `summary`
- `project_id` — required
- `objective` — what is being worked on (for `start`)
- `note` — optional context note

## Writes

```text
mas/projects/<project_id>/worklog.md
mas/projects/<project_id>/logs/session_<timestamp>.md
```

## Procedure

### start
1. Record session start time, project ID, and objective.
2. Write entry to `worklog.md` and create `logs/session_<timestamp>.md`.

### pause
1. Record pause time and current status.
2. Append to active session log.

### resume
1. Record resume time.
2. Append to active session log.

### stop
1. Record stop time.
2. List files changed, commands run (if relevant), phase changes, handoffs created/accepted.
3. Write final session log.
4. Suggest `mas-document` for checkpoint if significant work was done.

### summary
1. Read active session log.
2. Produce summary: objective, duration, completed tasks, artifacts, blockers, next actions.

## Session Log Format

```markdown
# Session Log — <project_id>

## Start
- Time: <ISO timestamp>
- Objective: <what is being worked on>

## Work Items
- [HH:MM] <task completed or in progress>
- [HH:MM] <decision made — with rationale>
- [HH:MM] <file created/modified: path>
- [HH:MM] <handoff created: id, to, task>

## Blockers
- <any blockers encountered>

## End
- Time: <ISO timestamp>
- Duration: <HH:MM>
- Status: complete | paused | blocked

## Summary
<brief summary of what was accomplished>
```

## Rules

- Record time in ISO format.
- Record file paths for any file created or modified.
- Record handoff IDs for any handoffs created or accepted.
- Record blockers explicitly — do not drop them.
- Feed stop/summary output into `mas-document` for checkpoint creation.
- Reference `standards/documentation-format.md` for format conventions.
