# Prompt Token and Caching Contract

This contract defines how MAS reduces prompt tokens without weakening accuracy,
and how all provider surfaces report token and cache behavior truthfully.

## Measurement Classes

MAS keeps prompt construction telemetry separate from model-use telemetry:

| Class | Event | Meaning |
|---|---|---|
| Prompt preview | `prompt_estimated` | Heuristic estimate produced when MAS assembles a prompt; non-billable by itself |
| Observed model turn | `agent_call` | A response was run or manually ingested |
| Provider correction | `agent_call` via `mas log-tokens` | Exact provider/surface counts, including cache fields when exposed |

`mas tokens` reports observed calls and preview estimates separately. A preview
must never be counted as a model call, cost, or cache saving.

Token payloads identify their measurement source. Provider metadata may include
`cached_input_tokens`, `cache_creation_input_tokens`, and
`billable_input_tokens`. Cache savings are reportable only from these observed
provider values, never inferred from a fingerprint match.

## Cache-Ready Prompt Shape

`PromptAssembler.last_prompt_metadata` partitions the prompt into:

- `static_prefix`: the stable agent template;
- `state`;
- `memory`;
- `skills`;
- `runtime`; and
- `unclassified`.

The metadata includes total estimated tokens and a SHA-256 fingerprint, character
count, and token estimate for the stable prefix. The agent template is placed
first; volatile state, retrieved memory, skill context, and task runtime data
follow it. Provider adapters may translate this boundary into their native cache
mechanism. The shared core remains provider neutral and does not emit
provider-specific cache directives.

Changing the stable prefix changes its fingerprint. Dynamic state must not be
moved into the prefix merely to improve nominal cache reuse.

Autonomous fallback reuses the already assembled prompt instead of rebuilding
or reordering the stable prefix. Manual clients should likewise reuse the
envelope prompt verbatim when moving to an approved fallback. A fingerprint or
model switch is not a cache hit; only provider cache counters prove one.

## Startup Token Budget

At prompt start, include only context required for the current governed action:

1. one stable agent contract;
2. a compact current-state projection;
3. at most three deduplicated events retrieved using phase, target area, and
   project goal;
4. only the authorized skills relevant to the action; and
5. the current handoff/task runtime context.

Exclude generic reconciled phase-transition noise. Prefer references or tool
discovery over eagerly injecting full skill, command, tool, or historical
catalogs. Do not repeatedly call `mas prompt` only to inspect the same prompt;
each preview is observable telemetry even though it is non-billable.

## Accuracy-Preserving Savings

Token reduction is accepted only when required facts and governance behavior are
retained. For a changed prompt policy:

1. define required facts and decisions for representative tasks;
2. compare the baseline and optimized prompt on task success, acceptance
   evidence, lifecycle compliance, and critical-fact recall;
3. test cache-hit and cache-miss paths;
4. keep deterministic state, decisions, and artifacts in canonical storage; and
5. fail back to richer retrieval when relevance or confidence is insufficient.

Do not use lossy generated summaries as the only copy of canonical history. Do
not truncate blindly, hide missing context, or route to a cheaper model without
an accuracy threshold and fallback. Model routing is an adapter/execution-policy
decision; it must not create a second governance router.

## Operational Checks

```bash
uv run mas tokens <project-id>
uv run mas log-tokens <project-id> <agent-id> \
  --prompt <n> --completion <n> \
  --cached-input <n> --cache-write <n> --billable-input <n>
uv run mas doctor <project-id>
```

Use prompt metadata for component regressions, provider telemetry for actual
cache economics, and evaluation evidence for accuracy retention.
