"""
Task Board Manager
Manages the task board for a single project: tasks, milestones,
dependency chains, progress tracking, and execution plan serialization.

Backed by two files in the project's execution/ directory:
  projects/{project_id}/execution/task_board.yaml   — task + milestone data
  projects/{project_id}/execution/execution_plan.yaml — compiled plan snapshot

Task statuses:     planned → assigned → in_progress → (completed | blocked | failed)
Milestone statuses: pending → in_progress → (completed | blocked)
Effort tiers:       trivial | small | medium | large | extra-large

Usage as library:
    from core.engine.task_board import TaskBoard
    board = TaskBoard("proj-001")
    ms_id = board.create_milestone({...})
    task_id = board.create_task({...})

Usage as CLI:
    uv run python core/task_board.py create-task --project-id proj-001 --task-json '{...}'
    uv run python core/task_board.py update-status --project-id proj-001 --task-id task-001 --status in_progress
    uv run python core/task_board.py list --project-id proj-001 [--status planned] [--milestone ms-001]
    uv run python core/task_board.py show --project-id proj-001 --task-id task-001
    uv run python core/task_board.py blocked --project-id proj-001
    uv run python core/task_board.py milestone-status --project-id proj-001 --milestone-id ms-001
    uv run python core/task_board.py progress-report --project-id proj-001 [--milestone-id ms-001]
    uv run python core/task_board.py create-milestone --project-id proj-001 --milestone-json '{...}'
    uv run python core/task_board.py deps --project-id proj-001 --task-id task-001
"""

import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from core.paths import mas_root
ROOT = mas_root()

VALID_TASK_STATUSES = {"planned", "assigned", "in_progress", "blocked", "completed", "failed"}
VALID_MILESTONE_STATUSES = {"pending", "in_progress", "completed", "blocked"}
VALID_EFFORT_TIERS = {"trivial", "small", "medium", "large", "extra-large"}
OVER_EFFORT_MULTIPLIER = 2   # flag if actual > 2x typical of effort tier


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# TaskBoard
# ---------------------------------------------------------------------------

