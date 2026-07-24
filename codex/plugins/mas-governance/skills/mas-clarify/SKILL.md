---
name: mas-clarify
description: Identify and prioritize blocking questions, safe assumptions, and proposed defaults to unblock MAS project work.
---

# MAS Clarify

Use this skill to surface and resolve ambiguity before or during MAS project execution.

## Trigger

Invoke when:
- A project brief is ambiguous and work cannot safely begin
- An agent has flagged an unresolved question that blocks a handoff
- A scope change has introduced new ambiguity
- Acceptance criteria are unclear or untestable

## Inputs

- `project_id` — required
- `context` — what is currently ambiguous or blocked
- `source` — `brief` | `state` | `handoff` | `user`

## Reads

```text
mas/projects/<project_id>/shared_state.yaml
mas/projects/<project_id>/decisions/open_questions.yaml
mas/projects/<project_id>/intake/clarified_spec.yaml (if present)
```

## Procedure

1. Read current open questions from `decisions/open_questions.yaml` and project state.
2. Identify new ambiguities from the provided context.
3. Categorize each question:
   - **Blocking** — must be answered before execution can proceed
   - **Non-blocking** — should be answered before review
   - **Deferrable** — can be decided later without risk
4. Identify safe assumptions the agent can make without user input.
5. Propose defaults for deferrable questions.
6. Present blocking questions to the user.
7. When answers are provided, produce explicit proposed updates to `shared_state.yaml decisions` and `open_questions.yaml` for the owning agent to apply.

## Output Format

```markdown
# Clarification Needed

## Blocking Questions
1. <question — must be answered before work can proceed>
2.

## Non-Blocking Questions
1. <question — should be answered before review>
2.

## Assumptions I Can Safely Make
1. <assumption — with rationale>
2.

## Proposed Defaults
1. <default value — with rationale>
2.
```

## Rules

- Prioritize blockers — do not ask endless questions.
- Maximum 7 questions per round; priority: blocking → non-blocking → deferrable.
- Never fabricate answers the user has not provided.
- Return proposed state/file updates; the owning agent performs the actual writes.
- Reference `standards/mas-governance.md` for decision recording requirements.
- If a user refuses to answer a required field after 2 rounds, escalate to master_orchestrator.
