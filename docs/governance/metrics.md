# MAS Evaluation Metrics

Reference for project-level metrics computed by `mas/core/engine/metrics_engine.py` (`MetricsEngine.evaluate_project`).

A metric with `mode='not_applicable'` is excluded from the project average — it had no signal in the current data. A metric with `mode='live'` and a numeric `score` in [0, 100] participates in `overall_project_score` (equal-weight average).

## Current Metric Set

| Metric | Range | Purpose |
|---|---|---|
| `goal_achievement` | 0-100 | Coverage of `project_definition.success_criteria` by completed task outputs / outcome evidence. |
| `acceptance_criteria_pass_rate` | 0-100 | Share of `project_definition.acceptance_criteria` with `met: true` and evidence. |
| `scope_adherence` | 0-100 | Penalizes blocked/failed/over-effort tasks relative to planned. |
| `documentation_completeness` | 0-100 | Presence of canonical phase artifacts on disk. |
| `phase_efficiency` | 0-100 | Handoffs per phase vs ideal; ip-003 exemptions reduce overhead. |
| `decision_quality` | 0-100 | Decision-log richness — rationale, alternatives_considered, count per phase. |
| `governance_compliance` | 0-100 | 100 − 7.5 × `governance_violations`, floored at 0. |
| `record_integrity` | 0-100 | 100 − 200 × retroactive_ratio. Live handoffs preferred. |
| `test_drift_detection` | 0-100 / N.A. | NEW (prop-TP-042) — paired-test coverage for implementation changes. |

## `test_drift_detection` (prop-TP-042)

### Purpose

Catch the class of bug where an implementation task changes a module constant or default and an existing test still asserts the old value. Such drift typically slipped to evaluation in past projects (e.g. `issue-001` in proj-YYYYMMDD-NNN-ml-autograder-improvements).

### Signal

The metric consumes a list of changed file paths, conventionally produced via `git diff --name-only <range>` over the project's commit set. The list is supplied through shared state:

```yaml
evaluation:
  test_drift_context:
    changed_files:
      - mas/core/engine/handoff_engine.py
      - mas/tests/unit/test_handoff_engine.py
      - mas/CHANGELOG.md
```

When `changed_files` is empty or absent, the metric returns `mode='not_applicable'` and is excluded from the average.

### Scoring

1. Filter docs (`.md`, `.rst`, `.txt`, anything under `docs/`, `CHANGELOG.md`) and config (`.yaml`, `.yml`, `.toml`, `.cfg`, `.ini`, `.json`) out of the impl set.
2. The impl set = `.py` files not under `tests/`, not starting with `test_`, not ending with `_test.py`.
3. The test set = anything under `tests/` plus any file whose basename starts with `test_` or ends with `_test.py`.
4. For each impl file `P`, it is **paired** iff any test-set path mentions `P`'s module basename (filename without extension) as a substring.
5. Score = `paired / impl_total × 100`.

Edge cases:
- No impl files in the changeset → 100 (nothing to drift).
- All impl files unpaired → 0.

### Interpretation

| Score | Reading |
|---|---|
| 100 | Every implementation change has at least one paired test edit. |
| 50-99 | Partial drift — surface unpaired files in the evaluation report. |
| 0-49 | Significant drift — recommend a follow-up reliability_engineer pass. |
| not_applicable | Evaluator did not supply changed-files context. Add it to make the metric live. |

### Implementation

- Function: `MetricsEngine.score_test_drift_detection(changed_files)` (pure).
- Wired into `MetricsEngine.evaluate_project()` via `shared_state.evaluation.test_drift_context.changed_files`.
- Tests: `mas/tests/unit/test_test_drift_detection_metric.py` (5 cases — N.A., docs-only, paired, unpaired, partial).

### Reference

- Proposal: prop-TP-042 / proj-YYYYMMDD-NNN-ml-autograder-improvements.
- Sprint: proj-YYYYMMDD-NNN-mas-trainer-proposals-impl.
