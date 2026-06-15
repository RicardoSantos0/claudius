# File Naming Conventions (MAS Repository)

Catalogues the conventions enforced (advisory) by `mas/core/tools/naming_convention_check.py`.

## Why Conventions Matter Here

Past sprints produced quality findings because planning-phase artifact paths (e.g. `docs/providers/opencode.md`) did not match the sibling-directory convention (`codex_cli.md`, `claude_code_cli.md` → `opencode_cli.md` expected). The tool catches such mismatches before they reach evaluation.

## The Rule: Dominant Sibling Suffix

When proposing a new file path, the tool inspects the parent directory and looks at the trailing underscore-separated token of every sibling file with the same extension.

- If a token appears as the trailing token in **≥ 2 siblings**, it becomes the *dominant suffix* for that directory.
- A proposed file whose name does not end in `_{dominant}.{ext}` is flagged with a suggested aligned name.

The check is **advisory only** — it does not fail builds, but it does exit with code 1 so it can be wired into checklists.

## Examples

| Directory | Siblings | Proposed | Tool verdict |
|---|---|---|---|
| `docs/providers/` | `codex_cli.md`, `claude_code_cli.md` | `opencode.md` | MISMATCH → `opencode_cli.md` |
| `docs/providers/` | `codex_cli.md`, `claude_code_cli.md` | `opencode_cli.md` | OK |
| `docs/architecture/` | `overview.md`, `index.md` | `runtime.md` | OK (no dominant suffix) |
| `mas/tests/unit/` | `test_handoff_engine.py`, `test_scribe.py` | `test_new_thing.py` | OK (matches prefix-via-suffix `test`) |

## When the Tool Cannot Help

- Parent directory does not exist yet (the first file in a new dir).
- No siblings share the same extension.
- Siblings have no token appearing twice.

In all three cases the tool returns `ok: True`.

## Convention Codification (current)

| Directory glob | Convention |
|---|---|
| `docs/providers/*.md` | `<provider>_cli.md` (e.g. `codex_cli.md`) |
| `mas/tests/unit/test_*.py` | `test_<module>.py` |
| `docs/governance/*.md` | one-noun-per-file (no dominant suffix) |

This table grows as conventions emerge and get documented here.

## Reference

- Tool: [`mas/core/tools/naming_convention_check.py`](../../mas/core/tools/naming_convention_check.py).
- Developer guide: [`docs/developer/tooling.md`](../developer/tooling.md).
- Proposal: prop-TP-044 / proj-YYYYMMDD-NNN-ml-autograder-improvements.
