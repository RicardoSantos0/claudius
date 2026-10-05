#!/usr/bin/env bash
# Links ~/.claude/agents, ~/.claude/commands and ~/.claude/standards to this repo,
# and points every installed client at skills/ (scripts/link_skill_surfaces.py).
# Run once per machine after cloning.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"

mkdir -p "$CLAUDE_DIR"

linked=0
failed=0

link() {
  local target="$REPO_DIR/$1"
  local link="$CLAUDE_DIR/$1"

  if [ -L "$link" ]; then
    echo "Already linked: $link"
    return
  elif [ -d "$link" ]; then
    echo "Backing up existing $link -> ${link}.bak"
    mv "$link" "${link}.bak"
  fi

  # Create the link without aborting the whole script on a single failure.
  if ln -s "$target" "$link" 2>/dev/null && [ -L "$link" ] && [ -e "$link" ]; then
    echo "Linked: $link -> $target"
    linked=$((linked + 1))
  else
    echo "FAILED to link: $link -> $target" >&2
    failed=$((failed + 1))
  fi
}

link agents
link commands
link standards

# --- Skills: point every installed client at skills/, never copy it ---
# One link per skill in ~/.claude/skills, ~/.copilot/skills and ~/.codex/skills,
# plus a skills.json entry for Antigravity; opencode and VS Code Copilot read the
# Claude folder. See scripts/link_skill_surfaces.py for the paths and why.
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  PYTHON_BIN=""
fi
if [ -n "$PYTHON_BIN" ]; then
  echo "----------------------------------------"
  # --home: on Windows Python reads USERPROFILE, not this shell's HOME.
  "$PYTHON_BIN" "$REPO_DIR/scripts/link_skill_surfaces.py" --repo-root "$REPO_DIR" --home "$HOME" \
    || { echo "Skill linking reported problems; see the lines above." >&2; failed=$((failed + 1)); }
else
  echo "Skipping skill links: no python on PATH (run scripts/link_skill_surfaces.py later)"
fi

echo "----------------------------------------"
echo "Done: $linked linked, $failed failed."
if [ "$failed" -ne 0 ]; then
  exit 1
fi
