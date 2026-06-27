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

The expected sequence is:

1. Create or resume a MAS project.
2. Let MAS select the next agent with `mas prompt <project-id>` or `mas_prompt`.
3. Run that prompt in the chosen surface: Claude Code, Codex, OpenCode, GitHub
   Copilot chat, ChatGPT, Gemini, LM Studio, Ollama, or another model UI.
4. Feed the response back through `mas ingest` or `mas_ingest`.
5. Repeat until the project is closed, then commit with `MAS: <project-id>`.

For standard projects, intake should include an accepted `inquirer_agent` handoff
before implementation proceeds. Manual mode still consumes model tokens, so
`mas prompt`, `mas ingest`, and `mas log-tokens` feed token evidence into the same
audit trail used by the commit gate.
