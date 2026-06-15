# Authoring Agents

How to add or update a MAS agent without breaking the registry invariants that
`scripts/validate_agents.py` (and CI) enforce.

## 1. Create or edit the agent file

Agent definitions live in `agents/{name}.md` with YAML frontmatter:

```markdown
---
name: my-agent
description: "One-line role description. Apply when ..."
tools: Read, Grep, Glob, Bash, Edit
---

# My Agent

Body: responsibilities, invocation context, behaviour.
```

Frontmatter rules checked by the validator:

- **Required fields**: `name`, `description`, `tools`.
- **`tools`** must come from the approved set: `Read`, `Grep`, `Glob`, `Bash`,
  `Agent`, `Edit`, `WebFetch`, `WebSearch`, `Write`, `TodoWrite`, `TodoRead`.
  Tools may be a comma-separated string or a YAML list.
- `_utilities.md` is skipped by the validator (it is not an agent).

(`model` and other extra keys are allowed; they are not required by the validator.)

## 2. Register the agent

Every agent file must have an entry in **`mas/roster/registry_canonical.yaml`** —
this is the source of truth `validate_agents.py` checks. Add an entry keyed by the
agent id:

```yaml
agents:
  my_agent:
    file: agents/my_agent.md
    claude_name: my-agent        # must be lowercase-hyphenated
    trust_tier: T1
    status: active
    model: sonnet
    tools: [Read, Grep, Glob, Bash, Edit]
    domains: [some-domain]       # required, non-empty
    roles: [some-role]           # required, non-empty
    human_invocable: false
    can_spawn: false
    can_write_state: false
    can_transition_phase: false
    risk_level: low
```

Registry entry rules checked by the validator:

- The `file` path must exist on disk.
- `claude_name` must be present and lowercase-hyphenated (e.g. `master-orchestrator`).
- `trust_tier`, `status`, `domains`, and `roles` must all be present and non-empty.

`mas/roster/registry_index.yaml` carries the MAS-engine view (agent capabilities and
the skills registry). If you add a brand-new agent the engine should discover, add
the corresponding entry there too.

## 3. Sync the runtime DB index

After editing any `agents/*.md` or `mas/roster/registry_canonical.yaml`, refresh the
runtime DB index so capability discovery and prompt assembly see the change:

```bash
uv run python mas/tools/roster_sync.py            # apply
uv run python mas/tools/roster_sync.py --dry-run  # preview without writing
```

This upserts the registry into the `mas_agents` table of `mas/data/episodic.db`.
Skipping it can leave a stale agent set in runtime lookups.

## 4. Validate

```bash
python scripts/validate_agents.py
```

Exit code `0` means all agent files and registry entries pass. The same check runs
in CI on every push/PR to `master`, so run it locally before committing.