class TaskBoard:
    """
    Manages tasks and milestones for a single project.
    All data is stored in projects/{project_id}/execution/task_board.yaml.
    """

    def __init__(self, project_id: str, projects_root: Optional[Path] = None):
        self.project_id = project_id
        if projects_root is None:
            from core.config import get_projects_dir
            projects_root = get_projects_dir()
        from core.utils.config import resolve_project_dir
        self.execution_dir = resolve_project_dir(project_id, projects_root=projects_root) / "execution"
        self.board_path = self.execution_dir / "task_board.yaml"
        self.plan_path = self.execution_dir / "execution_plan.yaml"

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        self.execution_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        """Load task board from disk. If absent but execution_plan.yaml exists, auto-sync."""
        if not self.board_path.exists():
            if self.plan_path.exists():
                self.sync_from_execution_plan()
            else:
                return {"tasks": [], "milestones": []}
        with open(self.board_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("tasks", [])
        data.setdefault("milestones", [])
        return data

    def sync_from_execution_plan(self) -> int:
        """
        Populate task_board.yaml from an existing execution_plan.yaml.
        Idempotent: merges by task_id/milestone_id, skipping duplicates.
        Returns the number of tasks written.
        """
        if not self.plan_path.exists():
            raise FileNotFoundError(f"No execution_plan.yaml at {self.plan_path}")
        with open(self.plan_path, encoding="utf-8") as f:
            plan = yaml.safe_load(f) or {}

        milestones = plan.get("milestones", [])
        tasks = plan.get("tasks", [])

        existing = {"tasks": [], "milestones": []}
        if self.board_path.exists():
            with open(self.board_path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or existing
            existing.setdefault("tasks", [])
            existing.setdefault("milestones", [])

        existing_ms_ids = {m["milestone_id"] for m in existing["milestones"]}
        existing_task_ids = {t["task_id"] for t in existing["tasks"]}

        for ms in milestones:
            if ms.get("milestone_id") not in existing_ms_ids:
                existing["milestones"].append(ms)

        added = 0
        for task in tasks:
            if task.get("task_id") not in existing_task_ids:
                existing["tasks"].append(task)
                added += 1

        self._ensure_dir()
        with open(self.board_path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        return added

    def _save(self, data: dict) -> None:
        self._ensure_dir()
        with open(self.board_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    def _next_task_id(self, data: dict) -> str:
        n = len(data["tasks"]) + 1
        return f"task-{self.project_id}-{n:03d}"

    def _next_milestone_id(self, data: dict) -> str:
        n = len(data["milestones"]) + 1
        return f"ms-{self.project_id}-{n:02d}"

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    def create_milestone(self, milestone_data: dict) -> str:
        """
        Create a milestone. Required: name, completion_criteria.
        Optional: description, task_ids.
        Returns the new milestone_id.
        """
        required = ["name", "completion_criteria"]
        missing = [f for f in required if f not in milestone_data]
        if missing:
            raise ValueError(f"Missing required milestone fields: {missing}")

        data = self._load()
        ms_id = milestone_data.get("milestone_id") or self._next_milestone_id(data)

        # Check for duplicate
        if any(m["milestone_id"] == ms_id for m in data["milestones"]):
            raise ValueError(f"Milestone already exists: {ms_id}")

        milestone = {
            "milestone_id": ms_id,
            "name": milestone_data["name"],
            "description": milestone_data.get("description", ""),
            "task_ids": milestone_data.get("task_ids", []),
            "completion_criteria": milestone_data["completion_criteria"],
            "status": "pending",
            "created_at": _now(),
            "started_at": None,
            "completed_at": None,
        }
        data["milestones"].append(milestone)
        self._save(data)
        return ms_id

    def get_milestone(self, milestone_id: str) -> Optional[dict]:
        """Return a milestone by ID, or None."""
        data = self._load()
        return next((m for m in data["milestones"]
                     if m["milestone_id"] == milestone_id), None)

    def get_milestone_status(self, milestone_id: str) -> dict:
        """
        Compute the live completion status of a milestone.
        Returns: {milestone_id, name, status, total, completed, blocked, in_progress, planned, pct_complete}
        """
        data = self._load()
        ms = next((m for m in data["milestones"]
                   if m["milestone_id"] == milestone_id), None)
        if ms is None:
            raise ValueError(f"Milestone not found: {milestone_id}")

        task_ids = ms.get("task_ids", [])
        tasks = [t for t in data["tasks"] if t["task_id"] in task_ids]

        counts = {s: 0 for s in VALID_TASK_STATUSES}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        total = len(tasks)
        completed = counts["completed"]
        pct = round(completed / total * 100, 1) if total > 0 else 0.0

        return {
            "milestone_id": milestone_id,
            "name": ms["name"],
            "status": ms["status"],
            "total_tasks": total,
            "completed": completed,
            "blocked": counts["blocked"],
            "in_progress": counts["in_progress"],
            "planned": counts["planned"],
            "failed": counts["failed"],
            "pct_complete": pct,
            "all_complete": total > 0 and completed == total,
        }

    def _refresh_milestone_status(self, data: dict, milestone_id: str) -> None:
        """Auto-advance milestone status based on its tasks."""
        ms = next((m for m in data["milestones"]
                   if m["milestone_id"] == milestone_id), None)
        if ms is None:
            return

        task_ids = ms.get("task_ids", [])
        if not task_ids:
            return

        tasks = [t for t in data["tasks"] if t["task_id"] in task_ids]
        statuses = {t["status"] for t in tasks}

        if all(t["status"] == "completed" for t in tasks):
            ms["status"] = "completed"
            ms["completed_at"] = _now()
        elif "blocked" in statuses:
            ms["status"] = "blocked"
        elif "in_progress" in statuses or "assigned" in statuses:
            if ms["status"] == "pending":
                ms["status"] = "in_progress"
                ms["started_at"] = _now()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, task_data: dict) -> str:
        """
        Add a task to the board.
        Required: description, milestone.
        Optional: task_id, required_inputs, expected_outputs, dependencies,
                  assigned_to, estimated_effort.
        Returns the new task_id.
        """
        required = ["description", "milestone"]
        missing = [f for f in required if f not in task_data]
        if missing:
            raise ValueError(f"Missing required task fields: {missing}")

        effort = task_data.get("estimated_effort", "small")
        if effort not in VALID_EFFORT_TIERS:
            raise ValueError(
                f"Invalid estimated_effort '{effort}'. "
                f"Must be one of: {sorted(VALID_EFFORT_TIERS)}"
            )

        data = self._load()
        task_id = task_data.get("task_id") or self._next_task_id(data)

        if any(t["task_id"] == task_id for t in data["tasks"]):
            raise ValueError(f"Task already exists: {task_id}")

        task = {
            "task_id": task_id,
            "description": task_data["description"],
            "milestone": task_data["milestone"],
            "required_inputs": task_data.get("required_inputs", []),
            "expected_outputs": task_data.get("expected_outputs", []),
            "dependencies": task_data.get("dependencies", []),
            "assigned_to": task_data.get("assigned_to"),
            "status": "planned",
            "estimated_effort": effort,
            "actual_effort": None,
            "blocker_description": None,
            "over_effort": False,
            "created_at": _now(),
            "started_at": None,
            "completed_at": None,
            "notes": task_data.get("notes", ""),
        }

        # Register task_id in milestone
        ms = next((m for m in data["milestones"]
                   if m["milestone_id"] == task["milestone"]), None)
        if ms is not None and task_id not in ms["task_ids"]:
            ms["task_ids"].append(task_id)

        data["tasks"].append(task)
        self._save(data)
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        """Return a task by ID, or None."""
        data = self._load()
        return next((t for t in data["tasks"] if t["task_id"] == task_id), None)

    def update_status(
        self,
        task_id: str,
        status: str,
        notes: Optional[str] = None,
        blocker_description: Optional[str] = None,
        actual_effort: Optional[str] = None,
    ) -> bool:
        """
        Update a task's status.
        Returns True if found and updated, False if task not found.
        Raises ValueError for invalid status.
        """
        if status not in VALID_TASK_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. "
                f"Must be one of: {sorted(VALID_TASK_STATUSES)}"
            )

        data = self._load()
        task = next((t for t in data["tasks"] if t["task_id"] == task_id), None)
        if task is None:
            return False

        now = _now()
        task["status"] = status

        if notes:
            task["notes"] = notes
        if blocker_description is not None:
            task["blocker_description"] = blocker_description
        if actual_effort is not None:
            if actual_effort not in VALID_EFFORT_TIERS:
                raise ValueError(f"Invalid actual_effort '{actual_effort}'")
            task["actual_effort"] = actual_effort
            # Check over-effort
            tier_order = ["trivial", "small", "medium", "large", "extra-large"]
            est_idx = tier_order.index(task["estimated_effort"])
            act_idx = tier_order.index(actual_effort)
            task["over_effort"] = act_idx >= est_idx + OVER_EFFORT_MULTIPLIER

        if status in ("in_progress", "assigned") and task.get("started_at") is None:
            task["started_at"] = now
        if status == "completed":
            task["completed_at"] = now
            task["blocker_description"] = None

        # Refresh milestone
        if task.get("milestone"):
            self._refresh_milestone_status(data, task["milestone"])

        self._save(data)
        return True

    def assign_task(self, task_id: str, agent_id: str) -> bool:
        """Assign a task to an agent and set status to 'assigned'."""
        data = self._load()
        task = next((t for t in data["tasks"] if t["task_id"] == task_id), None)
        if task is None:
            return False
        task["assigned_to"] = agent_id
        task["status"] = "assigned"
        if task.get("started_at") is None:
            task["started_at"] = _now()
        if task.get("milestone"):
            self._refresh_milestone_status(data, task["milestone"])
        self._save(data)
        return True

    def list_tasks(
        self,
        status: Optional[str] = None,
        milestone: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ) -> list:
        """Return tasks filtered by status, milestone, or assignee."""
        data = self._load()
        tasks = data["tasks"]
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        if milestone:
            tasks = [t for t in tasks if t["milestone"] == milestone]
        if assigned_to:
            tasks = [t for t in tasks if t.get("assigned_to") == assigned_to]
        return tasks

    def get_blocked(self) -> list:
        """Return all blocked tasks."""
        return self.list_tasks(status="blocked")

    def get_ready(self) -> list:
        """Return tasks with status 'planned' whose dependencies are all completed."""
        data = self._load()
        completed_ids = {t["task_id"] for t in data["tasks"] if t["status"] == "completed"}
        return [
            t for t in data["tasks"]
            if t["status"] == "planned"
            and all(dep in completed_ids for dep in t.get("dependencies", []))
        ]

    # ------------------------------------------------------------------
    # Dependency chain
    # ------------------------------------------------------------------

    def get_dependency_chain(self, task_id: str) -> list:
        """
        Return the full transitive dependency chain for a task (BFS).
        Returns list of task_ids in dependency order (shallowest first).
        Raises ValueError on circular dependencies.
        """
        data = self._load()
        task_index = {t["task_id"]: t for t in data["tasks"]}

        if task_id not in task_index:
            raise ValueError(f"Task not found: {task_id}")

        visited = []
        queue = list(task_index[task_id].get("dependencies", []))
        seen = set()

        while queue:
            tid = queue.pop(0)
            if tid in seen:
                continue
            if tid == task_id:
                raise ValueError(f"Circular dependency detected involving {task_id}")
            seen.add(tid)
            visited.append(tid)
            dep_task = task_index.get(tid)
            if dep_task:
                queue.extend(dep_task.get("dependencies", []))

        return visited

    # ------------------------------------------------------------------
    # Progress reports
    # ------------------------------------------------------------------

    def produce_progress_report(self, milestone_id: Optional[str] = None) -> dict:
        """
        Generate a progress report.
        If milestone_id is given, scope the report to that milestone.
        Otherwise report on the full project.
        """
        data = self._load()
        now = _now()

        if milestone_id:
            ms_status = self.get_milestone_status(milestone_id)
            tasks = [t for t in data["tasks"]
                     if t["milestone"] == milestone_id]
            scope = f"milestone:{milestone_id}"
        else:
            tasks = data["tasks"]
            scope = "project"
            ms_status = None

        total = len(tasks)
        counts = {s: 0 for s in VALID_TASK_STATUSES}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        completed = counts["completed"]
        pct = round(completed / total * 100, 1) if total > 0 else 0.0

        blocked_tasks = [t for t in tasks if t["status"] == "blocked"]
        over_effort_tasks = [t for t in tasks if t.get("over_effort")]
        in_progress = [t for t in tasks if t["status"] == "in_progress"]

        report = {
            "project_id": self.project_id,
            "generated_at": now,
            "scope": scope,
            "total_tasks": total,
            "pct_complete": pct,
            "by_status": counts,
            "milestone_status": ms_status,
            "in_progress_tasks": [
                {"task_id": t["task_id"], "description": t["description"],
                 "assigned_to": t.get("assigned_to")}
                for t in in_progress
            ],
            "blocked_tasks": [
                {"task_id": t["task_id"], "description": t["description"],
                 "blocker": t.get("blocker_description", "")}
                for t in blocked_tasks
            ],
            "over_effort_tasks": [
                {"task_id": t["task_id"], "description": t["description"],
                 "estimated": t["estimated_effort"],
                 "actual": t.get("actual_effort")}
                for t in over_effort_tasks
            ],
            "risks": [
                "Tasks blocked: escalation required" if blocked_tasks else None,
                "Over-effort tasks detected: re-scope may be needed" if over_effort_tasks else None,
            ],
        }
        report["risks"] = [r for r in report["risks"] if r]
        return report

    # ------------------------------------------------------------------
    # Execution plan
    # ------------------------------------------------------------------

    def produce_execution_plan(
        self,
        product_plan_path: str,
        approved_by: str = "master_orchestrator",
    ) -> dict:
        """
        Compile the full execution plan from current board state.
        Writes execution_plan.yaml to disk and returns the plan dict.
        """
        data = self._load()
        now = _now()

        plan = {
            "project_id": self.project_id,
            "created_at": now,
            "created_by": "project_manager_agent",
            "approved_by": approved_by,
            "approval_status": "pending_master_review",
            "product_plan_source": product_plan_path,
            "milestones": data["milestones"],
            "tasks": data["tasks"],
            "total_tasks": len(data["tasks"]),
            "total_milestones": len(data["milestones"]),
            "task_count_by_effort": self._count_by_field(data["tasks"], "estimated_effort"),
            "dependency_summary": {
                t["task_id"]: t.get("dependencies", [])
                for t in data["tasks"]
                if t.get("dependencies")
            },
        }

        self._ensure_dir()
        with open(self.plan_path, "w", encoding="utf-8") as f:
            yaml.dump(plan, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

        return plan

    @staticmethod
    def _count_by_field(items: list, field: str) -> dict:
        counts: dict = {}
        for item in items:
            val = item.get(field, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Blocker alerts
    # ------------------------------------------------------------------

    def build_blocker_alert(self, task_id: str) -> dict:
        """
        Build a blocker alert record for a blocked task.
        Suitable for appending to shared state execution.blocker_alerts.
        """
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] != "blocked":
            raise ValueError(f"Task {task_id} is not blocked (status={task['status']})")

        return {
            "alert_id": f"block-{task_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "task_id": task_id,
            "task_description": task["description"],
            "milestone": task["milestone"],
            "assigned_to": task.get("assigned_to"),
            "blocker_description": task.get("blocker_description", ""),
            "raised_at": _now(),
            "resolved": False,
            "resolved_at": None,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="task_board",
        description="Task Board CLI — create, update, and query tasks and milestones",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # create-task
    ct = sub.add_parser("create-task", help="Add a task to the board")
    ct.add_argument("--project-id", required=True)
    ct.add_argument("--task-json", required=True,
                    help="JSON string of task data")

    # create-milestone
    cm = sub.add_parser("create-milestone", help="Create a milestone")
    cm.add_argument("--project-id", required=True)
    cm.add_argument("--milestone-json", required=True,
                    help="JSON string of milestone data")

    # update-status
    us = sub.add_parser("update-status", help="Update a task's status")
    us.add_argument("--project-id", required=True)
    us.add_argument("--task-id", required=True)
    us.add_argument("--status", required=True,
                    choices=sorted(VALID_TASK_STATUSES))
    us.add_argument("--notes", default=None)
    us.add_argument("--blocker", default=None,
                    help="Blocker description (for status=blocked)")
    us.add_argument("--actual-effort", default=None,
                    choices=sorted(VALID_EFFORT_TIERS))

    # list
    ls = sub.add_parser("list", help="List tasks (optional filters)")
    ls.add_argument("--project-id", required=True)
    ls.add_argument("--status", default=None)
    ls.add_argument("--milestone", default=None)
    ls.add_argument("--assigned-to", default=None)

    # show
    sh = sub.add_parser("show", help="Show a task in detail")
    sh.add_argument("--project-id", required=True)
    sh.add_argument("--task-id", required=True)

    # blocked
    bl = sub.add_parser("blocked", help="Show all blocked tasks")
    bl.add_argument("--project-id", required=True)

    # milestone-status
    ms = sub.add_parser("milestone-status", help="Show milestone completion status")
    ms.add_argument("--project-id", required=True)
    ms.add_argument("--milestone-id", required=True)

    # progress-report
    pr = sub.add_parser("progress-report", help="Generate a progress report")
    pr.add_argument("--project-id", required=True)
    pr.add_argument("--milestone-id", default=None)

    # deps
    dp = sub.add_parser("deps", help="Show full dependency chain for a task")
    dp.add_argument("--project-id", required=True)
    dp.add_argument("--task-id", required=True)

    # plan
    pl = sub.add_parser("plan", help="Compile and write the execution plan")
    pl.add_argument("--project-id", required=True)
    pl.add_argument("--product-plan-path", required=True)

    # sync-from-plan
    sfp = sub.add_parser("sync-from-plan",
                         help="Populate task_board.yaml from an existing execution_plan.yaml")
    sfp.add_argument("--project-id", required=True)

    return p


def main_cli(args=None) -> int:
    p = _build_parser()
    ns = p.parse_args(args)
    board = TaskBoard(ns.project_id)

    if ns.command == "create-task":
        task_data = json.loads(ns.task_json)
        task_id = board.create_task(task_data)
        print(f"[ok] Created task: {task_id}")
        return 0

    if ns.command == "create-milestone":
        ms_data = json.loads(ns.milestone_json)
        ms_id = board.create_milestone(ms_data)
        print(f"[ok] Created milestone: {ms_id}")
        return 0

    if ns.command == "update-status":
        found = board.update_status(
            ns.task_id, ns.status,
            notes=ns.notes,
            blocker_description=ns.blocker,
            actual_effort=ns.actual_effort,
        )
        if found:
            print(f"[ok] {ns.task_id} -> {ns.status}")
        else:
            print(f"[error] Task not found: {ns.task_id}", file=sys.stderr)
            return 1
        return 0

    if ns.command == "list":
        tasks = board.list_tasks(
            status=ns.status,
            milestone=ns.milestone,
            assigned_to=ns.assigned_to,
        )
        if not tasks:
            print("[none] No tasks found.")
            return 0
        for t in tasks:
            print(
                f"  [{t['status']:12}] {t['task_id']}  "
                f"effort={t['estimated_effort']}  "
                f"ms={t['milestone']}"
            )
            print(f"    {t['description']}")
        return 0

    if ns.command == "show":
        task = board.get_task(ns.task_id)
        if task is None:
            print(f"[error] Task not found: {ns.task_id}", file=sys.stderr)
            return 1
        import yaml as _yaml
        print(_yaml.dump(task, default_flow_style=False, allow_unicode=True))
        return 0

    if ns.command == "blocked":
        blocked = board.get_blocked()
        if not blocked:
            print("[ok] No blocked tasks.")
            return 0
        print(f"{len(blocked)} blocked task(s):")
        for t in blocked:
            print(f"  {t['task_id']}: {t['description']}")
            print(f"    blocker: {t.get('blocker_description', '—')}")
        return 0

    if ns.command == "milestone-status":
        status = board.get_milestone_status(ns.milestone_id)
        import yaml as _yaml
        print(_yaml.dump(status, default_flow_style=False, allow_unicode=True))
        return 0

    if ns.command == "progress-report":
        report = board.produce_progress_report(milestone_id=ns.milestone_id)
        import yaml as _yaml
        print(_yaml.dump(report, default_flow_style=False, allow_unicode=True))
        return 0

    if ns.command == "deps":
        try:
            chain = board.get_dependency_chain(ns.task_id)
            if not chain:
                print(f"[none] {ns.task_id} has no dependencies.")
            else:
                print(f"Dependency chain for {ns.task_id}:")
                for tid in chain:
                    print(f"  {tid}")
        except ValueError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        return 0

    if ns.command == "plan":
        plan = board.produce_execution_plan(ns.product_plan_path)
        print(f"[ok] Execution plan written: {board.plan_path}")
        print(f"     Tasks: {plan['total_tasks']}  Milestones: {plan['total_milestones']}")
        return 0

    if ns.command == "sync-from-plan":
        try:
            added = board.sync_from_execution_plan()
            print(f"[ok] Synced {added} task(s) from execution_plan.yaml -> task_board.yaml")
        except FileNotFoundError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main_cli())
