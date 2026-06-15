# Skills

Skills are reusable, model-invoked capabilities. In Claude Code you invoke one by
typing `/<skill-name>`; inside the MAS workflow the orchestrator also **recommends**
skills automatically based on the current project phase (the "trigger phases" below).

Each skill lives in its own folder with a `SKILL.md` (the instructions Claude loads
when the skill runs). Add new ones with [`skill-builder`](skill-builder/), and
validate the set with `python scripts/validate_skills.py`.

## MAS workflow skills

The core pack — a coherent set that maps onto the MAS project lifecycle.

| Skill | Trigger phases | Purpose |
|-------|----------------|---------|
| [`mas-clarify`](mas-clarify/) | intake, specification | Surface blocking questions, safe assumptions, and proposed defaults to unblock work. |
| [`mas-plan`](mas-plan/) | planning, capability-discovery | Produce or update a phase-aware MAS execution plan. |
| [`mas-review`](mas-review/) | intake, planning, execution, review, evaluation | Review project state, pending handoffs, stale context, risks, and next action. |
| [`mas-examine`](mas-examine/) | execution, review | Analyze code, docs, state, policies, or architecture **without modifying state**. |
| [`mas-document`](mas-document/) | phase-close, closed | Update checkpoints, decision logs, artifact indexes, and progress summaries. |
| [`mas-handoff`](mas-handoff/) | session-end, PR, blocked | Produce human-facing handoff summaries from MAS state. |
| [`mas-logwork`](mas-logwork/) | execution, review, closed | Record work sessions and elapsed effort into MAS event memory. |
| [`mas-postmortem`](mas-postmortem/) | evaluation, blocked, incident | Analyze failed phases, rejected handoffs, violations, or regressions. |

## General-purpose skills

Reusable beyond the MAS lifecycle.

| Skill | Purpose |
|-------|---------|
| [`skill-builder`](skill-builder/) | Create new skills, optimize existing ones, or audit skill quality against Claude Code best practices. |
| [`graphify`](graphify/) | Turn any folder (code, docs, papers) into a navigable knowledge graph with community detection and query/path/explain tools — fast navigation and grounded answers about architecture and file relationships. |
| [`frontend-design`](frontend-design/) | Create distinctive, production-grade frontend interfaces — web components, pages, or apps. |

## Authoring

See [`docs/authoring-skills.md`](../docs/authoring-skills.md) for the `SKILL.md`
contract (frontmatter, trigger, procedure, output format) and conventions every
skill must follow.
