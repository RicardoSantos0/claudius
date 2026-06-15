#!/usr/bin/env bash
# Links ~/.claude/agents, ~/.claude/commands, and ~/.claude/skills to this repo.
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
link skills
link standards

echo "----------------------------------------"
echo "Done: $linked linked, $failed failed."
if [ "$failed" -ne 0 ]; then
  exit 1
fi
