#!/usr/bin/env python3
"""
validate_agents.py — Validate agent frontmatter and registry coverage.

Checks:
  1. Every agents/*.md (except _utilities.md) has YAML frontmatter
  2. Required frontmatter fields: name, description, tools
  3. Agent name is present (description of naming checked in notes)
  4. Every agent file is listed in mas/roster/registry_canonical.yaml
  5. Every registry entry's file exists on disk
  6. Registry claude_name is lowercase hyphenated

Usage:
    python scripts/validate_agents.py [--repo-root <path>]

Exit codes:
    0  — all checks pass
    1  — one or more checks failed (errors printed to stdout)
    2  — usage error or files not found
"""

import sys
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: uv pip install pyyaml", file=sys.stderr)
    sys.exit(2)


SKIP_FILES = {"_utilities.md"}
REQUIRED_FRONTMATTER_FIELDS = ["name", "description", "tools"]
APPROVED_TOOLS = {
    "Read", "Grep", "Glob", "Bash", "Agent", "Edit",
    "WebFetch", "WebSearch", "Write", "TodoWrite", "TodoRead"
}
CLAUDE_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')


def get_repo_root() -> Path:
    import argparse
    parser = argparse.ArgumentParser(description="Validate agent frontmatter and registry")
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    args = parser.parse_args()
    if args.repo_root:
        return Path(args.repo_root)
    # Auto-detect: look for pyproject.toml
    candidate = Path(__file__).parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    print("ERROR: cannot auto-detect repo root. Pass --repo-root.", file=sys.stderr)
    sys.exit(2)


def parse_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter from a markdown file. Returns None if not present."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return None
    fm_text = "\n".join(lines[1:end])
    try:
        return yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return None


def validate_agent_file(path: Path) -> list[str]:
    """Return list of error strings for an agent file."""
    errors = []
    content = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    if fm is None:
        return [f"{path.name}: missing YAML frontmatter"]

    for field in REQUIRED_FRONTMATTER_FIELDS:
        if not fm.get(field):
            errors.append(f"{path.name}: missing required frontmatter field '{field}'")

    # Check tools are from approved set
    tools_raw = fm.get("tools", "")
    if isinstance(tools_raw, str):
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    elif isinstance(tools_raw, list):
        tools = [str(t).strip() for t in tools_raw]
    else:
        tools = []
    for tool in tools:
        if tool not in APPROVED_TOOLS:
            errors.append(f"{path.name}: unknown tool '{tool}' (not in approved tool set)")

    return errors


def load_registry(registry_path: Path) -> dict:
    """Load registry_canonical.yaml and return the agents dict."""
    if not registry_path.exists():
        print(f"ERROR: registry not found at {registry_path}", file=sys.stderr)
        sys.exit(2)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    return data.get("agents", {})


def validate_registry_entry(agent_id: str, entry: dict, repo_root: Path) -> list[str]:
    """Return list of error strings for a registry entry."""
    errors = []

    # Check file exists
    file_path = repo_root / entry.get("file", "")
    if not file_path.exists():
        errors.append(f"Registry entry '{agent_id}': file not found at {entry.get('file')}")

    # Check claude_name is lowercase hyphenated
    claude_name = entry.get("claude_name", "")
    if not claude_name:
        errors.append(f"Registry entry '{agent_id}': missing 'claude_name'")
    elif not CLAUDE_NAME_PATTERN.match(claude_name):
        errors.append(
            f"Registry entry '{agent_id}': claude_name '{claude_name}' must be lowercase hyphenated (e.g. 'master-orchestrator')"
        )

    # Check required fields
    for field in ["trust_tier", "status", "domains", "roles"]:
        if not entry.get(field):
            errors.append(f"Registry entry '{agent_id}': missing required field '{field}'")

    return errors


def main() -> None:
    repo_root = get_repo_root()
    agents_dir = repo_root / "agents"
    registry_path = repo_root / "mas" / "roster" / "registry_canonical.yaml"

    if not agents_dir.exists():
        print(f"ERROR: agents/ directory not found at {agents_dir}", file=sys.stderr)
        sys.exit(2)

    all_errors: list[str] = []

    # 1. Validate each agent file
    agent_files = sorted(agents_dir.glob("*.md"))
    agent_file_names = set()
    for agent_file in agent_files:
        if agent_file.name in SKIP_FILES:
            continue
        agent_file_names.add(agent_file.name)
        errors = validate_agent_file(agent_file)
        all_errors.extend(errors)

    # 2. Load registry and validate coverage
    registry_agents = load_registry(registry_path)

    # Check every agent file is in the registry (by file field)
    registry_files = {Path(e["file"]).name for e in registry_agents.values() if "file" in e}
    for fname in agent_file_names:
        if fname not in registry_files and fname not in SKIP_FILES:
            all_errors.append(f"{fname}: not listed in registry_canonical.yaml")

    # 3. Validate each registry entry
    for agent_id, entry in registry_agents.items():
        errors = validate_registry_entry(agent_id, entry, repo_root)
        all_errors.extend(errors)

    # Report
    if all_errors:
        print(f"FAIL: {len(all_errors)} validation error(s):")
        for error in all_errors:
            print(f"  - {error}")
        sys.exit(1)
    else:
        agent_count = len([f for f in agent_file_names if f not in SKIP_FILES])
        print(f"OK: {agent_count} agent files and {len(registry_agents)} registry entries validated successfully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
