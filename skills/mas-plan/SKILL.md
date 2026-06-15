---
name: mas-plan
description: Produce or update a phase-aware MAS execution plan based on current project state, objectives, and constraints.
---

# MAS Plan

Use this skill to produce or update a phase-aware execution plan for an active or new MAS project.

## Trigger

Invoke when:
- Starting a new MAS project and need a structured plan
- Current plan is stale after a scope change
- Re-planning after a blocked phase or evaluation failure

## Inputs

- `project_id` — required
- `objective` — what needs to be accomplished
- `depth` — `lite` | `standard` (default) | `full`
- `constraints` — optional additional constraints

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/CHECKPOINT.md (if present)
mas/projects/<project_id>/checkpoints/  (latest file, fallback)
mas/policies/handoff_protocol.yaml
mas/policies/spawn_policy.yaml
mas/policies/evaluation_policy.yaml
mas/roster/registry_index.yaml
mas/projects/<project_id>/planning/product_plan.yaml (if exists)
```

## Writes (when asked)

```text
mas/projects/<project_id>/plans/<timestamp>_plan.md
mas/projects/<project_id>/shared_state.yaml (updates via master_orchestrator only)
```

## Procedure

1. Read the current project state and latest checkpoint.
2. Identify the current phase and what artifacts are present vs. missing.
3. Identify the objective and applicable constraints.
4. Select appropriate depth: `lite` for ≤5 file changes, `standard` for most projects, `full` for multi-agent/architecture projects.
5. Produce a structured plan with explicit phase path, agent assignments, required artifacts, and handoff sequences.
6. Flag any governance requirements (mandatory phases, consultation triggers, spawn requirements).
7. Return the plan and, if writing to disk, confirm the path.

## Output Format

```markdown
# MAS Plan

## Goal
<objective statement>

## Assumptions
<list of assumptions — clearly separated from facts>

## Required Artifacts
<list of files that must exist before execution can proceed>

## Phase Path
<ordered list of phases with rationale for each>

## Agent / Team Assignments
<agent → task mapping>

## Handoffs
<list of handoffs required and their sequence>

## Risks
<identified risks with mitigations>

## Validation Plan
<how each phase will be validated>

## Next 3 Actions
1.
2.
3.
```

## Rules

- Do not bypass mandatory MAS phases (intake, specification, planning, capability_discovery, execution, evaluation).
- Record assumptions separately from facts.
- Produce different plans for `lite` vs `full` scope.
- Identify required artifacts before execution — do not plan execution without confirmed artifact list.
- Reference `mas/policies/` and `standards/mas-project-lifecycle.md` for phase gate requirements.
- Never propose bypassing the handoff protocol.
