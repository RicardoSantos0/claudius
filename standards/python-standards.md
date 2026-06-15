# Python Standards

**Type:** Advisory
**Applies to:** All Python code in `mas/` and `scripts/`

---

## Package Layout

```text
mas/
  core/
    engine/         # Core MAS engine modules
    config.py       # Config loader
    agent_runner.py # Agent execution
  foundation/       # Schemas, wire protocol
  policies/         # Policy YAML files
  roster/           # Agent registry
  templates/        # Handoff/spec templates
scripts/            # Standalone utility scripts
```

---

## Import Style

- Standard library imports first
- Third-party imports second
- Local imports last
- Separate each group with a blank line
- No wildcard imports (`from x import *`)

---

## Type Hints

- Use type hints on all public functions
- Use `from __future__ import annotations` for forward references
- Prefer `list[str]` over `List[str]` (Python 3.10+)
- Use `Optional[X]` or `X | None` — be explicit

---

## Error Handling

- Raise specific exceptions, not bare `Exception`
- CLI scripts: exit with code 0 (success), 1 (failure), 2 (usage error)
- Log errors to `stderr`, output to `stdout`
- Do not swallow exceptions silently

---

## Logging

- Use `logging` module, not `print()`, in library code
- CLI scripts may use `print()` for user-facing output
- Use `logger = logging.getLogger(__name__)` at module level
- Log levels: `DEBUG` for verbose tracing, `INFO` for normal flow, `WARNING` for recoverable issues, `ERROR` for failures

---

## Test Expectations

- Tests live in `mas/tests/`
- Use `pytest`
- Test names: `test_<function>_<scenario>` (e.g., `test_load_config_missing_file`)
- Tests should not read `.env`, `mas/projects/`, or `secrets/`
- Prefer deterministic tests; mock external I/O
- Target: every public function in `mas/core/` has at least one test

---

## CLI Behavior

- Accept `--help` on all CLI commands
- Print usage on error with exit code 2
- Non-zero exit on failure; 0 on success
- Produce human-readable output on success: `OK <details>` or `ERROR: <message>`
- Accept `--project-id` and `--agent` parameters consistently across MAS CLI commands

---

## Security

- Never read `.env`, `secrets/`, `mas/projects/`, or browser state from scripts
- Never hard-code credentials or API keys
- Use `os.environ.get(key)` with explicit fallbacks — never `os.environ[key]` without a try/except
- Do not log secret values even in debug mode

---

## Determinism

- Avoid `random` in tests unless seeded
- Avoid `datetime.now()` in tests — inject timestamps or mock
- File paths: use `pathlib.Path` for cross-platform compatibility
