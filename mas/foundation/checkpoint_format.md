# CHECKPOINT.md Format Standard

Version: 1.0  
Owner: `checkpoint_writer.py`

---

## Purpose

`CHECKPOINT.md` is the canonical session-resume document for an active MAS project.
It is generated automatically after:

1. Every `handoff_engine.accept()` call
2. Every `shared_state_manager.write()` that changes `core_identity.current_phase`

It can also be generated on demand:

```bash
uv run python mas/core/checkpoint_writer.py --project-id {project_id}
```

---

## Required Sections

All sections below must be present in every CHECKPOINT.md.

### 1. Document Header

```markdown
# CHECKPOINT — {project_id}
> Generated: {YYYY-MM-DD HH:MM UTC}
```

### 2. Identity Table

| Field | Value |
|-------|-------|
| Project ID | `{project_id}` |
| Request ID | `{request_id}` |
| Status | `{active \| paused \| closed}` |
| Current phase | **`{current_phase}`** |

### 3. Phase Progress

A single line showing all 9 phases with status markers:

- Completed phases: `~~phase~~` (strikethrough)
- Current phase: `**phase**` (bold)
- Future phases: `phase` (plain)

Example:
```
~~intake~~ → ~~specification~~ → **planning** → capability_discovery → execution → review → evaluation → improvement → closed
```

### 4. Project Brief (summary)

The original brief or a ≤300-character summary of it.
Omitted only if `project_definition.brief_summary` and `original_brief` are both empty.

### 5. Execution Plan

```markdown
Path: `{execution_plan_path}`
```

Value is `—` if no plan has been compiled yet.

### 6. Last Handoff

A table showing the most recent entry in `workflow.handoff_history`:

| Field | Value |
|-------|-------|
| ID | `{handoff_id}` |
| From | `{from_agent}` |
| To | `{to_agent}` |
| Timestamp | {YYYY-MM-DD HH:MM UTC} |
| Status | `{pending \| accepted \| rejected}` |
| Task | {task_description} |

Followed by the payload summary as a blockquote.

Shows `_No handoffs yet._` if `handoff_history` is empty.

### 7. Pending Handoffs

A markdown table of all handoffs with `acceptance.status == "pending"`.

```markdown
| ID | From | To | Task |
|---|------|----|----|
| `{id}` | `{from}` | `{to}` | {task} |
```

Shows `_No pending handoffs._` if none exist.

### 8. Active Delivery Risks (conditional)

Present only when `execution.delivery_risks` is non-empty:

```markdown
## Active Delivery Risks

- **[{severity}]** {description}
```

### 9. Spawned Agents Count (conditional)

Present only when `spawning.spawned_agents` is non-empty.

### 10. How to Resume

Always the last section. Must include `/resume-mas {project_id}` and the manual
`mas status` / `mas pending` verification commands.

---

## Guarantees

- File is always at: `mas/projects/{project_id}/CHECKPOINT.md`
- File is always UTF-8 encoded
- File is always overwritten (never appended) — it reflects current state
- Checkpoint write failure is non-fatal: it must never block a handoff or state write
- The file is **not** tracked by git (covered by `mas/projects/` in `.gitignore`)

---

## Reading a Checkpoint on Resume

When `/resume-mas {project_id}` is invoked:

1. Read `CHECKPOINT.md` for narrative context
2. Verify with `uv run mas status {project_id}` — ground truth is always shared state
3. If checkpoint and state disagree, **trust shared state** — the checkpoint may be stale
4. Re-generate checkpoint after verification: `uv run python mas/core/checkpoint_writer.py --project-id {project_id}`
