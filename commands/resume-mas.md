# /resume-mas

Resume a MAS project from its last checkpoint after a session break.

## Usage

```
/resume-mas [project-id]
```

Project IDs use the format `proj-YYYYMMDD-NNN-slug` (e.g. `proj-YYYYMMDD-NNN-session-scheduler`).
If `project-id` is omitted, you will be prompted to list active projects.

## What this command does

1. Locates the latest `CHECKPOINT.md` for the project
2. Reads the checkpoint to reconstruct full project context
3. Reads current shared state to verify nothing has changed since the checkpoint
4. Invokes `master_orchestrator` with all context needed to continue from the exact point where the session ended

## Steps

$ARGUMENTS

You are resuming a MAS project after a session break.

**Step 1 — Locate the project**

If a project ID was provided as the argument, use it directly.
Otherwise, run:
```bash
uv run mas roster
ls mas/projects/
```
Then ask the user which project to resume.

**Step 2 — Read the checkpoint**

```bash
cat mas/projects/$PROJECT_ID/CHECKPOINT.md
```

Read this file carefully. It tells you:
- The current phase
- The last accepted handoff
- Any pending handoffs
- Active delivery risks
- The execution plan path

**Step 3 — Verify current state**

```bash
uv run mas status $PROJECT_ID
uv run mas pending $PROJECT_ID
```

Cross-check with the checkpoint. If there are pending handoffs, they take priority.

**Step 4 — Resume as master_orchestrator**

You are now acting as `master_orchestrator`. Based on the checkpoint:

- If there are **pending handoffs**: accept them and continue from the result
- If the last handoff was **accepted** and the phase is in progress: determine the next action from the execution plan and issue the appropriate handoff
- If a **phase just completed**: snapshot the state, advance the phase, and continue

Continue the project from exactly where it left off. Do not re-do completed work.

**Step 5 — Confirm resume to user**

Tell the user:
- Which project was resumed
- What phase it is in
- What the next action is
