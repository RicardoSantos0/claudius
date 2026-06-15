"""
Intake Checker
Analyzes project specifications for completeness and computes quality scores.
Used by the Inquirer Agent to determine when a spec is ready for handoff.

Quality score formula:
    score = (required_count/7 * 0.7) + (recommended_count/5 * 0.3)
    Threshold for handoff: >= 0.85

Required items (7):
    project_goal, problem_statement, scope_inclusions, scope_exclusions,
    constraints, success_criteria, expected_outputs

Recommended items (5):
    stakeholders, dependencies, timeline_expectations,
    quality_expectations, prior_art

Usage as library:
    from core.engine.intake_checker import IntakeChecker
    checker = IntakeChecker()
    result = checker.analyze(spec)

Usage as CLI:
    uv run python mas/core/engine/intake_checker.py analyze --spec-json '{...}'
    uv run python mas/core/engine/intake_checker.py score --spec-json '{...}'
    uv run python core/intake_checker.py record-qa --project-id proj-001 --round 1 --qa-json '[...]'
    uv run python core/intake_checker.py write-spec --project-id proj-001 --spec-json '{...}'
"""

import sys
import json
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent.parent.parent

REQUIRED_FIELDS = [
    "project_goal",
    "problem_statement",
    "scope_inclusions",   # spec.scope.inclusions
    "scope_exclusions",   # spec.scope.exclusions
    "constraints",
    "success_criteria",
    "expected_outputs",
]

RECOMMENDED_FIELDS = [
    "stakeholders",
    "dependencies",
    "timeline_expectations",
    "quality_expectations",
    "prior_art",
]

QUALITY_THRESHOLD = 0.85


@dataclass
class CompletenessResult:
    complete: bool
    score: float
    required_present: list[str]
    required_missing: list[str]
    recommended_present: list[str]
    recommended_missing: list[str]
    ambiguous: list[str]
    ready_for_handoff: bool


