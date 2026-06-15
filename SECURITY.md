# Security Policy

## Supported versions

`claudius` is pre-1.0 software. Security fixes are applied to the latest released
minor version only.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on this repository. This opens a private
advisory visible only to the maintainers.

Include, where possible:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version(s) and environment.

You can expect an initial acknowledgement within a few days. There is no bug
bounty program.

## What `claudius` stores locally

`claudius` is a local, file-based framework. Understanding where it keeps state
helps you avoid committing sensitive data:

- **Project state** lives under `mas/projects/<project-id>/` (shared state YAML,
  decision logs, handoffs, artifacts). This is per-project working data.
- **Event store / metrics** live in a local SQLite database under `mas/data/`.
- **Audit log** is written to `mas/audit.log`.
- **Configuration** is read from `.env` (never commit this) and
  `mas/system_config.yaml`.

All of the above (except `system_config.yaml`) are runtime/local state and are
excluded from version control by `.gitignore` and from source archives by
`.gitattributes` (`export-ignore`).

## Secret handling

- Put your `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). **Never commit
  `.env`.**
- Do not paste API keys, tokens, or credentials into agent prompts, project
  briefs, or committed files.
- If a secret is ever committed, **rotate it immediately at the source** — git
  history makes deletion alone insufficient.

## Permissions model

`claudius` runs agents through Claude Code with explicit allowed-tool grants per
agent and a trust-tier model. Review `standards/security-and-permissions.md` and
each agent's frontmatter before granting broader permissions.
