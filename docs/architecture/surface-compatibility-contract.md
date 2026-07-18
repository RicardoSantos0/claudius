# Surface Compatibility Contract

MAS is provider agnostic. A client surface may help a human or model interact with
MAS, but it must not own a separate governance workflow.

## Core invariant

New surfaces use one of two paths:

- MCP: model-aware clients call `mas_prompt_envelope`; prompt-only clients may keep
  using `mas_prompt`. Both continue through the same ingest/state/governance tools.
- Manual loop: `mas prompt` selects and records a route, the chosen surface produces
  text, and `mas ingest` returns it to the governed workflow.

No surface should add a governance fork to `mas/core`.

| Surface | Expected path | Compatibility rule |
|---|---|---|
| CLI | Direct `mas` commands | Canonical operator and scripting interface |
| MCP clients | `mas-server` | Preferred tool-native transport |
| Claude Code | MCP, installed agents/skills/commands, or manual loop | One client surface, not the MAS identity |
| Codex | `mas-governance` plugin over `mas-server` | Consume OpenAI model/reasoning hints from the prompt envelope |
| OpenCode | MCP or manual loop | Consume inherited catalog and `-m provider/model` hint |
| Copilot / ChatGPT | MCP where available or manual loop | No surface-specific governance logic |
| Gemini | LiteLLM/API adapter or manual loop | No Gemini-specific phase logic |
| Ollama / LM Studio | OpenAI-compatible endpoint or LiteLLM | Same governed text loop |
| Package mode | `claudius` wheel and `$MAS_HOME` | Runtime state remains outside package source |

Provider adapters may handle transport, model names, retries, availability, and token
metadata. They may not change phase gates, handoffs, access control, evaluation policy,
or task-board rules.

A new surface is compatible when it can read project status, request the next governed
prompt, return model text through ingest, and preserve shared-state, handoff, task-board,
token-accounting, model-routing, and evaluation behavior without a surface-specific
engine fork.
