# Security Policy

## Supported versions

`claudius` is pre-1.0 software. Security fixes are applied to the latest released
minor version only.

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

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

- Put the API key for whichever provider you select — for example
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or the credential your LiteLLM route
  requires — in `.env` (see `.env.example`). **Never commit `.env`.**
- The manual prompt/ingest and MCP workflows make no provider API calls and need
  no credentials at all. Keys are only required for autonomous `mas run` and
  opt-in model canaries.
- Do not paste API keys, tokens, or credentials into agent prompts, project
  briefs, or committed files.
- If a secret is ever committed, **rotate it immediately at the source** — git
  history makes deletion alone insufficient.

## Execution and permissions model

`claudius` is provider-neutral: it assembles governed prompts and records
governed state, and the model call happens either inside the host agent surface
(Claude Code, Codex, OpenCode, or another MCP client), in a manual paste loop, or
— for autonomous `mas run` — through the provider adapter you configure. The
security implications differ by surface:

- **Host-surface and MCP execution.** Tool permissions are enforced by the host
  agent surface, using each agent's declared tool grants plus the trust-tier
  model. Review `standards/security-and-permissions.md` and each agent's
  frontmatter before granting broader permissions.
- **Manual paste loop.** No tools are executed by `claudius`; you decide what to
  run from a model's reply. Treat model output as untrusted input.
- **Autonomous `mas run`.** Prompts and responses leave your machine for the
  selected provider. Choose the catalog accordingly, and keep project briefs free
  of data you are not willing to send to that provider.
