# MAS Agent Utility Reference

Shared CLI commands used across agents. All run from the repo root (where `pyproject.toml` lives).

All engine modules live under `mas/core/engine/`. Top-level `mas/core/` contains the CLI
(`cli.py`), database layer (`db.py`), and configuration (`config.py`).

## Path Rule (REQUIRED)

Always create files with the **Write tool using absolute paths** — never bash heredocs or
`cat <<EOF` patterns, which inherit a shell path context and may silently write to the wrong
location. Build the absolute path from the repo root, e.g.
`<repo-root>/mas/projects/{project_id}/...` (on Windows, use the platform's absolute form,
e.g. `C:\path\to\claude-config\mas\projects\{project_id}\...`).

## Handoff Commands
```bash
# Accept a pending handoff
uv run python mas/core/engine/handoff_engine.py accept --handoff-id {handoff_id} --project-id {project_id}

# Create a handoff (delegate or return work)
uv run python mas/core/engine/handoff_engine.py create --project-id {project_id} --from {agent_id} --to {target_agent} --phase {phase} --task "{task}" --summary "{summary}"

# List pending handoffs (optionally filter by recipient)
uv run python mas/core/engine/handoff_engine.py pending --project-id {project_id} [--to-agent {agent_id}]

# Show a specific handoff
uv run python mas/core/engine/handoff_engine.py show --handoff-id {handoff_id} --project-id {project_id}

# Reject a handoff
uv run python mas/core/engine/handoff_engine.py reject --handoff-id {handoff_id} --project-id {project_id} --reason "{reason}"
```

## Shared State Commands
```bash
# Read a field
uv run python mas/core/engine/shared_state_manager.py read --project-id {project_id} --path {dotted.path}

# Write a scalar field
uv run python mas/core/engine/shared_state_manager.py write --project-id {project_id} --section {section} --field {field} --value "{value}" --agent {agent_id}

# Write a complex field (JSON)
uv run python mas/core/engine/shared_state_manager.py write --project-id {project_id} --section {section} --field {field} --value-json '{json}' --agent {agent_id}

# Append to a list field
uv run python mas/core/engine/shared_state_manager.py append --project-id {project_id} --section {section} --field {field} --value-json '{json}' --agent {agent_id}

# Show full state
uv run python mas/core/engine/shared_state_manager.py show --project-id {project_id}

# Snapshot at phase boundary (Master only)
uv run python mas/core/engine/shared_state_manager.py snapshot --project-id {project_id} --phase {phase}

# Approve a field as immutable (Master only)
uv run python mas/core/engine/shared_state_manager.py approve --project-id {project_id} --section {section} --field {field} --agent master_orchestrator
```

## Intake Commands (Inquirer only)
```bash
uv run python mas/core/engine/intake_checker.py analyze --spec-json '{spec_json}'
uv run python mas/core/engine/intake_checker.py questions --spec-json '{spec_json}' --round {n} --max 7
uv run python mas/core/engine/intake_checker.py record-qa --project-id {project_id} --round {n} --qa-json '[...]'
uv run python mas/core/engine/intake_checker.py write-spec --project-id {project_id} --spec-json '{spec_json}'
```

## Task Board Commands (Project Manager only)
```bash
uv run python mas/core/engine/task_board.py create-milestone --project-id {project_id} --milestone-json '{json}'
uv run python mas/core/engine/task_board.py create-task --project-id {project_id} --task-json '{json}'
uv run python mas/core/engine/task_board.py update-status --project-id {project_id} --task-id {id} --status {status}
uv run python mas/core/engine/task_board.py list --project-id {project_id} [--status {status}] [--milestone {ms_id}]
uv run python mas/core/engine/task_board.py blocked --project-id {project_id}
uv run python mas/core/engine/task_board.py milestone-status --project-id {project_id} --milestone-id {ms_id}
uv run python mas/core/engine/task_board.py progress-report --project-id {project_id} [--milestone-id {ms_id}]
uv run python mas/core/engine/task_board.py deps --project-id {project_id} --task-id {id}
uv run python mas/core/engine/task_board.py plan --project-id {project_id} --product-plan-path "{path}"
```

## Capability Registry Commands (HR / Evaluator / Spawner)
```bash
uv run python mas/core/engine/capability_registry.py search --tags "tag1,tag2" [--min-score 50]
uv run python mas/core/engine/capability_registry.py gap-cert --project-id {project_id} --requested-by {agent} --need "..." --tags "..." --save
uv run python mas/core/engine/capability_registry.py register --entry-json '{json}' --authorized-by master_orchestrator
uv run python mas/core/engine/capability_registry.py retire --agent-id {agent_id} --reason "..." --authorized-by master_orchestrator
uv run python mas/core/engine/capability_registry.py show --agent-id {agent_id}
```

## Metrics Commands (Evaluator only)
```bash
uv run python mas/core/engine/metrics_engine.py score-project --project-id {project_id}
uv run python mas/core/engine/metrics_engine.py score-agent --project-id {project_id} --agent-id {agent_id}
uv run python mas/core/engine/metrics_engine.py report --project-id {project_id} --agents "a1,a2" [--save]
```

## Training Commands (Trainer only)
```bash
uv run python mas/core/engine/training_engine.py analyze --project-id {project_id}
uv run python mas/core/engine/training_engine.py backlog [--status pending]
uv run python mas/core/engine/training_engine.py approve --proposal-id {id} --authorized-by master_orchestrator
uv run python mas/core/engine/training_engine.py reject --proposal-id {id} --reason "..." --authorized-by master_orchestrator
```

## Spawn Commands (Spawner only)
```bash
uv run python mas/core/engine/spawn_policy.py validate --project-id {project_id} --request-file {path} --cert-file {path}
uv run python mas/core/engine/spawn_policy.py history --project-id {project_id}
```

## Consultation Commands (Master only)
```bash
uv run python mas/core/engine/consultation_engine.py create --project-id {project_id} --question "{question}" --decision-type {type}
uv run python mas/core/engine/consultation_engine.py show --project-id {project_id} --request-id {rid}
uv run python mas/core/engine/consultation_engine.py check-risk --project-id {project_id} --request-id {rid}
```

## Wire Protocol (debugging)
```bash
uv run python -m core.wire_protocol encode '{payload_json}'
uv run python -m core.wire_protocol decode '{wire_json}'
uv run python -m core.wire_protocol validate '{wire_json}'
uv run python -m core.wire_protocol codes
```
