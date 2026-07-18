# Phase-Aware Model Routing

MAS chooses a semantic execution profile before it chooses a vendor model. This
keeps lifecycle policy stable across Anthropic, OpenAI, Gemini through LiteLLM,
Codex, OpenCode, and future provider catalogs.

## Routing policy

| Lifecycle phase | Default profile | Intent |
|---|---|---|
| intake, specification, planning | `reasoning` | Ambiguous requirements and consequential decisions |
| capability discovery | `standard` | Matching and bounded synthesis |
| execution | `economy` | Efficient delivery for well-scoped work |
| review, evaluation | `reasoning` | Independent judgment and final gates |
| improvement, closure | `economy` | Bounded documentation and record work |

Route precedence is: authorized explicit override; risk, critical-agent, or retry
escalation; per-agent override; phase profile; legacy fallback. Provider/model
overrides are atomic pairs—a partial pair is rejected rather than combined with a
different catalog.

## Provider catalogs and lifecycle

Catalogs live under `llm.provider_catalogs` in `mas/system_config.yaml` and map
`reasoning`, `standard`, and `economy` to provider targets. Choose one with
`--catalog`, `MAS_MODEL_CATALOG`, or `default_provider_catalog`.

Anthropic is installed with the base package. Autonomous OpenAI routes require
`claudius[openai]`; Gemini and other LiteLLM routes require `claudius[litellm]`.
Install `claudius[providers]` for both. Manual/MCP envelopes can still recommend
these catalog targets without the SDK because the host surface performs the call.

Each catalog carries `source_url`, `validated_at`, `lifecycle_state`, and
`credential_env`. When lifecycle checks are enabled, MAS rejects inactive,
future-dated, stale, or unprovenanced catalogs. Runtime validation uses the current
date; a fixed date is supported only through injected test configuration.

```powershell
mas model-catalogs
mas model-canary --catalog openai          # preview; no provider call
mas model-canary --catalog openai --live   # explicit bounded call
```

Live canaries use a fixed prompt and require the provider to report the exact
requested model identity. Missing credentials skip cleanly. Missing or mismatched
identity fails closed. Credentials, prompt/response content, and arbitrary provider
payloads are never persisted.

## Manual, Codex, and OpenCode surfaces

`mas prompt` always selects and audits a route. Plain output includes a route
header; JSON and MCP envelopes carry structured routing metadata.

```powershell
mas prompt <project-id> --surface codex --json
mas prompt <project-id> --surface opencode --catalog gemini --json
```

Codex receives model and reasoning-effort hints. OpenCode receives its configured
`-m provider/model` arguments and inherits the selected catalog. A manual envelope
is marked `recommended` unless a launcher/client actually applies it; autonomous
`mas run` routes are `engine_enforced`.

Agent prompt frontmatter uses `model: inherit` and `model_profile: auto`. The
canonical registry leaves its legacy `model` override empty. Phase, risk policy,
catalog, and surface mapping therefore determine the actual provider/model.

## Audit and telemetry

`llm.routing.audit_persistence` controls route-audit durability:

- `best_effort` continues with a visible warning if persistence fails.
- `required` blocks before any provider execution if the audit cannot be stored.

Live calls write only allowlisted route metadata: catalog/profile/model, phase,
latency, retry/escalation, token counts, result, error type, and nullable
adapter-supplied cost/quality. SQLite and PostgreSQL expose equivalent aggregates.
An entirely unpriced or unscored result remains `null`; `priced_route_count` and
`quality_scored_count` expose measurement coverage.

```powershell
mas route-metrics
mas route-metrics --catalog openai --profile economy
```

MCP clients have equivalent `mas_model_catalogs`, `mas_model_canary`, and
`mas_route_metrics` tools.

## Source basis

- [Anthropic model overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Codex subagent model guidance](https://developers.openai.com/codex/subagents)
- [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models)
- [OpenCode model selection](https://opencode.ai/docs/models/)

Revalidate configured IDs against the official sources before changing defaults,
and evaluate quality on your own workload before production rollout.
