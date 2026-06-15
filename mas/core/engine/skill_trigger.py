"""Skill trigger policy evaluator for MAS workflow skills."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate repo root (pyproject.toml not found)")


@dataclass(frozen=True)
class SkillRecommendation:
    rule_id: str
    skill: str
    required: bool
    reason: str


class SkillTriggerPolicy:
    """Evaluates mas/policies/skill_trigger_policy.yaml."""

    _POLICY_REL = Path("mas") / "policies" / "skill_trigger_policy.yaml"

    def __init__(self, policy_path: Path | None = None) -> None:
        self._repo_root = _find_repo_root()
        self._policy_path = policy_path or (self._repo_root / self._POLICY_REL)
        self._policy = self._load_policy()

    def _load_policy(self) -> dict:
        with self._policy_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def recommendations_for(
        self,
        *,
        state: dict,
        project_dir: Path | None = None,
        event: str | None = None,
        phase: str | None = None,
        changed_paths: list[str] | None = None,
        status: str | None = None,
    ) -> list[SkillRecommendation]:
        context = self._build_context(
            state=state,
            project_dir=project_dir,
            event=event,
            phase=phase,
            changed_paths=changed_paths or [],
            status=status,
        )
        rules = self._policy.get("skill_trigger_policy", {}).get("rules", [])
        recommendations: list[SkillRecommendation] = []
        for rule in rules:
            if not self._when_matches(rule.get("when", {}), context):
                continue
            rec = rule.get("recommend", {})
            recommendations.append(
                SkillRecommendation(
                    rule_id=str(rule.get("id", "")),
                    skill=str(rec.get("skill", "")),
                    required=bool(rec.get("required", False)),
                    reason=str(rec.get("reason", "")),
                )
            )
        return [rec for rec in recommendations if rec.skill]

    def render_block(self, recommendations: list[SkillRecommendation], project_id: str) -> str:
        if not recommendations:
            return ""
        lines = ["## Recommended Skill Use", "", "Before your next action, evaluate these triggers:"]
        for idx, rec in enumerate(recommendations, 1):
            label = "REQUIRED" if rec.required else "OPTIONAL"
            lines.append(f"{idx}. {label}: `/{rec.skill} {project_id}`")
            lines.append(f"   Reason: {rec.reason}")
        lines.append("")
        lines.append("If a REQUIRED skill applies, use it before producing a final decision.")
        lines.append("Record completed skills in `skill_used` / `sk_used`.")
        return "\n".join(lines)

    def _build_context(
        self,
        *,
        state: dict,
        project_dir: Path | None,
        event: str | None,
        phase: str | None,
        changed_paths: list[str],
        status: str | None,
    ) -> dict:
        project_id = state.get("core_identity", {}).get("project_id", "")
        resolved_project_dir = project_dir
        if resolved_project_dir is None and project_id:
            from core.utils.config import resolve_project_dir
            resolved_project_dir = resolve_project_dir(
                project_id, projects_root=self._repo_root / "mas" / "projects")
        return {
            "event": event,
            "phase": phase or state.get("core_identity", {}).get("current_phase", ""),
            "status": status or state.get("core_identity", {}).get("status", ""),
            "state": state,
            "project_dir": resolved_project_dir,
            "changed_paths": _normalise_paths(changed_paths),
        }

    def _when_matches(self, when: dict, context: dict) -> bool:
        if not when:
            return False
        for key, expected in when.items():
            if key == "event":
                if context.get("event") != expected:
                    return False
            elif key == "phase":
                if context.get("phase") != expected:
                    return False
            elif key == "status":
                if context.get("status") != expected:
                    return False
            elif key == "missing_artifact":
                project_dir = context.get("project_dir")
                if project_dir is None or (Path(project_dir) / str(expected)).exists():
                    return False
            elif key == "state_path_non_empty":
                value = _get_nested(context["state"], str(expected))
                if value in (None, "", [], {}):
                    return False
            elif key == "touched_paths_any":
                if not _matches_any(context["changed_paths"], expected or []):
                    return False
            else:
                return False
        return True


def _get_nested(data: dict, path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _normalise_paths(paths: list[str]) -> list[str]:
    return [str(p).replace("\\", "/").lstrip("./") for p in paths if p]


def _matches_any(paths: list[str], patterns: list[str]) -> bool:
    for path in paths:
        for pattern in patterns:
            if fnmatch.fnmatch(path, str(pattern).replace("\\", "/")):
                return True
    return False
