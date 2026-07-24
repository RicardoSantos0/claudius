# Surface Compatibility Contract

MAS is provider agnostic. A client surface may help a human or model interact with
MAS, but it must not own a separate governance workflow.

## Core invariant

New surfaces use one of two paths:

- MCP: model-aware clients must call `mas_prompt_envelope`; prompt-only clients
  may keep using `mas_prompt` only when dispatch verification is not required.
  A raw prompt contains no model-selection receipt contract. Both continue
  through the same ingest/state/governance tools.
- Manual loop: `mas prompt` selects and records an ordered route, the chosen
  surface produces text, and `mas ingest` returns it with the dispatch receipt
  fields. Prompt previews are non-billable estimates; ingested responses are
  observed manual turns with heuristic token counting. Exact provider/cache
  counts use `mas log-tokens`.

No surface should add a governance fork to `mas/core`.

| Surface | Expected path | Compatibility rule |
|---|---|---|
| CLI | Direct `mas` commands | Canonical operator and scripting interface |
| MCP clients | `mas-server` | Preferred tool-native transport |
| Claude Code | MCP, installed agents/skills/commands, or manual loop | Apply the envelope model at invocation; planning is Fable-first with approved Opus fallback; return a receipt |
| Codex | `mas-governance` plugin over `mas-server` | Apply OpenAI model/reasoning hints and report the actual model |
| OpenCode | MCP or manual loop | Apply inherited catalog and `-m provider/model`, then return the route |
| Copilot / ChatGPT | MCP where available or manual loop | Advisory unless the host can select/report a model; never claim enforcement without evidence |
| Local model host | MCP or manual loop | Supply an explicit provider/model pair or local catalog; the local surface fails closed instead of inheriting a cloud route |
| Gemini | LiteLLM/API adapter or manual loop | No Gemini-specific phase logic |
| Ollama / LM Studio | OpenAI-compatible endpoint or LiteLLM | Same governed text loop |
| Package mode | `claudius` wheel and `$MAS_HOME` | Runtime state remains outside package source |

Provider adapters may handle transport, model names, retries, availability, and token
metadata. They may not change phase gates, handoffs, access control, evaluation policy,
or task-board rules.

Prompt envelopes expose provider-neutral component estimates plus a stable-prefix
fingerprint. Adapters may translate this boundary into provider-specific prompt
caching, but the core never claims a cache hit or saving without provider-reported
cached-input and billable-input metadata. See
[Prompt Token and Caching Contract](prompt-token-contract.md).

A new surface is compatible when it can read project status, request the next
governed prompt, return model text through ingest, return a matching receipt
when required, and preserve shared-state, handoff, task-board, token-accounting,
model-routing, and evaluation behavior without a surface-specific engine fork.