def _is_present(value: Any) -> bool:
    """Return True if a value is non-empty / non-null."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _extract_field(spec: dict, field_name: str) -> Any:
    """Extract a possibly-nested field from the spec dict."""
    # Handle scope sub-fields
    if field_name == "scope_inclusions":
        return spec.get("scope", {}).get("inclusions")
    if field_name == "scope_exclusions":
        return spec.get("scope", {}).get("exclusions")
    return spec.get(field_name)


class IntakeChecker:
    """
    Analyzes project specifications for completeness and readiness.
    """

    def analyze(self, spec: dict) -> CompletenessResult:
        """
        Analyze a specification dict for completeness.
        Returns a CompletenessResult with score and gap lists.
        """
        required_present = []
        required_missing = []
        recommended_present = []
        recommended_missing = []
        ambiguous = []

        for f in REQUIRED_FIELDS:
            val = _extract_field(spec, f)
            if _is_present(val):
                required_present.append(f)
            else:
                required_missing.append(f)

        for f in RECOMMENDED_FIELDS:
            val = _extract_field(spec, f)
            if _is_present(val):
                recommended_present.append(f)
            else:
                recommended_missing.append(f)

        # Flag potentially ambiguous values (very short strings)
        for f in REQUIRED_FIELDS + RECOMMENDED_FIELDS:
            val = _extract_field(spec, f)
            if isinstance(val, str) and 0 < len(val.strip()) < 10:
                ambiguous.append(f)

        r = len(required_present)
        rec = len(recommended_present)
        score = round((r / 7 * 0.7) + (rec / 5 * 0.3), 4)
        complete = len(required_missing) == 0
        ready = score >= QUALITY_THRESHOLD

        return CompletenessResult(
            complete=complete,
            score=score,
            required_present=required_present,
            required_missing=required_missing,
            recommended_present=recommended_present,
            recommended_missing=recommended_missing,
            ambiguous=ambiguous,
            ready_for_handoff=ready,
        )

    def generate_questions(self, result: CompletenessResult,
                           round_number: int,
                           max_questions: int = 7) -> list[dict]:
        """
        Generate targeted clarification questions from a completeness result.
        Returns up to max_questions questions in priority order.
        round_number: 1, 2, or 3 (max 3 rounds allowed).
        """
        if round_number > 3:
            return []

        questions = []
        templates = _QUESTION_TEMPLATES

        # Priority: missing required fields first
        for field_name in result.required_missing:
            if field_name in templates and len(questions) < max_questions:
                questions.append({
                    "field": field_name,
                    "type": "required",
                    "question": templates[field_name],
                    "round": round_number,
                })

        # Then ambiguous fields
        for field_name in result.ambiguous:
            if field_name in templates and len(questions) < max_questions:
                if not any(q["field"] == field_name for q in questions):
                    questions.append({
                        "field": field_name,
                        "type": "ambiguous",
                        "question": f"Can you expand on '{field_name}'? The current value seems brief.",
                        "round": round_number,
                    })

        # Then missing recommended fields (if space)
        for field_name in result.recommended_missing:
            if field_name in templates and len(questions) < max_questions:
                questions.append({
                    "field": field_name,
                    "type": "recommended",
                    "question": templates[field_name],
                    "round": round_number,
                })

        return questions[:max_questions]

    def apply_answers(self, spec: dict, qa_round: list[dict]) -> dict:
        """
        Apply Q&A answers back into the spec dict.
        Each entry in qa_round: {field, question, answer, resolved: bool}
        Returns updated spec.
        """
        updated = dict(spec)
        for entry in qa_round:
            field_name = entry.get("field", "")
            answer = entry.get("answer")
            if not answer or entry.get("resolved") is False:
                continue

            if field_name == "scope_inclusions":
                updated.setdefault("scope", {})["inclusions"] = (
                    answer if isinstance(answer, list) else [answer]
                )
            elif field_name == "scope_exclusions":
                updated.setdefault("scope", {})["exclusions"] = (
                    answer if isinstance(answer, list) else [answer]
                )
            else:
                updated[field_name] = answer

        return updated

    def record_qa(self, project_id: str, round_number: int,
                  qa_entries: list[dict]) -> Path:
        """Write a Q&A round to the project's intake folder."""
        from core.utils.config import resolve_project_dir
        intake_dir = resolve_project_dir(project_id, projects_root=ROOT / "projects") / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        qa_path = intake_dir / "clarification_qa.yaml"

        existing = []
        if qa_path.exists():
            with open(qa_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                existing = data.get("rounds", [])

        existing.append({
            "round": round_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entries": qa_entries,
        })

        with open(qa_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"project_id": project_id, "rounds": existing},
                f, default_flow_style=False, allow_unicode=True, sort_keys=False,
            )
        return qa_path

    def write_spec(self, project_id: str, spec: dict,
                   result: CompletenessResult) -> Path:
        """Write the clarified specification to the project's intake folder."""
        from core.utils.config import resolve_project_dir
        intake_dir = resolve_project_dir(project_id, projects_root=ROOT / "projects") / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        spec_path = intake_dir / "clarified_spec.yaml"

        output = {
            "project_id": project_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "inquirer_agent",
            "quality_score": result.score,
            "ready_for_handoff": result.ready_for_handoff,
            "specification": spec,
            "unresolved": result.required_missing + result.ambiguous,
        }

        with open(spec_path, "w", encoding="utf-8") as f:
            yaml.dump(output, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        return spec_path


# --- QUESTION TEMPLATES ---

_QUESTION_TEMPLATES = {
    "project_goal": (
        "What is the desired outcome of this project? "
        "What should be different or better when it is done?"
    ),
    "problem_statement": (
        "What specific problem does this project solve? "
        "Who experiences this problem and how often?"
    ),
    "scope_inclusions": (
        "What is explicitly in scope for this project? "
        "List the specific deliverables, features, or outcomes you expect."
    ),
    "scope_exclusions": (
        "What is explicitly out of scope? "
        "What should this project NOT do or produce?"
    ),
    "constraints": (
        "What constraints apply? "
        "(e.g., budget limits, technology requirements, compliance rules, team size)"
    ),
    "success_criteria": (
        "How will you know this project succeeded? "
        "What specific, measurable outcomes would confirm success?"
    ),
    "expected_outputs": (
        "What specific deliverables do you expect? "
        "(e.g., a working system, a report, a set of scripts, a trained model)"
    ),
    "stakeholders": (
        "Who are the key stakeholders? "
        "Who cares about the outcome and who will review or use the deliverables?"
    ),
    "dependencies": (
        "What does this project depend on? "
        "(e.g., existing systems, data sources, external APIs, other projects)"
    ),
    "timeline_expectations": (
        "Are there any time constraints or target completion dates?"
    ),
    "quality_expectations": (
        "What quality bar applies? "
        "(e.g., production-ready, prototype, internal tool, specific test coverage)"
    ),
    "prior_art": (
        "Has anything similar been attempted before? "
        "Are there existing systems or attempts we should build on or avoid repeating?"
    ),
}


# --- CLI ---

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="intake_checker", description="Intake Checker CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # analyze
    a = sub.add_parser("analyze", help="Analyze spec completeness")
    a.add_argument("--spec-json", required=True, help="Spec dict as JSON string")

    # score
    s = sub.add_parser("score", help="Just compute the quality score")
    s.add_argument("--spec-json", required=True)

    # questions
    q = sub.add_parser("questions", help="Generate clarification questions")
    q.add_argument("--spec-json", required=True)
    q.add_argument("--round", type=int, default=1)
    q.add_argument("--max", type=int, default=7, dest="max_questions")

    # record-qa
    r = sub.add_parser("record-qa", help="Record a Q&A round to disk")
    r.add_argument("--project-id", required=True)
    r.add_argument("--round", type=int, required=True)
    r.add_argument("--qa-json", required=True, help="Q&A entries as JSON array")

    # write-spec
    w = sub.add_parser("write-spec", help="Write clarified spec to disk")
    w.add_argument("--project-id", required=True)
    w.add_argument("--spec-json", required=True)

    return p


def main_cli(args=None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(args)
    checker = IntakeChecker()

    if ns.command in ("analyze", "score", "questions"):
        spec = json.loads(ns.spec_json)
        result = checker.analyze(spec)

        if ns.command == "score":
            print(f"{result.score:.4f}")

        elif ns.command == "analyze":
            out = {
                "complete": result.complete,
                "score": result.score,
                "ready_for_handoff": result.ready_for_handoff,
                "required_missing": result.required_missing,
                "recommended_missing": result.recommended_missing,
                "ambiguous": result.ambiguous,
            }
            print(yaml.dump(out, default_flow_style=False, allow_unicode=True))

        elif ns.command == "questions":
            questions = checker.generate_questions(result, ns.round, ns.max_questions)
            print(yaml.dump(questions, default_flow_style=False, allow_unicode=True))

    elif ns.command == "record-qa":
        entries = json.loads(ns.qa_json)
        path = checker.record_qa(ns.project_id, ns.round, entries)
        print(f"OK {path}")

    elif ns.command == "write-spec":
        spec = json.loads(ns.spec_json)
        result = checker.analyze(spec)
        path = checker.write_spec(ns.project_id, spec, result)
        print(f"OK {path} score={result.score:.4f} ready={result.ready_for_handoff}")

    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
