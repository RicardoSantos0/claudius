# Links ~/.claude/agents, ~/.claude/commands, and ~/.claude/skills to this repo.
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
Link-Dir "skills"
Link-Dir "standards"

Write-Host "----------------------------------------"
Write-Host "Done: $linked linked, $failed failed."
if ($failed -ne 0) { exit 1 }
