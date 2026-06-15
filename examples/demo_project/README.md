# Demo project — tiny CLI calculator

A neutral, self-contained example you can run end-to-end with no private data. It
shows the MAS lifecycle on a deliberately small task: **build a command-line
calculator that adds, subtracts, multiplies, and divides two numbers.**

Follow the step-by-step commands in [`docs/walkthrough.md`](../../docs/walkthrough.md).
This folder holds a **sample** of what the planning artifact looks like once
filled in — see [`product_plan.sample.yaml`](product_plan.sample.yaml).

## Try it

```bash
mas init demo-calculator --mode=lite
mas status   demo-calculator      # (use the printed proj-... id)
mas prompt   demo-calculator      # next-agent guidance
# …do the work (write calculator.py + tests), record it in shared_state…
mas close    demo-calculator
```

## Expected lifecycle (lite)

1. **intake** → clarified scope + plan (`planning/product_plan.yaml`, `execution_plan.yaml`)
2. **execution** → the deliverable: `calculator.py` + a couple of tests
3. **closed** → `evaluation/project_evaluation.yaml` + `CLOSED.md`

## What this demonstrates

- **Durable, inspectable state** — every decision and artifact is recorded in
  `shared_state.yaml`, readable without any database.
- **Phase gates** — you cannot advance a phase without its exit artifact on disk.
- **Governance without overhead** — lite mode keeps a tiny task tiny while still
  giving you a plan, an audit trail, and a clean closeout.

> No runtime databases or logs are committed here — only the human-readable
> artifacts a new user needs to follow along.
