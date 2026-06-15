# Documentation Format Standard

**Type:** Advisory
**Applies to:** Project docs, checkpoints, session notes, handoff summaries

---

## Recommended Sections

For any project document, include these sections in order (omit sections that don't apply):

```text
Purpose / Context
Current status
Completed work
Decisions (with rationale)
Open questions
Risks
Validation / test results
Next actions
References / artifacts
```

---

## Section Guidelines

### Purpose / Context
One paragraph. State what this document is and why it was created.

### Current Status
Two to four sentences. What is the current state right now? Who owns it? What phase is it in?

### Completed Work
Bulleted list. Each item should reference a file path, handoff ID, or other concrete artifact.

```markdown
- Created `scripts/export_source.sh` — verified with git archive test
- Accepted handoff `ho-proj-001-005` from inquirer_agent
- Updated `mas/roster/registry_index.yaml` — 16 active agents listed
```

### Decisions
Each decision must include: what was decided, why, and what was not chosen.

```markdown
- **Decision:** Use full MAS workflow (not lite)
  - Rationale: >5 file changes, new architecture
  - Alternatives: lite workflow — rejected (scope too broad)
```

### Open Questions
Explicitly list questions that are unresolved. Never silently drop them.

```markdown
- [ ] Should validate_agents.py also check skill SKILL.md files?
- [ ] Is a GitHub Actions runner available on the target machine?
```

### Risks
List identified risks with their severity and mitigation plan.

```markdown
- [medium] Frontmatter normalization may break agent resolution — mitigation: run tests before and after
```

### Validation
What was tested, what passed, what failed.

```markdown
- `python scripts/check_archive_clean.py` — PASS on clean archive, FAIL on .env archive ✓
- `scripts/validate_agents.py` — not yet run (Phase 3 not started)
```

### Next Actions
Numbered, concrete, actionable. Not "continue work" but "run validate_agents.py and fix any frontmatter gaps."

---

## YAML Documents

For YAML documents (shared_state, handoffs, evaluations), use:

```yaml
# Section comments to explain blocks
field_name: value  # inline comment for non-obvious values
```

Prefer `snake_case` for keys. Timestamps in ISO 8601 format: `2026-05-02T10:00:00Z`.

---

## Length Guidelines

| Document type | Target length |
|---------------|---------------|
| Session note (brief) | 100–300 words |
| Checkpoint (standard) | 500–1000 words |
| Phase summary | 300–600 words |
| Postmortem | 500–1500 words |
| Handoff | 200–500 words |
