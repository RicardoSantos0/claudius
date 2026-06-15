#!/usr/bin/env python3
"""
validate_skills.py — Validate skills directory against the MAS skill registry.

Checks:
  1. Every skills/*/SKILL.md exists and has valid YAML frontmatter.
  2. Every active skill in registry_index.yaml exists on disk.
  3. Every MAS workflow skill (category: workflow) has trigger_phases set.
  4. Every recommended_for agent exists in registry_canonical.yaml.

Usage:
    python scripts/validate_skills.py [--repo-root <path>]

Exit codes:
    0  — all checks pass
    1  — one or more checks failed (errors printed to stdout)
    2  — usage error or registry files not found
"""

import sys
import argparse
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: uv pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def get_repo_root() -> Path:
    parser = argparse.ArgumentParser(description="Validate MAS skill registry")
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    args = parser.parse_args()
    if args.repo_root:
        return Path(args.repo_root)
    candidate = Path(__file__).parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    print("ERROR: cannot auto-detect repo root. Pass --repo-root.", file=sys.stderr)
    sys.exit(2)


def parse_frontmatter(content: str) -> dict | None:
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
    try:
        return yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return None


def load_registry(registry_path: Path) -> dict:
    if not registry_path.exists():
        print(f"ERROR: registry not found: {registry_path}", file=sys.stderr)
        sys.exit(2)
    try:
        with registry_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        print(f"ERROR: invalid YAML in {registry_path}: {exc}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    repo_root = get_repo_root()
    skills_dir = repo_root / "skills"
    registry_index_path = repo_root / "mas" / "roster" / "registry_index.yaml"
    registry_canonical_path = repo_root / "mas" / "roster" / "registry_canonical.yaml"

    errors: list[str] = []

    # Load registries
    registry_index = load_registry(registry_index_path)
    registry_canonical = load_registry(registry_canonical_path)

    skills_in_registry = registry_index.get("skills", [])
    agents_in_canonical = set(registry_canonical.get("agents", {}).keys())

    # Check 1: Every skills/*/SKILL.md exists and has valid frontmatter
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                errors.append(f"[SKILL.MD_MISSING] {skill_dir.name}: no SKILL.md found")
                continue
            content = skill_md.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            if fm is None:
                errors.append(f"[FRONTMATTER_INVALID] {skill_dir.name}/SKILL.md: missing or invalid YAML frontmatter")
            else:
                if not fm.get("name"):
                    errors.append(f"[FRONTMATTER_NO_NAME] {skill_dir.name}/SKILL.md: missing 'name' field")
                if not fm.get("description"):
                    errors.append(f"[FRONTMATTER_NO_DESC] {skill_dir.name}/SKILL.md: missing 'description' field")
    else:
        errors.append(f"[SKILLS_DIR_MISSING] skills/ directory not found at {skills_dir}")

    # Check 2: Every active registry skill exists on disk
    for entry in skills_in_registry:
        if not isinstance(entry, dict):
            continue
        skill_id = entry.get("skill_id", "")
        status = entry.get("status", "")
        if status != "active":
            continue
        skill_path = skills_dir / skill_id / "SKILL.md"
        if not skill_path.exists():
            errors.append(f"[REGISTRY_SKILL_MISSING_ON_DISK] '{skill_id}': active in registry but no skills/{skill_id}/SKILL.md")

    # Check 3: Every workflow skill has trigger_phases
    for entry in skills_in_registry:
        if not isinstance(entry, dict):
            continue
        skill_id = entry.get("skill_id", "")
        category = entry.get("category", "")
        status = entry.get("status", "")
        if status != "active" or category != "workflow":
            continue
        trigger_phases = entry.get("trigger_phases")
        if not trigger_phases:
            errors.append(f"[WORKFLOW_SKILL_NO_TRIGGER_PHASES] '{skill_id}': workflow skill missing trigger_phases")

    # Check 4: Every recommended_for agent exists in registry_canonical
    for entry in skills_in_registry:
        if not isinstance(entry, dict):
            continue
        skill_id = entry.get("skill_id", "")
        recommended_for = entry.get("recommended_for", [])
        if not isinstance(recommended_for, list):
            continue
        for agent_id in recommended_for:
            if agent_id not in agents_in_canonical:
                errors.append(
                    f"[RECOMMENDED_FOR_UNKNOWN_AGENT] '{skill_id}': recommended_for '{agent_id}' "
                    f"not found in registry_canonical.yaml"
                )

    # Report
    if errors:
        for err in errors:
            print(err)
        print(f"\n{len(errors)} error(s) found.")
        return 1

    active_count = sum(1 for e in skills_in_registry if isinstance(e, dict) and e.get("status") == "active")
    print(f"[ok] All checks passed. {active_count} active skill(s) validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
