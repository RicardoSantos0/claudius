# Walkthrough — your first MAS project

This is a hands-on tour of the project lifecycle using a small, neutral task:
**"build a tiny CLI calculator."** It uses no private data and runs entirely
locally. Pair it with [`examples/demo_project/`](../examples/demo_project/).

> The CLI never calls a model. It assembles the **prompt** for the next agent and
> tracks **state**; you (in Claude Code) or `mas run` do the actual work. So this
> walkthrough is fully reproducible offline.

## 0. Install + check

```bash
uv venv --python 3.12 .venv
#   Windows:        .venv\Scripts\activate
#   macOS/Linux:    source .venv/bin/activate
uv pip install -e .
mas doctor          # environment + paths sanity check
```

`mas doctor` should end with `Summary: ok=… warn=0 fail=0` (or list actionable
setup steps).

## 1. Initialize a project

For a small, well-scoped task use **lite** mode (3 phases: intake → execution →
closed). Larger or ambiguous work uses **standard** mode (full 9-phase governance);
see [`operation-modes.md`](operation-modes.md).

```text
$ mas init demo-calculator --mode=lite
[ok] Project initialized [lite]: …/mas/projects/proj-YYYYMMDD-NNN-demo-calculator
     Project ID  : proj-YYYYMMDD-NNN-demo-calculator
     State file  : …/proj-YYYYMMDD-NNN-demo-calculator/shared_state.yaml
     Request ID  : req-YYYYMMDDHHMMSS
     Mode        : lite
     Execution   : manual (no API calls)
```

This creates one folder per project with a `shared_state.yaml` — the single source
of truth for the project's identity, decisions, plan, and artifacts.

## 2. Check status anytime

```text
$ mas status proj-YYYYMMDD-NNN-demo-calculator

Project  : proj-YYYYMMDD-NNN-demo-calculator
Status   : active
Phase    : intake [lite]
Mode     : lite
Owner    : master_orchestrator
Completed phases : none
Pending handoffs : 0
Violations       : 0
Storage          : db=sqlite vector=disabled
Tokens (total)   : 0
```

## 3. Get the next agent's prompt

`mas prompt` assembles the full, governance-aware prompt for whoever should act
next (at intake, that's the Master Orchestrator):

```text
$ mas prompt proj-YYYYMMDD-NNN-demo-calculator
# Agent: master_orchestrator  |  Project: proj-YYYYMMDD-NNN-demo-calculator
# Prompt length: ~36000 chars
#------------------------------------------------------------
You are the **Master Orchestrator** of the Governed Multi-Agent Delivery System.
…
```

Paste that prompt into Claude Code (or run `mas run` for the autonomous loop) to
produce the next artifact. As work progresses you record decisions, the plan, and
deliverables into `shared_state.yaml`; the phase advances only when its exit
artifact exists on disk.

## 4. The lite lifecycle

| Phase | You produce | Exit artifact |
|-------|-------------|---------------|
| intake | a clarified spec + product/exec plan (collapsed in lite) | `planning/product_plan.yaml`, `planning/execution_plan.yaml` |
| execution | the deliverables (here: a `calculator.py` + tests) | confirmed files on disk |
| closed | an evaluation | `evaluation/project_evaluation.yaml` |

## 5. Close

```bash
mas close proj-YYYYMMDD-NNN-demo-calculator
```

Closing writes a final state snapshot and a `CLOSED.md`, and refreshes the registry
index. Use [`mas-document`](../skills/mas-document/) / [`mas-handoff`](../skills/mas-handoff/)
to produce the human-facing summary.

## What you end up with

A self-contained project folder (no database needed to read it):

```
proj-YYYYMMDD-NNN-demo-calculator/
├── shared_state.yaml          # identity, decisions, plan, artifacts
├── planning/                  # product_plan.yaml, execution_plan.yaml
├── evaluation/                # project_evaluation.yaml
└── CLOSED.md                  # human-readable closeout
```

That's the whole point: **durable, inspectable project memory** with governance
gates between phases — not a black box.
