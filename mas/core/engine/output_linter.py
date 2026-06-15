"""
MAS Output Linter

Detects verbose, malformed, or non-compliant agent outputs.
Records findings as output_lint events in episodic.db via EventRecorder.
Lint codes are stable identifiers — do not rename once published.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Stable lint codes
LINT_VERBOSE = "MAS.OUTPUT.VERBOSE"
LINT_MISSING_WIRE = "MAS.OUTPUT.MISSING_WIRE"
LINT_TOO_MANY_SECTIONS = "MAS.OUTPUT.TOO_MANY_SECTIONS"
LINT_REPEATS_POLICY = "MAS.OUTPUT.REPEATS_POLICY"
LINT_RSN_TOO_LONG = "MAS.OUTPUT.RSN_TOO_LONG"

_POLICY_PHRASES = [
    "wire protocol",
    "handoff_engine.py",
    "shared_state_manager.py append",
    "uv run python mas/core",
]

_WIRE_MARKERS = ['"s":', '"_v":', '"next_action":', '"rsn":']


@dataclass
class LintResult:
    passed: bool
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return bool(self.findings)


class OutputLinter:
    """Checks agent output text for verbosity and compliance issues."""

    def __init__(self, max_rsn_words: int = 50, max_sections: int = 7) -> None:
        self._max_rsn_words = max_rsn_words
        self._max_sections = max_sections

    def lint(self, output: str, agent_id: str = "", project_id: str = "") -> LintResult:
        """Run all lint checks. Returns LintResult with any findings."""
        findings: list[dict[str, Any]] = []

        findings.extend(self._check_verbose(output, agent_id))
        findings.extend(self._check_missing_wire(output, agent_id))
        findings.extend(self._check_too_many_sections(output, agent_id))
        findings.extend(self._check_repeats_policy(output, agent_id))
        findings.extend(self._check_rsn_length(output, agent_id))

        return LintResult(passed=len(findings) == 0, findings=findings)

    def _check_verbose(self, output: str, agent_id: str) -> list[dict]:
        word_count = len(output.split())
        if word_count > 800:
            return [{"code": LINT_VERBOSE, "agent": agent_id,
                     "detail": f"Output is {word_count} words (threshold: 800)"}]
        return []

    def _check_missing_wire(self, output: str, agent_id: str) -> list[dict]:
        has_wire = any(marker in output for marker in _WIRE_MARKERS)
        if not has_wire:
            return [{"code": LINT_MISSING_WIRE, "agent": agent_id,
                     "detail": "No wire protocol markers found in output"}]
        return []

    def _check_too_many_sections(self, output: str, agent_id: str) -> list[dict]:
        sections = re.findall(r"^#{1,3} .+", output, re.MULTILINE)
        if len(sections) > self._max_sections:
            return [{"code": LINT_TOO_MANY_SECTIONS, "agent": agent_id,
                     "detail": f"{len(sections)} sections (threshold: {self._max_sections})"}]
        return []

    def _check_repeats_policy(self, output: str, agent_id: str) -> list[dict]:
        lower = output.lower()
        hits = [p for p in _POLICY_PHRASES if p.lower() in lower]
        if len(hits) >= 2:
            return [{"code": LINT_REPEATS_POLICY, "agent": agent_id,
                     "detail": f"Repeated policy phrases detected: {hits}"}]
        return []

    def _check_rsn_length(self, output: str, agent_id: str) -> list[dict]:
        match = re.search(r'"rsn"\s*:\s*"([^"]*)"', output)
        if match:
            words = len(match.group(1).split())
            if words > self._max_rsn_words:
                return [{"code": LINT_RSN_TOO_LONG, "agent": agent_id,
                         "detail": f"rsn is {words} words (threshold: {self._max_rsn_words})"}]
        return []


def check_wire_compliance(payload: dict) -> tuple[float, list[str]]:
    """Return (score, issues) for a single payload dict.

    Score 1.0 — both ``_v`` and ``s`` present.
    Score 0.5 — exactly one of ``_v`` or ``s`` present.
    Score 0.0 — neither present.
    """
    has_v = "_v" in payload
    has_s = "s" in payload
    issues: list[str] = []
    if not has_v:
        issues.append("Missing field '_v' (wire version marker)")
    if not has_s:
        issues.append("Missing field 's' (wire signal/summary)")
    if has_v and has_s:
        return 1.0, issues
    if has_v or has_s:
        return 0.5, issues
    return 0.0, issues


def wire_compliance_rate(payloads: list[dict]) -> float:
    """Return the average wire compliance score across *payloads*.

    Returns 0.0 for an empty list.
    """
    if not payloads:
        return 0.0
    return sum(check_wire_compliance(p)[0] for p in payloads) / len(payloads)


def check_phase_close_wire(payload: dict) -> tuple[bool, list[str]]:
    """Validates that a Scribe phase-close wire payload has required fields.

    Returns (is_valid, list_of_errors).
    Required fields: _v, s, art
    """
    errors = []
    if "_v" not in payload:
        errors.append("Missing required field: _v (wire protocol version)")
    if "s" not in payload:
        errors.append("Missing required field: s (status code)")
    if "art" not in payload:
        errors.append("Missing required field: art (artifact list)")
    elif not isinstance(payload["art"], list):
        errors.append("Field 'art' must be a list")
    return (len(errors) == 0, errors)
