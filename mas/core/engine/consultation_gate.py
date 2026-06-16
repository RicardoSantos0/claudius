"""Consultation trigger policy evaluator for MAS orchestration gates."""

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


@dataclass(frozen=True)
class ConsultationRequirement:
    rule_id: str
    decision_type: str
    consultants: list[str]
    required: bool = True


class ConsultationGate:
    """Evaluates mas/policies/consultation_trigger_policy.yaml."""

    _POLICY_REL = Path("mas") / "policies" / "consultation_trigger_policy.yaml"

    def __init__(self, policy_path: Path | None = None) -> None:
        self._repo_root = _find_repo_root()
        self._policy_path = policy_path or (self._repo_root / self._POLICY_REL)
        self._policy = self._load_policy()

    def _load_policy(self) -> dict:
        with self._policy_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def required_for(
        self,
        *,
        state: dict,
        parsed: Any | None = None,
        changed_paths: list[str] | None = None,
        status: str | None = None,
    ) -> list[ConsultationRequirement]:
        """Return required consultation rules that match the current context."""
        context = self._build_context(state, parsed, changed_paths or [], status)
        rules = self._policy.get("consultation_trigger_policy", {}).get("required", [])
        matches: list[ConsultationRequirement] = []
        for rule in rules:
            if self._when_matches(rule.get("when", {}), context):
                matches.append(
                    ConsultationRequirement(
                        rule_id=str(rule.get("id", "")),
                        decision_type=str(rule.get("decision_type", "")),
                        consultants=[str(c) for c in rule.get("consultants", [])],
                        required=True,
                    )
                )
        return matches

    def has_valid_trigger(
        self,
        requirement: ConsultationRequirement,
        consultation_trigger: dict | None,
        state: dict,
    ) -> bool:
        """Return True if a matching consultation trigger or synthesis exists."""
        if self._has_synthesis(requirement, state):
            return True
        if not consultation_trigger:
            return False
        decision_type = str(consultation_trigger.get("decision_type", ""))
        if decision_type and decision_type != requirement.decision_type:
            return False
        selected = [str(c) for c in consultation_trigger.get("consultants", [])]
        return set(requirement.consultants).issubset(set(selected))

    def _build_context(
        self,
        state: dict,
        parsed: Any | None,
        changed_paths: list[str],
        status: str | None,
    ) -> dict:
        capability = state.get("capability", {})
        artifacts = list(changed_paths)
        if parsed is not None:
            artifacts.extend(str(a) for a in getattr(parsed, "artifacts", []) if a)

        next_agent = getattr(parsed, "next_agent", "") if parsed is not None else ""
        parsed_status = getattr(parsed, "status", "") if parsed is not None else ""
        state_status = state.get("core_identity", {}).get("status", "")
        current_phase = state.get("core_identity", {}).get("current_phase", "")

        return {
            "changed_paths": _normalise_paths(artifacts),
            "changed_files_count": len(set(artifacts)),
            "status": status or parsed_status or state_status,
            "phase": current_phase,
            "has_spawn_request": bool(capability.get("spawn_requests"))
            or str(next_agent) == "spawner_agent",
            "has_gap_certificate": bool(capability.get("capability_gap_certificates"))
            or any("gap_certificate" in p or "gap-cert" in p for p in artifacts),
        }

    def _when_matches(self, when: dict, context: dict) -> bool:
        if not when:
            return False
        if "any" in when:
            return any(self._when_matches(item, context) for item in when.get("any", []))
        for key, expected in when.items():
            if key == "touched_paths_any":
                if not _matches_any(context["changed_paths"], expected or []):
                    return False
            elif key == "changed_files_count_gte":
                if context["changed_files_count"] < int(expected):
                    return False
            elif key in {"has_spawn_request", "has_gap_certificate"}:
                if bool(context.get(key)) is not bool(expected):
                    return False
            elif key == "status":
                if context.get("status") != expected:
                    return False
            elif key == "phase":
                if context.get("phase") != expected:
                    return False
            else:
                return False
        return True

    def _has_synthesis(self, requirement: ConsultationRequirement, state: dict) -> bool:
        syntheses = state.get("consultation", {}).get("synthesis", [])
        if not isinstance(syntheses, list):
            return False
        for item in syntheses:
            if not isinstance(item, dict):
                continue
            if item.get("rule_id") == requirement.rule_id:
                return True
            if item.get("decision_type") == requirement.decision_type:
                return True
        return False


def _normalise_paths(paths: list[str]) -> list[str]:
    return [str(p).replace("\\", "/").lstrip("./") for p in paths if p]


def _matches_any(paths: list[str], patterns: list[str]) -> bool:
    for path in paths:
        for pattern in patterns:
            pat = str(pattern).replace("\\", "/")
            if fnmatch.fnmatch(path, pat):
                return True
    return False
