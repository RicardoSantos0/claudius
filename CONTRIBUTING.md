# Contributing to claudius

Thanks for your interest in contributing! `claudius` is a governed multi-agent
delivery framework for Claude Code. This guide covers how to set up, what
standards apply, and how to get a change merged.

## Development setup

```bash
# 1. Clone
git clone https://github.com/<owner>/claudius.git
cd claudius

# 2. Create + activate a virtual environment
uv venv --python 3.12 .venv
#   Windows (PowerShell):  .venv\Scripts\activate
#   macOS/Linux:           source .venv/bin/activate

# 3. Install (editable) with dev dependencies
uv pip install -e .
uv pip install pytest pytest-cov

# 4. Verify
mas doctor
pytest mas/tests/ -q
```

> **Note (cloud-synced filesystems):** on cloud- or network-synced folders,
> `uv` hardlinking can fail. Set `UV_LINK_MODE=copy` if you hit a hardlink error.

## Standards

This repo keeps its conventions in [`standards/`](standards/). Please read the
relevant ones before contributing:

- [`agent-frontmatter.md`](standards/agent-frontmatter.md) — required fields for `agents/*.md`
- [`commit-style.md`](standards/commit-style.md) — commit message format
- [`documentation-format.md`](standards/documentation-format.md) — doc + skill structure
- [`python-standards.md`](standards/python-standards.md) — Python conventions
- [`mas-governance.md`](standards/mas-governance.md) — governance model
- [`security-and-permissions.md`](standards/security-and-permissions.md) — tool grants + trust tiers

## Before you open a PR

Run the same checks CI runs:

```bash
python scripts/validate_agents.py     # agent frontmatter + registry coverage
python scripts/validate_skills.py     # skill SKILL.md validity + registry consistency
pytest mas/tests/ -q                  # full test suite (coverage gate enforced)
```

- **Agents:** every agent in `agents/` must appear in `mas/roster/registry_index.yaml`
  with the required frontmatter, and vice versa.
- **Skills:** every shipped skill needs a valid `SKILL.md`; no registry entry may
  point to a missing folder.
- **Tests:** add or update tests for behavior changes. Keep coverage at or above
  the configured threshold.

## Pull request expectations

1. Branch off `main`; keep PRs focused.
2. Describe **what** changed and **why**.
3. Ensure CI is green (validators + tests on supported Python versions).
4. Do not commit secrets, personal paths, or runtime state (`.env`, `mas/data/`,
   `mas/projects/`, logs). See [`SECURITY.md`](SECURITY.md).

## Reporting bugs / requesting features

Open a GitHub issue. For security issues, follow [`SECURITY.md`](SECURITY.md)
instead of opening a public issue.

## License

By contributing, you agree that your contributions are licensed under the
[BSD 3-Clause License](LICENSE).
