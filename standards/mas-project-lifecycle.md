# MAS Project Lifecycle Standard

**Type:** Normative
**Applies to:** All MAS projects
**Source of truth:** `master_orchestrator.md`, `mas/policies/`

---

## Phase Overview

```
intake → specification → planning → capability_discovery → execution → review → evaluation → improvement → closed
```

## Full vs Lite Workflow

**Full workflow** — for projects with:
- Multiple agents, external integrations, new architecture
- >5 file changes
- User-visible behaviour changes

**Lite workflow** — for projects with:
- ≤5 file changes
- Single-concern bug fixes
- Isolated text/config edits
- No new agents needed

Lite collapses intake+specification and skips the consultant review phase. Still requires: `shared_state.yaml`, `product_plan.yaml`, `execution_plan.yaml`, `project_evaluation.yaml`.

---

## Phase Details

### intake
**Goal:** Transform raw brief into a clarified spec.
**Owner:** `inquirer_agent`
**Exit artifact:** `intake/clarified_spec.yaml` (score ≥ 0.85)
**Key rules:**
- Max 3 Q&A rounds with the user
- All 7 required fields must be present: project_goal, problem_statement, scope_inclusions, scope_exclusions, constraints, success_criteria, expected_outputs

### specification
**Goal:** Produce a structured product plan with requirements and acceptance criteria.
**Owner:** `product_manager_agent`
**Exit artifact:** `planning/product_plan.yaml`
**Key rules:**
- Requirements categorized as MoSCoW
- Each must-have requirement has at least one acceptance criterion
- Each acceptance criterion has a matching `test_strategy.acceptance_test_specs` entry before execution planning begins
- If consultation is required or selected, it happens after product-plan creation and before `project_manager_agent` produces the execution plan

### planning
**Goal:** Produce a concrete execution plan with phases, tasks, agents, and dependencies.
**Owner:** `project_manager_agent`
**Exit artifact:** `planning/execution_plan.yaml`
**Pre-execution gate:** Master must populate `project_definition.acceptance_criteria` and `decisions.decision_log` before this phase closes.
**Test-first gate:** Execution plan must schedule test-definition tasks before implementation tasks, and implementation tasks must depend on the relevant test-definition task IDs.

### capability_discovery
**Goal:** Identify which agents exist for the required capabilities; certify gaps.
**Owner:** `hr_agent`
**Exit artifact:** `hr/deployment_plan.yaml`
**Key rules:**
- HR produces the DeploymentPlan; Master executes it
- Master may not re-derive routing independently
- Gap certificates require a spawn/defer/no-action decision from Master

### execution
**Pre-dispatch gate (TP-017):** `mas/projects/{project_id}/planning/product_plan.yaml` must exist before any delivery agent is dispatched.
**Test-first pre-dispatch gate:** product plan must define `test_strategy.test_first_required: true`; every acceptance criterion must have a pre-development test spec; implementation work may not start until those test-definition tasks exist.
**Owner:** Delivery agents per DeploymentPlan
**Exit artifact:** All deliverable files confirmed on disk
**Verification:** Master must verify every claimed file before accepting completion handoffs.

### review
**Goal:** Spawn opportunity review before evaluation.
**Owner:** `master_orchestrator`
**Required:** Assess whether any capability gaps warrant formal spawn proposals (spawn/defer/no-action with rationale).

### evaluation
**Goal:** Score the project on governance and delivery metrics.
**Owner:** `evaluator_agent`
**Exit artifact:** `evaluation/project_evaluation.yaml`
**Scoring fields:** `acceptance_criteria_pass_rate`, `goal_achievement`, `decision_quality`, `documentation_completeness`
**Consultation gate:** `risk_advisor`, `quality_advisor`, and `efficiency_advisor` review evaluation evidence before Master accepts final conclusions or closes the project.

### improvement
**Goal:** Produce improvement proposals from evaluation findings.
**Owner:** `trainer_agent`
**Exit artifact:** At least one file in `improvement/improvement_proposals/`

### closed
**Actions:** Project summary, lessons learned, final change_log entry.
**Note:** Graph memory steps are not required for closure.

---

## Mandatory State Fields (Metrics Gate)

Before advancing past planning, master must populate:
1. `project_definition.acceptance_criteria` — one entry per criterion, `met: false`
2. `decisions.decision_log` — at least 1 entry per execution phase

Missing fields cannot be backfilled after the fact and will score 0 on governance metrics.

---

## Project Initialization

```bash
uv run mas init <slug>
# Verify shared_state.yaml exists
# Create handoff to scribe_agent for folder init
# Wait for scribe:recorded before proceeding
```
