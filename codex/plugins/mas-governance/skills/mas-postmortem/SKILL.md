---
name: mas-postmortem
description: Analyze failed phases, rejected handoffs, policy violations, regressions, or blocked projects. Produces a structured postmortem and feeds findings into MAS evaluation and training loops.
---

# MAS Postmortem

Use this skill after a failure, violation, or blocked state to understand what went wrong and prevent recurrence.

## Trigger

Invoke when:
- An evaluation phase failed
- A handoff was rejected
- A phase was blocked after retry
- A spawned agent failed
- An artifact was unexpectedly missing
- A policy violation was detected
- An assumption proved wrong and caused rework
- Token/cost consumption was significantly higher than expected

## Inputs

- `project_id` — required
- `incident_type` — one of the trigger categories above
- `scope` — `phase` | `handoff` | `agent` | `project`
- `depth` — `quick` | `standard` (default) | `full`

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/handoffs/ (rejected handoffs)
mas/projects/<project_id>/evaluations/ (failed evaluations)
mas/policies/*.yaml
git log --oneline -n 20
```

## Writes (when asked)

```text
mas/projects/<project_id>/postmortems/<timestamp>_postmortem.md
mas/roster/training_backlog.yaml (append training proposals)
```

## Procedure

1. Read the project state, failed handoffs, and evaluation reports.
2. Build a timeline of events leading to the failure.
3. Identify: expected behavior, actual behavior, deviation point.
4. Generate root cause candidates — do not pick a single cause without evidence.
5. Identify contributing factors (assumptions, missing context, policy gaps).
6. Identify what worked well (to preserve).
7. Produce action items with owner, priority, and validation.
8. Generate training/policy update proposals if recurrence risk is high.

## Output Format

```markdown
# MAS Postmortem

## Incident Summary
<one paragraph: what failed, when, impact>

## Timeline
<chronological list of events with timestamps>

## Expected Behavior
<what should have happened>

## Actual Behavior
<what actually happened>

## Root Cause Candidates
1. <candidate — evidence for/against>
2.

## Contributing Factors
<list of factors that made the failure more likely>

## What Worked
<list of things that went right>

## What Failed
<list of things that went wrong>

## Action Items

| Action | Owner | Priority | Due / Trigger | Validation |
|--------|-------|----------|---------------|------------|
| | | | | |

## Training / Policy Updates
<list of proposed training backlog entries or policy change proposals>

## Follow-up Evaluation
<what should be checked to confirm the issue is resolved>
```

## Writes / Follow-up

Postmortem results should feed into:

```text
mas/roster/training_backlog.yaml     (append training proposals)
mas/projects/<project_id>/postmortems/
mas/projects/<project_id>/evaluations/
mas/policies/ change proposals       (if policy gap identified)
```

## Rules

- Focus on systemic causes, not individual blame.
- Distinguish root causes from contributing factors.
- Record what worked — postmortems are not only about failure.
- All action items must have an owner and a validation step.
- Feed training proposals into `mas/roster/training_backlog.yaml`.
- Reference `standards/mas-governance.md` for policy requirements.
- For policy violations: escalate to master_orchestrator with the postmortem.
