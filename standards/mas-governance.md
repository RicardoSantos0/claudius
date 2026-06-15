# MAS Governance Standard

**Type:** Normative
**Applies to:** All MAS agents and skills
**Source of truth:** `mas/policies/*.yaml`

## Normative Hierarchy

Use this precedence order when wording appears to conflict:
1. Policy files under `mas/policies/*.yaml` are canonical runtime rules
2. Foundation contracts under `mas/foundation/*` define schema and protocol structures
3. Standards under `standards/*` provide human-readable obligations and must not override policy files
4. Agent and skill files provide role-specific operating guidance and must defer to policy and foundation contracts

If any rule in this file conflicts with policy text, follow policy text and treat this file as needing correction.

---

## Trust Tiers

| Tier | Label | Agents | Authority |
|------|-------|--------|-----------|
| T0 | Core | `master_orchestrator`, `scribe_agent` | Full workflow coordination; can advance phases; can approve spawns |
| T1 | Established | `hr_agent`, `inquirer_agent`, `product_manager_agent`, `project_manager_agent`, `evaluator_agent`, `trainer_agent`, all consultants | Can execute assigned phases; cannot spawn; cannot modify core state |
| T2 | Supervised | `spawner_agent` | Supervised execution only; all outputs reviewed by T0 |
| T3 | Provisional | Spawned agents | Limited trust; cannot transition phases; all outputs reviewed |

---

## Phase Gates

Valid phase sequence:

```
intake → specification → planning → capability_discovery → execution → review → evaluation → improvement → closed
```

**Each phase requires an exit artifact before advancing:**

| Phase | Exit artifact |
|-------|--------------|
| intake | `intake/clarified_spec.yaml` |
| specification | `planning/product_plan.yaml` |
| planning | `planning/execution_plan.yaml` |
| execution | Confirmed deliverable files on disk |
| evaluation | `evaluation/project_evaluation.yaml` |
| improvement | `improvement/improvement_proposals/` (at least one file) |

**Phase transition rules:**
1. Verify exit artifact exists on disk
2. Snapshot shared state
3. Scribe records phase close (`s: "scribe:recorded"`) — blocking gate
4. Master advances `current_phase` only after Scribe confirms
5. Log transition in `workflow.completed_phases`

---

## Handoff Protocol

Every delegation requires a formal handoff via `handoff_engine.py create`. Informal delegation is a governance violation.

Handoff lifecycle:
1. **Created** — `master_orchestrator` creates with task and payload
2. **Accepted** — receiving agent accepts before starting work
3. **Returned** — receiving agent creates return handoff to master
4. **Accepted by master** — master verifies deliverables, accepts

**Delivery Verification:** Before accepting any completion handoff, master must verify all claimed files exist on disk.

---

## Consultant Review

Required for: spawn approvals, high/critical risk decisions, agent disagreements, post-approval scope changes.

Available consultants: `risk_advisor`, `quality_advisor`, `devils_advocate`, `domain_expert`, `efficiency_advisor`

- Architecture decisions: `domain_expert`, `risk_advisor`, `quality_advisor`
- Scope/governance decisions: `risk_advisor`, `devils_advocate`, `efficiency_advisor`
- Critical decisions: all five

---

## Spawning Rules

Prerequisites for spawning:
1. Formal Capability Gap Certificate from `hr_agent`
2. Positive consultant panel review
3. Evaluator verification of the spawned package

Limits: max 3 spawns per project, 1 per phase, no recursive spawning.

---

## Escalation Triggers

Escalate to human when:
- Risk classification is "critical"
- Consultant raises unresolvable concern
- Two consecutive spawn requests denied
- Phase blocked after retry
- All 5 consultants unanimously flag high-risk
- Trust tier promotion requested
- Governance policy change needed

---

## Shared State Access Control

Agents write only to fields they own:
- `master_orchestrator` — `core_identity`, `decisions`, `project_definition`, `workflow`
- `scribe_agent` — `artifacts`
- `inquirer_agent` — `project_definition.clarified_specification`, `project_definition.success_criteria`
- `evaluator_agent` — `evaluation`

See `mas/policies/governance_policy.yaml` for the canonical access control list.
