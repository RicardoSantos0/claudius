"""Skill trigger policy evaluator for MAS workflow skills."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _find_repo_root() -> Path:
    # Repo root in a clone, or the $MAS_HOME workspace root when pip-installed.
    from core.paths import repo_root
    return repo_root()


# How an agent on a runtime with no skill loader asks for a skill. ResponseParser takes
# the last fenced code block of a response that holds a JSON object as its wire block,
# and reads "skill_request" (or its short form "sk_req") from it: an object naming the
# skill in "name" (or "skill"), with an optional "query".
# OrchestrationLoop._handle_skill_request then authorizes the request, audits it, and
# carries the skill's text into the next step's prompt under Grounded Context. The loop
# skips the decisions, handoffs and phase changes of a response that carries one. That
# promise holds for the master and for a single sub-agent only: a consultation is one
# exchange whose answer is never read for a skill_request, and parallel sub-agents share
# one pending slot. The prompt assembler gives those prompts another sentence instead.
SKILL_REQUEST_FIELD = '"skill_request": {"name": "<skill>", "query": "<what you need it for>"}'
SKILL_REQUEST_HINT = (
    "To use an authorized skill whose text is not in this prompt, end your response "
    "with your wire block, a JSON object in a ```json fence, carrying "
    f"`{SKILL_REQUEST_FIELD}`. Its text then arrives "
    "on your next step, under Grounded Context. MAS does not act on the decisions, "
    "handoffs or phase changes in a response that carries a skill_request, so send the "
    "request on its own and answer on the next step."
)


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

    @staticmethod
    def render_block(
        recommendations: list[SkillRecommendation],
        project_id: str,
        *,
        inline: bool = False,
        inlined: list[str] | tuple[str, ...] | set[str] | None = None,
        delivered: list[str] | tuple[str, ...] | set[str] | None = None,
        request_note: str = SKILL_REQUEST_HINT,
    ) -> str:
        """Render recommendations as a prompt block.

        ``inline`` is for a runtime with no skill loader (the AgentRunner API adapters):
        the block then names each skill without a slash command, because that runtime
        has none to run. The block says a skill's text is included only when the
        prompt carries it: ``inlined`` names the skills under Inlined Skills, and
        ``delivered`` the skills whose full text a skill_request brought in under
        Grounded Context. ``request_note`` says how, or whether, the agent can ask for
        any other skill; its default is the skill_request hint, and a caller whose
        runtime answers no skill_request passes a sentence that says so.
        """
        if not recommendations:
            return ""
        included = set(inlined or ())
        requested = set(delivered or ())
        lines = ["## Recommended Skill Use", "", "Before your next action, evaluate these triggers:"]
        for idx, rec in enumerate(recommendations, 1):
            label = "REQUIRED" if rec.required else "OPTIONAL"
            if inline:
                if rec.skill in requested:
                    where = " (text included under Grounded Context)"
                elif rec.skill in included:
                    where = " (text included under Inlined Skills)"
                else:
                    where = " (text not in this prompt)"
                lines.append(f"{idx}. {label}: `{rec.skill}`{where}")
            else:
                lines.append(f"{idx}. {label}: `/{rec.skill} {project_id}`")
            lines.append(f"   Reason: {rec.reason}")
        lines.append("")
        if inline:
            if any(rec.required and rec.skill in included | requested
                   for rec in recommendations):
                lines.append(
                    "Apply each REQUIRED skill whose text is included before producing "
                    "a final decision."
                )
            if request_note:
                lines.append(request_note)
        else:
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
