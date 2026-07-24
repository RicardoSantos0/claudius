# Agent Frontmatter Standard

**Type:** Normative
**Applies to:** All files in `agents/*.md`
**Validated by:** `scripts/validate_agents.py`

---

## Canonical Frontmatter Fields

All agent `.md` files must include a YAML frontmatter block at the top:

```yaml
---
name: <claude-facing-name>
description: <one-line description of what the agent does>
tools: Read, Grep, Glob, Bash, Agent
model: inherit
model_profile: auto
---
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Claude Code-facing agent name (lowercase hyphenated) |
| `description` | string | One-line description of the agent's purpose |
| `tools` | comma-separated | Tools the agent is authorized to use |
| `model` | string | Use `inherit`; explicit IDs are legacy compatibility overrides |
| `model_profile` | string | Use `auto` so phase, risk, retry, and surface policy choose the semantic tier |

### Naming Convention

| Concept | Format | Example |
|---------|--------|---------|
| Claude Code agent name | lowercase hyphenated | `master-orchestrator` |
| MAS internal ID | lowercase snake case | `master_orchestrator` |
| File name | snake case `.md` | `master_orchestrator.md` |
| Registry key | MAS internal ID | `master_orchestrator` |

Claude Code uses the `name` field in frontmatter. MAS internals use the snake_case ID. Both must be mapped in `mas/roster/registry_index.yaml`.

### Approved Tool Names

```text
Read      Grep      Glob
Bash      Agent     Edit
WebFetch  WebSearch
```

Use only these exact names. Do not invent tool names.

### Model Routing

```text
model: inherit
model_profile: auto
```

Do not pin vendor model names in agent prompts. Model assignments are governed by
the phase-aware profiles and provider catalogs in `mas/system_config.yaml`. An
explicit model is reserved for a documented compatibility exception.

---

## MAS-Only Metadata

The following fields belong in `mas/roster/registry_index.yaml`, not in agent frontmatter:

```yaml
trust_tier:           # T0, T1, T2, T3
performance_score:
status:               # active, inactive, deprecated, experimental
can_spawn:
can_write_state:
can_transition_phase:
human_invocable:
risk_level:
domains:
roles:
owner:
```

Keeping MAS metadata out of frontmatter prevents prompt bloat and allows registry updates without modifying agent prompts.

---

## Example

```yaml
---
name: master-orchestrator
description: Coordinates MAS phases, governance, delegation, handoffs, consultation, and escalation.
tools: Read, Grep, Glob, Bash, Agent
model: inherit
model_profile: auto
---
```

Registry entry:

```yaml
master_orchestrator:
  file: agents/master_orchestrator.md
  claude_name: master-orchestrator
  trust_tier: T0
  status: active
  model: ""
  model_profile: auto
  tools: [Read, Grep, Glob, Bash, Agent]
  domains: [orchestration, governance, planning]
  roles: [coordinator, phase-manager]
  human_invocable: true
  can_spawn: true
  can_write_state: true
  can_transition_phase: true
  risk_level: critical
```
