# Handoff Policies

Documents the runtime gates implemented in `mas/core/engine/handoff_engine.py` and the policy hooks in `mas/policies/governance_policy.yaml`.

## Consecutive-Return-Handoff Gate (TP-milestone-c-001)

### Problem

A prompt-only "do not ping-pong" rule for the Master Orchestrator proved insufficient: the pattern recurred in `proj-c` after the rule was added in `b-003`. Prompts cannot enforce; the engine can.

### Pattern Detected

Three consecutive handoffs on the same task without forward progress:

```
n-2:  A -> B   (task T)
n-1:  B -> A   (task T, return)
n  :  A -> B   (task T, re-issue)  <-- consecutive return detected here
```

Task identity is **exact match on `task_description`**. Distinct tasks (e.g. `"Task A"` then `"Task B"`) do not trigger.

### Defaults and Opt-In

The gate is **disabled by default** for backward compatibility with in-flight projects whose handoff chains were opened before the gate landed.

A project opts in by appending a single `policy_flag` to `decisions.policy_flags`:

```yaml
decisions:
  policy_flags:
    - {type: consecutive_return_handoff_gate, status: enabled}
```

### Behavior

| State | Behavior |
|---|---|
| **Disabled** (default) | Emits a hint-level audit warning only; no policy_flag recorded; the handoff is created. |
| **Enabled** | Appends `{type: consecutive_return_handoff, severity: warn, ...}` to `decisions.policy_flags`; the handoff is **still created** (non-blocking) so a project mid-flight cannot deadlock; the violation is surfaced at evaluation. |

Non-blocking by design: the gate raises governance signal, not a hard error. Trainer proposals or master_orchestrator intervention are expected to break the ping-pong rather than the engine refusing the handoff.

### Implementation

- Detection: `_detect_consecutive_return(history, from_agent, to_agent, task)` — pure function, no state mutation.
- Policy lookup: `_consecutive_return_gate_enabled(state)` — reads `decisions.policy_flags`.
- Wiring: invoked inside `HandoffEngine.create()` after `_warn_missing_tasks_completed`, before appending the new handoff to `workflow.handoff_history`.

### Tests

`mas/tests/unit/test_handoff_engine_consecutive_return_gate.py`:
1. **Happy path / backward compat:** gate off, A->B->A->B creates no flag.
2. **Edge — opt-in:** gate enabled, A->B->A->B records a `consecutive_return_handoff` flag.
3. **Edge — distinct tasks:** gate enabled, A->B (Task A) -> B->A -> A->B (Task B) does **not** flag, because task identity differs.

### Reference

- Proposal: TP-milestone-c-001 (proj-YYYYMMDD-NNN).
- Sprint: proj-YYYYMMDD-NNN-mas-trainer-proposals-impl.
