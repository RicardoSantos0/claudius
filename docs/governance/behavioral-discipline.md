# Behavioral Discipline Guardrails

MAS cannot physically prevent every filesystem edit a capable local tool can make.
The enforceable boundary is commit and closure discipline: tracked work must carry
evidence that it passed through MAS.

## Local Commit Gate

Install hooks once per clone:

```bash
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

The `mas-discipline-commit-msg` hook requires commit messages to include:

```text
MAS: proj-YYYYMMDD-NNN-slug
```

The referenced local project must:

- exist under `mas/projects/`,
- have standard-mode handoff trace when standard mode is used,
- include an accepted `inquirer_agent` intake handoff,
- have non-zero token accounting,
- and be closed with `CLOSED.md` plus `final_shared_state.yaml`.

For intentional emergency bypasses, use:

```text
MAS-BYPASS: user-authorized rationale
```

Bypasses are not silent success. They create reviewable evidence and should be
scored as governance debt during evaluation.

## CI Marker Gate

CI cannot inspect local gitignored project state, so it validates the push commit
message only. Direct pushes must include either a `MAS:` marker or an explicit
`MAS-BYPASS:` rationale. Local hooks remain the stricter gate.

## Codex / OpenCode / Copilot Surfaces

All surfaces should use `mas prompt` / `mas ingest`, MCP `mas_prompt` /
`mas_ingest`, or `mas run`. A surface that edits files directly can still do so
at the operating-system level, but its commit will fail the local evidence gate
unless the corresponding MAS project exists and is complete.
