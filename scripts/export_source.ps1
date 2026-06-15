# export_source.ps1 — Produce a clean source-only archive from tracked files.
#
# Usage:
#   .\scripts\export_source.ps1 [-Out claude-config-source.zip]
#
# This script uses `git archive` to export only files that are tracked in
# the repository, honouring .gitattributes export-ignore rules. It will never
# include .env files, .venv/, mas/projects/, browser state, databases, or logs.

param(
    [string]$Out = "claude-config-source.zip"
)

$RepoRoot = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) {
    throw "Not inside a git repository."
}

Set-Location $RepoRoot
git archive --format=zip --output $Out HEAD
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed."
}

Write-Host "Wrote $Out from tracked files only."
Write-Host "Verify with: python scripts/check_archive_clean.py $Out"
