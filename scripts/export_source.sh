#!/usr/bin/env bash
set -euo pipefail

# export_source.sh — Produce a clean source-only archive from tracked files.
#
# Usage:
#   scripts/export_source.sh [output-file]
#
# Default output: claude-config-source.zip
#
# This script uses `git archive` to export only files that are tracked in
# the repository, honouring .gitattributes export-ignore rules. It will never
# include .env files, .venv/, mas/projects/, browser state, databases, or logs.

repo_root="$(git rev-parse --show-toplevel)"
out="${1:-claude-config-source.zip}"

cd "$repo_root"
git archive --format=zip --output="$out" HEAD

echo "Wrote $out from tracked files only."
echo "Verify with: python scripts/check_archive_clean.py $out"
