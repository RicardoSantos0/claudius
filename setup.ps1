# Links ~/.claude/agents, ~/.claude/commands and ~/.claude/standards to this repo,
# and points every installed client at skills/ (scripts/link_skill_surfaces.py).
# Run once per machine after cloning (as Administrator for symlinks).

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeDir = "$env:USERPROFILE\.claude"

New-Item -ItemType Directory -Force -Path $ClaudeDir | Out-Null

$linked = 0
$failed = 0

function Link-Dir($name) {
  $target = "$RepoDir\$name"
  $link   = "$ClaudeDir\$name"

  if (Test-Path -PathType Container $link) {
    if ((Get-Item $link).LinkType -eq "SymbolicLink") {
      Write-Host "Already linked: $link"
      return
    } else {
      Write-Host "Backing up existing $link -> ${link}.bak"
      Move-Item $link "${link}.bak"
    }
  }

  try {
    New-Item -ItemType SymbolicLink -Path $link -Target $target -ErrorAction Stop | Out-Null
    Write-Host "Linked: $link -> $target"
    $script:linked++
  } catch {
    Write-Host "FAILED to link: $link -> $target ($($_.Exception.Message))" -ForegroundColor Red
    $script:failed++
  }
}

Link-Dir "agents"
Link-Dir "commands"
Link-Dir "standards"

# --- Skills: point every installed client at skills/, never copy it ---
# One junction per skill in ~/.claude/skills, ~/.copilot/skills and
# ~/.codex/skills, plus a skills.json entry for Antigravity; opencode and VS Code
# Copilot read the Claude folder. See scripts/link_skill_surfaces.py for why.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
  Write-Host "----------------------------------------"
  & $pythonCmd.Source "$RepoDir\scripts\link_skill_surfaces.py" --repo-root $RepoDir
  if ($LASTEXITCODE -ne 0) { Write-Host "Skill linking reported problems; see the lines above." -ForegroundColor Red; $failed++ }
} else {
  Write-Host "Skipping skill links: python not on PATH (run scripts/link_skill_surfaces.py later)"
}

Write-Host "----------------------------------------"
Write-Host "Done: $linked linked, $failed failed."
if ($failed -ne 0) { exit 1 }
