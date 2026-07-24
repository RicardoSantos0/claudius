#!/usr/bin/env python3
"""
check_mas_discipline.py — enforce MAS evidence for commits.

Local commit-msg usage:
    python scripts/check_mas_discipline.py --message-file .git/COMMIT_EDITMSG

CI marker-only usage:
    python scripts/check_mas_discipline.py --message-file commit.txt --skip-project-state

Rules:
  1. Commit message must include `MAS: proj-YYYYMMDD-NNN-slug`.
  2. Local strict mode requires that project to exist under mas/projects/.
  3. Standard projects must have accepted inquirer intake and handoff history.
  4. Strict mode requires token accounting and closed-project final artifacts.
  5. Emergency bypasses must be explicit: `MAS-BYPASS: <rationale>`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by users without deps
    print("ERROR: PyYAML not installed. Run: uv sync", file=sys.stderr)
    sys.exit(2)


PROJECT_RE = re.compile(r"\bproj-\d{8}-\d{3}-[a-z0-9][a-z0-9-]*\b")
BYPASS_RE = re.compile(r"(?im)^MAS-BYPASS:\s*(\S.*)$")


def repo_root(arg: str | None = None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    candidate = Path(__file__).resolve().parents[1]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd().resolve()


def read_message(path: str | None, project_id: str | None) -> tuple[str, str | None]:
    if path:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    else:
        text = ""
    return text, project_id or parse_project_id(text)


def parse_project_id(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().lower().startswith("mas:"):
            match = PROJECT_RE.search(line)
            if match:
                return match.group(0)
    return None


def parse_bypass(text: str) -> str | None:
    match = BYPASS_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def find_project_dir(root: Path, project_id: str) -> Path | None:
    projects = root / "mas" / "projects"
    flat = projects / project_id
    if flat.is_dir():
        return flat
    if not projects.is_dir():
        return None
    matches = [
        child / project_id
        for child in projects.iterdir()
        if child.is_dir() and (child / project_id).is_dir()
    ]
    if not matches:
        return None
    with_state = [p for p in matches if (p / "shared_state.yaml").exists()]
    return with_state[0] if with_state else matches[0]


def load_state(project_dir: Path) -> dict[str, Any]:
    state_path = project_dir / "shared_state.yaml"
    if not state_path.exists():
        raise FileNotFoundError(f"{state_path} missing")
    return yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}


def handoff_status(handoff: dict[str, Any]) -> str:
    acceptance = handoff.get("acceptance")
    if isinstance(acceptance, dict) and acceptance.get("status"):
        return str(acceptance["status"])
    return str(handoff.get("status") or handoff.get("acc") or "")


def has_accepted_inquirer_intake(history: list[dict[str, Any]]) -> bool:
    for handoff in history:
        if handoff_status(handoff) != "accepted":
            continue
        if handoff.get("phase") != "intake":
            continue
        agents = {str(handoff.get("from_agent", "")), str(handoff.get("to_agent", ""))}
        if "inquirer_agent" in agents:
            return True
    return False


def validate_state(project_dir: Path, state: dict[str, Any], *, allow_active: bool) -> list[str]:
    errors: list[str] = []
    workflow = state.get("workflow", {}) or {}
    core = state.get("core_identity", {}) or {}
    communication = state.get("communication", {}) or {}

    mode = str(workflow.get("mode") or core.get("mode") or "standard")
    history = workflow.get("handoff_history", []) or []
    if mode == "standard":
        if not history:
            errors.append("standard project has no handoff history")
        if not has_accepted_inquirer_intake(history):
            errors.append("standard project lacks accepted inquirer_agent intake handoff")

    total_tokens = int(communication.get("total_tokens_used") or 0)
    if not communication.get("token_tracking_enabled", True):
        errors.append("token tracking is disabled")
    if total_tokens <= 0:
        errors.append("manual/API token accounting is zero")

    phase = str(core.get("current_phase") or "")
    status = str(core.get("status") or "")
    is_closed = phase == "closed" or status == "closed"
    if not allow_active and not is_closed:
        errors.append(f"project is not closed (phase={phase or 'unknown'}, status={status or 'unknown'})")
    if is_closed:
        for required in ("CLOSED.md", "final_shared_state.yaml"):
            if not (project_dir / required).exists():
                errors.append(f"closed project missing {required}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce MAS project evidence for commits.")
    parser.add_argument("--repo-root", default=None, help="Repository root")
    parser.add_argument("--message-file", default=None, help="Commit message file")
    parser.add_argument("--project-id", default=None, help="Project id override")
    parser.add_argument("--skip-project-state", action="store_true",
                        help="Only require MAS marker/bypass; do not inspect mas/projects")
    parser.add_argument("--allow-active", action="store_true",
                        help="Allow an active MAS project instead of requiring closed final artifacts")
    args = parser.parse_args(argv)

    root = repo_root(args.repo_root)
    message, project_id = read_message(args.message_file, args.project_id)

    bypass = parse_bypass(message)
    if bypass:
        print(f"[warn] MAS discipline bypass recorded: {bypass}")
        return 0

    if not project_id:
        print(
            "FAIL: commit requires `MAS: proj-YYYYMMDD-NNN-slug` in the message "
            "or an explicit `MAS-BYPASS: <rationale>`.",
            file=sys.stderr,
        )
        return 1

    if args.skip_project_state:
        print(f"[ok] MAS marker present: {project_id}")
        return 0

    project_dir = find_project_dir(root, project_id)
    if project_dir is None:
        print(f"FAIL: MAS project not found under mas/projects: {project_id}", file=sys.stderr)
        return 1

    try:
        state = load_state(project_dir)
    except Exception as exc:
        print(f"FAIL: could not read MAS project state for {project_id}: {exc}", file=sys.stderr)
        return 1

    errors = validate_state(project_dir, state, allow_active=args.allow_active)
    if errors:
        print(f"FAIL: MAS discipline check failed for {project_id}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"[ok] MAS discipline evidence present: {project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
