# Wire Protocol Standard

**Type:** Normative
**Applies to:** All inter-agent handoff payloads and responses
**Source of truth:** `mas/foundation/wire_protocol_spec.yaml`

---

## Format

All agent-to-agent outputs use MAS wire protocol v1.0:

```json
{
  "_v": "1.0",
  "s": "task:complete",
  "art": ["path/to/artifact.yaml"],
  "dec": [
    {
      "id": "d-001",
      "v": "decision value",
      "rat": "why this decision was made",
      "alt": ["alternative A", "alternative B"],
      "rel": "d-000"
    }
  ],
  "rsn": "Optional reasoning — max 100 words"
}
```

---

## Fields

Canonical transport keys are the compact forms defined in `mas/foundation/wire_protocol_spec.yaml`.
Expanded aliases are accepted for compatibility and mapped by runtime decoding.

| Field | Required | Description |
|-------|----------|-------------|
| `_v` | Yes | Protocol version — always `"1.0"` |
| `s` | Yes | Status code (see vocabulary below) |
| `art` | No | List of artifact paths on disk |
| `dec` | No | List of decisions made |
| `rsn` | No | Optional reasoning — max 100 words |
| `skill_request` / `sk_req` | No | Skill request object; `sk_req` is canonical transport key and `skill_request` is accepted alias |
| `skill_used` / `sk_used` | No | Skill usage list; `sk_used` is canonical transport key and `skill_used` is accepted alias |

Omit empty lists and null values.

---

## Status Code Vocabulary

| Code | Meaning |
|------|---------|
| `task:complete` | Task finished successfully |
| `task:delegated` | Task delegated to another agent |
| `task:blocked` | Task cannot proceed; escalation needed |
| `eval:pass` | Evaluation passed |
| `eval:fail` | Evaluation failed |
| `consult:approve` | Consultant approves |
| `consult:flag` | Consultant flags a risk |
| `scribe:recorded` | Scribe recorded the artifact/phase |
| `hr:plan_ready` | HR deployment plan is ready |
| `spawn:approved` | Spawn approved |
| `spawn:denied` | Spawn denied |

---

## Decision Quality Fields

To score above 70 on `decision_quality`, each `dec` entry should include:

| Field | Description | Scoring impact |
|-------|-------------|----------------|
| `id` | Decision identifier | Required |
| `v` | Decision value / outcome | Required |
| `rat` | Rationale — why this decision was made | +20 pts |
| `alt` | Alternatives considered — list of strings | +20 pts |
| `rel` | Related decision id or context | +20 pts |

---

## Orchestration Extension Keys

Used when `mas run` drives the project loop:

```json
{
  "_v": "1.0",
  "s": "task:complete",
  "next_action": "delegate",
  "next_agent": "inquirer_agent",
  "skill_request": {
    "name": "mas-examine",
    "query": "Review runtime diff before delegation",
    "required": true
  },
  "skill_used": [
    {
      "name": "mas-review",
      "purpose": "state review before resume",
      "output": "mas/projects/proj-.../logs/mas-review-20260502.md"
    }
  ],
  "consultation_trigger": {
    "decision_type": "architecture",
    "question": "Should we spawn a specialist agent for X?",
    "consultants": ["domain_expert", "risk_advisor"],
    "context": {"gap": "no agent covers X"}
  }
}
```

| Key | Values | Meaning |
|-----|--------|---------|
| `next_action` | `delegate`, `advance_phase`, `consult`, `escalate`, `wait` | What the loop should do next |
| `next_agent` | agent_id | Who to delegate to (when `next_action == "delegate"`) |
| `next_agents` | [agent_id, ...] | Parallel dispatch (when HR marks `parallel: true`) |
| `skill_request` / `sk_req` | object | Skill request; use `name`, `query`, and optional `required`; transport should prefer `sk_req` |
| `skill_used` / `sk_used` | list | Skill usage records; string entries are accepted but object entries are preferred; transport should prefer `sk_used` |
