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

The same rule applies to memory and instructions: all surfaces consume the
canonical `AGENTS.md` files and governed MAS stores. Provider-local memory may
assist recall, but it is never authoritative. `mas close` synchronizes a shared
`PROJECT_SUMMARY.md` into the SQL event ledger; it does not write provider
private formats as a second MAS data path.

## Skills reach every surface through links

`skills/` holds the only copy of every skill, so each surface gets a pointer to
it and never a copy of its own: a copy drifts from the canonical skill and then
shadows it. `scripts/link_skill_surfaces.py` writes the pointers, `setup.ps1` and
`setup.sh` run it, and `--check` reports drift without writing.

| Surface | Pointer | Where the installed client looks |
|---|---|---|
| Claude Code | one link per skill in `~/.claude/skills/` | `~/.claude/skills` |
| opencode | none needed | reads `~/.claude/skills` |
| VS Code Copilot | none needed | reads `~/.claude/skills` and `~/.copilot/skills`, keeping the first skill of each name |
| Copilot CLI | one link per skill in `~/.copilot/skills/` | `~/.copilot/skills` and `~/.agents/skills`, not `~/.claude/skills` |
| Codex | one link per skill in `~/.codex/skills/` | `$CODEX_HOME/skills` |
| Antigravity | an absolute path to `skills/` in `~/.gemini/config/skills.json` | the manifest, which rejects a `~/` path |

Map a new surface from the installed client rather than its documentation, then
confirm what the model actually sees. Clients drop a skill without a warning
when its frontmatter breaks their parser, so `scripts/validate_skills.py`
refuses a `name`, `description` or `argument-hint` that is not a string.

A runtime with no skill loader of its own gets the text from MAS instead. An API
run through `agent_runner` receives the text of each REQUIRED skill its agent is
authorized for, bounded per skill and in total, and on the main dispatch the
agent can ask for any other authorized skill with
`"skill_request": {"name": "<skill>", "query": "<why>"}` in its wire block; the
text arrives on its next step. An MCP client without a loader calls `mas_skill`,
which lists an agent's authorized skills or, given a skill and an exact project
id, returns the skill's text and folder after authorization and an audit.

## Supported surfaces

| Surface | Expected path | Compatibility rule |
|---|---|---|
| CLI | Direct `mas` commands | Canonical operator and scripting interface |
| MCP clients | `mas-server` | Preferred tool-native transport |
| Claude Code | MCP, installed agents and commands, per-skill links, or manual loop | Apply the envelope model at invocation; planning is Opus 5.5 first with an approved Fable 5.1 backup; return a receipt |
| Codex | `mas-governance` plugin over `mas-server`, skills through links | Apply OpenAI model/reasoning hints and report the actual model |
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
