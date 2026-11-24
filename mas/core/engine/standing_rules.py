"""Standing-rule register: rules a project's agents must obey in every dispatch.

The register is an optional ``governance/standing_rules.yaml`` in a project
directory. Each entry has ``id``, ``text`` (one line, at most 200 characters),
``scope`` (``agents``, optional ``units``), ``check`` (a test id, a grep pattern
or ``review``), ``status`` (``active`` or ``retired``) and ``retired_by``. An
entry that points at a contract or style document also carries ``path`` and
``sha256``. A malformed register raises ``StandingRulesError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REGISTER_RELATIVE = Path("governance") / "standing_rules.yaml"
MAX_TEXT = 200
BLOCK_CAP = 4000
_STATUSES = ("active", "retired")


class StandingRulesError(ValueError):
    """The register is malformed."""


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    agents: tuple[str, ...]
    units: tuple[str, ...]
    check: str
    status: str
    retired_by: str | None
    path: str | None = None
    sha256: str | None = None


def _fail(where: str, why: str) -> StandingRulesError:
    return StandingRulesError(f"standing rules {where}: {why}")


def _str_list(value, where: str, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise _fail(where, f"{field} must be a list of non-empty strings")
    return tuple(value)


def _parse_entry(entry, index: int, seen: set[str]) -> Rule:
    where = f"entry {index}"
    if not isinstance(entry, dict):
        raise _fail(where, "must be a mapping")
    rule_id = entry.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise _fail(where, "id is required")
    where = f"entry {rule_id!r}"
    if rule_id in seen:
        raise _fail(where, "duplicate id")
    seen.add(rule_id)
    text = entry.get("text")
    if not isinstance(text, str) or not text.strip() or "\n" in text.strip():
        raise _fail(where, "text must be one non-empty line")
    if len(text) > MAX_TEXT:
        raise _fail(where, f"text is {len(text)} characters, the limit is {MAX_TEXT}")
    scope = entry.get("scope")
    if not isinstance(scope, dict) or "agents" not in scope:
        raise _fail(where, "scope.agents is required")
    agents = _str_list(scope["agents"], where, "scope.agents")
    units = _str_list(scope["units"], where, "scope.units") if scope.get("units") else ()
    check = entry.get("check")
    if not isinstance(check, str) or not check.strip():
        raise _fail(where, "check is required (a test id, a grep pattern or 'review')")
    status = entry.get("status")
    if status not in _STATUSES:
        raise _fail(where, f"status must be one of {_STATUSES}")
    retired_by = entry.get("retired_by")
    if status == "retired" and not retired_by:
        raise _fail(where, "a retired rule needs retired_by")
    if status == "active" and retired_by:
        raise _fail(where, "an active rule must not carry retired_by")
    path, sha = entry.get("path"), entry.get("sha256")
    if bool(path) != bool(sha):
        raise _fail(where, "path and sha256 go together")
    return Rule(rule_id, text, agents, units, check, status,
                str(retired_by) if retired_by else None, path, sha)


def load_register(path: Path) -> list[Rule]:
    """Load and validate the register; a missing file means no rules."""
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fail(str(path), f"not valid YAML ({exc})") from exc
    entries = (data or {}).get("rules") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise _fail(str(path), "top level must be a mapping with a 'rules' list")
    seen: set[str] = set()
    return [_parse_entry(e, i, seen) for i, e in enumerate(entries)]


def rules_for_agent(rules: list[Rule], agent_id: str) -> list[Rule]:
    """Active rules whose scope names this agent (or '*')."""
    return [r for r in rules
            if r.status == "active" and ("*" in r.agents or agent_id in r.agents)]


def _line(rule: Rule) -> str:
    units = f" [{', '.join(rule.units)}]" if rule.units else ""
    doc = f" ({rule.path}, sha256 {rule.sha256})" if rule.path else ""
    return f"- {rule.id}{units}: {rule.text}{doc}"


def render_block(rules: list[Rule], register_path: str, cap: int = BLOCK_CAP) -> str:
    """The Standing Rules section. Rule text stops at the cap; the rest are listed by id
    with the register path, so an agent can still cite every in-scope id."""
    if not rules:
        return ""
    lines, used = [], 0
    for rule in rules:
        line = _line(rule)
        if used + len(line) + 1 > cap:
            break
        lines.append(line)
        used += len(line) + 1
    out = "## Standing Rules\nThese bind you in every dispatch. Cite each rule id you checked.\n"
    out += "\n".join(lines)
    rest = rules[len(lines):]
    if rest:
        out += (f"\n- {len(rest)} more rules, ids only (text in {register_path}): "
                + ", ".join(r.id for r in rest))
    return out
