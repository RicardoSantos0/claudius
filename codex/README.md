# Codex surface: `mas-governance` plugin

This directory is a **local Codex marketplace** that makes OpenAI **Codex CLI** a first-class
surface over the `claudius` MAS core, following the "one provider-agnostic core, many surfaces"
model. It exposes the **66-tool `mas-server` MCP** inside Codex, the same governed engine Claude
Code reaches. The plugin bundles no skills; see [Skills](#skills).

```
codex/
├── .agents/plugins/marketplace.json     # marketplace index (one plugin: mas-governance)
└── plugins/mas-governance/
    ├── .codex-plugin/plugin.json        # plugin manifest (no skills key)
    └── .mcp.json                        # launches the claudius mas-server (stdio)
```

The `.mcp.json` here is **portable**: it runs `uv run --extra server mas-server` from this
checkout. For a setup that works regardless of Codex's working directory, pin the project path:

```jsonc
"command": "uv",
"args": ["run", "--project", "/abs/path/to/claudius", "--extra", "server", "mas-server"]
```

## Skills

Codex gets every skill through a link, never through this plugin. Codex reads skills from
`$CODEX_HOME/skills`, which is `~/.codex/skills` unless `CODEX_HOME` is set.
`scripts/link_skill_surfaces.py`, which setup runs, links each skill in `skills/` into
`~/.codex/skills`, so Codex reads the canonical files and an edit to `skills/` reaches Codex
without a regeneration step. Up to plugin 0.2.0 the plugin carried copies of the eight
`mas-*` skills instead; copies drift from the canonical files, so they were removed.

## Register with Codex

Add to `~/.codex/config.toml` (mirrors Codex's existing marketplace/plugin pattern):

```toml
[marketplaces.mas-local]
source_type = "local"
source = '/abs/path/to/claudius/codex'

[plugins."mas-governance@mas-local"]
enabled = true
```

Then **reload Codex** (it loads marketplaces/plugins at startup).

## Verify

1. Confirm the server launches standalone (the command Codex will run):

   ```bash
   uv run --extra server mas-server        # from this checkout
   ```

   It should start an MCP stdio server (Ctrl-C to stop). If `mcp` is missing:
   `uv sync --extra server`.

2. In Codex after reload: the `mas-governance` plugin should appear enabled and the `mas_*` tools
   (e.g. `mas_status`, `mas_roster`, `mas_prompt`, `mas_ingest`) should be callable.
