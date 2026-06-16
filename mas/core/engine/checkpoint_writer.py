"""
Checkpoint Writer
Generates a CHECKPOINT.md file for the active project, capturing enough
context for a fresh session to resume without manual reconstruction.

Called automatically after:
  - every handoff accept() in HandoffEngine
  - every phase transition write() in SharedStateManager

Usage as library:
    from core.engine.checkpoint_writer import CheckpointWriter
    cw = CheckpointWriter("proj-YYYYMMDD-NNN-session-scheduler")
    cw.write()

Usage as CLI:
    uv run python mas/core/engine/checkpoint_writer.py --project-id proj-YYYYMMDD-NNN-session-scheduler
"""

from __future__ import annotations

import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from core.utils.wire_protocol import WireDecoder as _WireDecoder
    _wire_decoder = _WireDecoder()
except ImportError:
    _wire_decoder = None  # type: ignore

from core.paths import mas_root
ROOT = mas_root()   # mas/


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return str(ts)


class CheckpointWriter:
    """
    Reads shared state for a project and renders a CHECKPOINT.md
    in the project root directory.
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        try:
            from core.utils.config import resolve_project_dir
            self.project_dir = resolve_project_dir(project_id, projects_root=ROOT / "projects")
        except Exception:
            self.project_dir = ROOT / "projects" / project_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self) -> Path:
        """Generate or overwrite CHECKPOINT.md. Returns the path."""
        state = self._load_state()
        content = self._render(state)
        path = self.project_dir / "CHECKPOINT.md"
        path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self, state: dict) -> str:
        ci = state.get("core_identity", {})
        wf = state.get("workflow", {})
        pd = state.get("project_definition", {})
        ex = state.get("execution", {})
        spawn = state.get("spawning", {})

        project_id      = ci.get("project_id", self.project_id)
        phase           = ci.get("current_phase", "unknown")
        status          = ci.get("status", "unknown")
        request_id      = ci.get("request_id", "—")
        completed       = wf.get("completed_phases", [])
        handoff_history = wf.get("handoff_history", [])
        pending         = [h for h in handoff_history
                           if h.get("acceptance", {}).get("status") == "pending"]
        last_handoff    = handoff_history[-1] if handoff_history else None
        exec_plan       = ex.get("execution_plan_path", "—")
        risks           = ex.get("delivery_risks", [])
        spawn_count     = len(spawn.get("spawned_agents", []))

        brief_summary   = pd.get("brief_summary") or pd.get("original_brief", "")
        if isinstance(brief_summary, str) and len(brief_summary) > 300:
            brief_summary = brief_summary[:297] + "..."

        lines: list[str] = []

        lines += [
            f"# CHECKPOINT — {project_id}",
            f"> Generated: {_fmt_ts(datetime.now(timezone.utc).isoformat())}",
            "",
            "---",
            "",
            "## Identity",
            "",
            f"| Field         | Value |",
            f"|---------------|-------|",
            f"| Project ID    | `{project_id}` |",
            f"| Request ID    | `{request_id}` |",
            f"| Status        | `{status}` |",
            f"| Current phase | **`{phase}`** |",
            "",
        ]

        # Phase progress
        all_phases = [
            "intake", "specification", "planning", "capability_discovery",
            "execution", "review", "evaluation", "improvement", "closed",
        ]
        phase_row = []
        for p in all_phases:
            if p in completed:
                phase_row.append(f"~~{p}~~")
            elif p == phase:
                phase_row.append(f"**{p}**")
            else:
                phase_row.append(p)
        lines += [
            "## Phase Progress",
            "",
            " → ".join(phase_row),
            "",
        ]

        # Brief
        if brief_summary:
            lines += [
                "## Project Brief (summary)",
                "",
                brief_summary,
                "",
            ]

        # Execution plan
        lines += [
            "## Execution Plan",
            "",
            f"Path: `{exec_plan}`",
            "",
        ]

        # Last handoff
        lines += ["## Last Handoff", ""]
        if last_handoff:
            ho_id   = last_handoff.get("handoff_id", "—")
            frm     = last_handoff.get("from_agent", "—")
            to      = last_handoff.get("to_agent", "—")
            ho_ts   = _fmt_ts(last_handoff.get("timestamp"))
            ho_st   = last_handoff.get("acceptance", {}).get("status", "—")
            task    = last_handoff.get("task_description", "—")
            raw_payload = last_handoff.get("payload", {})
            # Expand wire format before rendering human-readable Markdown
            if _wire_decoder and isinstance(raw_payload, dict) and "_v" in raw_payload:
                raw_payload = _wire_decoder.decode(raw_payload)
            summary = raw_payload.get("summary", "—") if raw_payload else "—"
            lines += [
                f"| Field     | Value |",
                f"|-----------|-------|",
                f"| ID        | `{ho_id}` |",
                f"| From      | `{frm}` |",
                f"| To        | `{to}` |",
                f"| Timestamp | {ho_ts} |",
                f"| Status    | `{ho_st}` |",
                f"| Task      | {task} |",
                "",
                f"> {summary}",
                "",
            ]
        else:
            lines += ["_No handoffs yet._", ""]

        # Pending handoffs
        lines += ["## Pending Handoffs", ""]
        if pending:
            lines += ["| ID | From | To | Task |", "|---|------|----|----|"]
            for h in pending:
                lines.append(
                    f"| `{h['handoff_id']}` "
                    f"| `{h['from_agent']}` "
                    f"| `{h['to_agent']}` "
                    f"| {h['task_description']} |"
                )
            lines.append("")
        else:
            lines += ["_No pending handoffs._", ""]

        # Delivery risks
        if risks:
            lines += ["## Active Delivery Risks", ""]
            for r in risks:
                sev   = r.get("severity", "?")
                desc  = r.get("description", "—")
                lines.append(f"- **[{sev}]** {desc}")
            lines.append("")

        # Spawn count
        if spawn_count:
            lines += [
                "## Spawned Agents",
                "",
                f"{spawn_count} agent(s) spawned this project.",
                "",
            ]

        # Resume instructions
        lines += [
            "---",
            "",
            "## How to Resume",
            "",
            f"/resume-mas {project_id}",
            "",
            "```bash",
            f"uv run mas status {project_id}",
            f"uv run mas pending {project_id}",
            "```",
            "",
            "_Source of truth is this project's shared_state.yaml in this repository._",
            "",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        state_path = self.project_dir / "shared_state.yaml"
        if not state_path.exists():
            raise FileNotFoundError(
                f"No shared state found for project '{self.project_id}' "
                f"(expected {state_path})"
            )
        with state_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write CHECKPOINT.md for a project",
        epilog="uv run python mas/core/checkpoint_writer.py --project-id proj-001",
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    ns = parser.parse_args()

    try:
        cw = CheckpointWriter(ns.project_id)
        path = cw.write()
        print(f"[ok] Checkpoint written: {path}")
        return 0
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
