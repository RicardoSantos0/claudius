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

`product_manager_agent` and `project_manager_agent` always receive `reasoning`,
including planning corrections dispatched during execution. The selected
surface/catalog still determines the concrete provider.

## Ordered candidates and fallback

Each route records a unique `dispatch_id`, ordered approved candidates, selected
index, reason, and receipt requirement. The Anthropic reasoning chain is:

1. `anthropic/claude-fable-5`;
2. `anthropic/claude-opus-4-8`, only after `model_unavailable` or `refusal`.

Constrain a client or subscription plan before launch:

```powershell
mas prompt <project-id> product_manager_agent --surface claude `
  --exclude-model claude-fable-5 --json
mas prompt <project-id> project_manager_agent --surface claude `
  --available-model claude-opus-4-8 --json
```

`MAS_AVAILABLE_MODELS` and `MAS_EXCLUDED_MODELS` provide comma-separated
environment equivalents. MAS fails closed when no candidate remains.
Autonomous dispatch does not cross models for rate limits, transient faults,
authorization failures, or unclassified errors.

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

## Manual client surfaces

`mas prompt` always selects and audits a route. Plain output includes a route
header; JSON and MCP envelopes carry structured routing metadata.

```powershell
mas prompt <project-id> product_manager_agent --surface claude --json
mas prompt <project-id> --surface copilot --json
mas prompt <project-id> --surface codex --json
mas prompt <project-id> --surface opencode --catalog gemini --json
mas prompt <project-id> --surface local --provider openai --model my-local-model --json
```

Claude uses the Anthropic catalog. Codex uses its OpenAI catalog and
reasoning-effort hint. OpenCode inherits the catalog and receives
`-m provider/model`. GitHub Copilot remains advisory unless its active host can
select and report a model. Local clients must supply an explicit compatible
provider/model pair or catalog; MAS fails closed instead of inheriting a
default cloud route.

Manual accuracy has four distinct claims:

| Claim | Evidence |
|---|---|
| Selection | MAS chose an approved route. |
| Client application | The host was instructed/configured to use it. |
| Receipt | The host/operator/provider reported what ran. |
| Verification | Provider-reported identity matched an approved candidate. |

`client_selectable` is not enforcement. Client/operator reports are
attestations. Reasoning-profile manual dispatches require a matching receipt
before `advance_phase` or `delegate`:

```powershell
mas ingest <project-id> --agent product_manager_agent `
  --dispatch-id <id> --reported-provider anthropic `
  --reported-model claude-fable-5 --verification-source client < response.txt
```

MCP clients pass the same fields to `mas_ingest`. Missing, mismatched, or
replayed required receipts block state change. Legacy projects without a
persisted selection remain compatible. Autonomous `mas run` is
`engine_enforced` and checks provider-reported identity.

Claude Code custom-agent invocation should pass the envelope model explicitly.
The `CLAUDE_CODE_SUBAGENT_MODEL` environment setting has higher precedence than
per-invocation and frontmatter settings, so the receipt remains necessary.

Agent prompt frontmatter uses `model: inherit` and `model_profile: auto`. The
canonical registry leaves its legacy `model` override empty. Phase, risk policy,
catalog, and surface mapping therefore determine the actual provider/model.

## Audit and telemetry

`llm.routing.audit_persistence` controls route-audit durability:

- `best_effort` continues with a visible warning if persistence fails.
- `required` blocks before any provider execution if the audit cannot be stored.

Receipt-required routes always require the selection audit to persist,
regardless of the general setting.

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
- [Anthropic model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
- [Anthropic refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)
- [Claude Code subagents and model precedence](https://code.claude.com/docs/en/sub-agents)
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)
- [OpenAI model catalog](https://developers.openai.com/api/docs/models)
- [Codex subagent model guidance](https://developers.openai.com/codex/subagents)
- [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models)
- [OpenCode model selection](https://opencode.ai/docs/models/)

Revalidate configured IDs against the official sources before changing defaults,
and evaluate quality on your own workload before production rollout.
