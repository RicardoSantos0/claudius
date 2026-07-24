"""
Prompt Assembler
Loads agent .md templates and injects scoped shared state.
Each agent receives ONLY the shared state fields it is authorized to read.
This prevents attention pollution and enforces information boundaries.
"""

import hashlib
import json
import re
import logging
from pathlib import Path
from typing import Any

import yaml

from core.utils.token_counter import TokenCounter
from core.engine.context_compressor import compress, estimate_tokens
from core.engine.agent_ids import normalize_agent_id

# Threshold (tokens) above which we compress the state projection before injection
_COMPRESSION_TOKEN_THRESHOLD = 2000

_token_counter = TokenCounter()

logger = logging.getLogger(__name__)

from core.paths import mas_root
ROOT = mas_root()
AGENTS_DIR = ROOT / "agents"

# Maps agent_id → list of state paths the agent may read
# Each path is "section.field" or "section" (all fields in section)
STATE_PROJECTIONS: dict[str, list[str]] = {
    "master_orchestrator": [
        "core_identity",
        "project_definition",
        "workflow",
        "decisions",
        "capability",
        "consultation",
        "evaluation",
        "_meta",
    ],
    "scribe_agent": [
        "core_identity",
        "workflow.current_owner",
        "workflow.handoff_history",
        "workflow.completed_phases",
        "decisions",
        "artifacts",
    ],
    "inquirer_agent": [
        "core_identity",
        "project_definition.original_brief",
        "project_definition.clarified_specification",
    ],
    "product_manager_agent": [
        "core_identity",
        "project_definition",
        "workflow.current_owner",
        "workflow.resource_requests",
    ],
    "project_manager_agent": [
        "core_identity",
        "project_definition",
        "workflow",
        "execution",
        "capability.reuse_candidates",
        "capability.capability_gap_certificates",
    ],
    "hr_agent": [
        "core_identity",
        "workflow.resource_requests",
        "workflow.resource_allocations",
        "capability",
    ],
    "evaluator_agent": [
        "core_identity",
        "project_definition",
        "workflow",
        "decisions",
        "artifacts",
        "evaluation",
        "capability.spawned_agents",
    ],
    "trainer_agent": [
        "core_identity",
        "evaluation",
        "workflow.completed_phases",
    ],
    "spawner_agent": [
        "core_identity",
        "capability.spawn_requests",
        "capability.spawned_agents",
        "capability.capability_gap_certificates",
    ],
}

# Consultant projections — only the consultation context the Master provides
CONSULTANT_PROJECTION = ["core_identity.project_id"]
for _c in ("risk_advisor", "quality_advisor", "devils_advocate",
           "domain_expert", "efficiency_advisor"):
    STATE_PROJECTIONS[_c] = CONSULTANT_PROJECTION


def _get_nested(data: dict, path: str) -> Any:
    """Get a value from nested dict by dot-notation path."""
    parts = path.split(".")
    node = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _project_state(state: dict, agent_id: str) -> dict:
    """
    Return a filtered view of state containing only fields
    the agent is authorized to read.
    """
    canonical_agent_id = normalize_agent_id(agent_id) or agent_id
    projection_paths = STATE_PROJECTIONS.get(canonical_agent_id, [])
    projected = {}

    for path in projection_paths:
        parts = path.split(".")
        if len(parts) == 1:
            # Full section
            section = parts[0]
            if section in state:
                projected[section] = state[section]
        elif len(parts) == 2:
            # Single field within section
            section, field = parts
            if section in state and field in state[section]:
                projected.setdefault(section, {})[field] = state[section][field]

    return projected


def _strip_empty(obj: Any) -> Any:
    """Recursively remove None values, empty strings, empty lists, and empty dicts."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            v2 = _strip_empty(v)
            if v2 is not None and v2 != "" and v2 != [] and v2 != {}:
                cleaned[k] = v2
        return cleaned or None
    if isinstance(obj, list):
        cleaned = [_strip_empty(i) for i in obj if _strip_empty(i) is not None]
        return cleaned or None
    return obj


def _compact_projection(projected: dict) -> dict:
    """
    Produce a lean version of the projected state for token efficiency.
    - Removes _meta section entirely (timestamps not useful in prompts)
    - Strips None/empty values recursively
    - Trims handoff_history to last 2 entries
    - Trims consultation_requests to last active entry
    """
    compact = dict(projected)

    # Drop _meta — agents don't need timestamps in prompts
    compact.pop("_meta", None)

    # Trim handoff history to most recent 2
    if "workflow" in compact and isinstance(compact["workflow"], dict):
        history = compact["workflow"].get("handoff_history")
        if isinstance(history, list) and len(history) > 2:
            compact["workflow"] = dict(compact["workflow"])
            compact["workflow"]["handoff_history"] = history[-2:]

    # Trim consultation requests to last 1
    if "consultation" in compact and isinstance(compact["consultation"], dict):
        reqs = compact["consultation"].get("consultation_requests")
        if isinstance(reqs, list) and len(reqs) > 1:
            compact["consultation"] = dict(compact["consultation"])
            compact["consultation"]["consultation_requests"] = reqs[-1:]

    return _strip_empty(compact) or {}


def _fill_placeholders(template: str, context: dict) -> str:
    """Replace {placeholder} markers with values from context."""
    def replacer(match):
        key = match.group(1).strip()
        val = context.get(key)
        if val is None:
            return match.group(0)  # Leave unfilled placeholders as-is
        if isinstance(val, (dict, list)):
            return yaml.dump(val, default_flow_style=False,
                             allow_unicode=True).strip()
        return str(val)

    return re.sub(r"\{([^}]+)\}", replacer, template)


class PromptAssembler:
    """
    Assembles agent system prompts by injecting scoped state context
    into .md templates.
    """

    def __init__(self, agents_dir: Path = AGENTS_DIR):
        self.agents_dir = agents_dir
        # Safe defaults for callers/tests that replace ``assemble`` while still
        # consuming the provider-neutral envelope.
        self.last_token_count = 0
        self.last_prompt_metadata: dict[str, Any] = {}

    def _db_template_path(self, agent_id: str) -> str | None:
        """Query mas_agents for template_path. Returns None on any error or miss."""
        try:
            from core.db import _get_connection, DB_PATH
            with _get_connection(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT template_path FROM mas_agents WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                if row and row["template_path"]:
                    return row["template_path"]
        except Exception as exc:
            logger.debug("DB template_path lookup failed; using filesystem: %s", exc)
        return None

    def get_template_path(self, agent_id: str) -> Path:
        canonical_agent_id = normalize_agent_id(agent_id) or agent_id
        # DB registry is primary source; filesystem is fallback
        db_path = self._db_template_path(canonical_agent_id)
        if db_path:
            p = Path(db_path)
            return p if p.is_absolute() else ROOT.parent / p
        return self.agents_dir / f"{canonical_agent_id}.md"

    def load_template(self, agent_id: str) -> str:
        """Load the raw .md template for an agent (strips YAML frontmatter)."""
        canonical_agent_id = normalize_agent_id(agent_id) or agent_id
        path = self.get_template_path(canonical_agent_id)
        if not path.exists():
            raise FileNotFoundError(f"Agent template not found: {path}")
        content = path.read_text(encoding="utf-8")
        # Strip YAML frontmatter (--- ... ---)
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip()
        return content

    def _authorized_skills(self, agent_id: str) -> list[dict]:
        """Return authorized skills for this agent as serializable dicts."""
        try:
            from core.engine.skill_bridge import SkillBridge
            return [s.to_dict() for s in SkillBridge().authorized_skills(agent_id)]
        except Exception:
            return []

    def _build_skill_access_block(self, agent_id: str, skills: list[dict]) -> str:
        """Human-readable skill access section appended to prompts."""
        if not skills:
            return (
                "## Skill Access\n"
                "Authorized skills: none\n"
                "If a skill is required, delegate to an authorized agent.\n"
            )

        names = ", ".join(s.get("name", "") for s in skills if s.get("name"))
        lines = [
            "## Skill Access",
            f"Authorized skills: {names}",
            "Use `/skill-name <query>` when a skill can improve speed or grounding.",
            "Reference skill outputs in your `art` list when relevant.",
        ]
        return "\n".join(lines) + "\n"

    def _build_recommended_skill_block(
        self,
        state: dict,
        extra_context: dict | None,
    ) -> str:
        """Render phase-aware required/recommended skill triggers."""
        try:
            from core.engine.skill_trigger import SkillTriggerPolicy
            project_id = state.get("core_identity", {}).get("project_id", "")
            if project_id:
                from core.utils.config import resolve_project_dir
                project_dir = resolve_project_dir(project_id, projects_root=ROOT / "projects")
            else:
                project_dir = None
            event = None
            changed_paths: list[str] = []
            status = None
            if extra_context:
                event = extra_context.get("runtime_event")
                raw_paths = extra_context.get("changed_paths", [])
                if isinstance(raw_paths, str):
                    changed_paths = [p.strip() for p in raw_paths.splitlines() if p.strip()]
                elif isinstance(raw_paths, list):
                    changed_paths = [str(p) for p in raw_paths]
                status = extra_context.get("runtime_status")
            policy = SkillTriggerPolicy()
            recs = policy.recommendations_for(
                state=state,
                project_dir=project_dir,
                event=str(event) if event else None,
                changed_paths=changed_paths,
                status=str(status) if status else None,
            )
            return policy.render_block(recs, project_id)
        except Exception:
            return ""

    def _append_missing_context_sections(
        self,
        prompt: str,
        template: str,
        context: dict[str, str],
    ) -> str:
        """
        Backward-compatible context injection.
        If templates don't define injected placeholders, append key context blocks.
        """
        sections: list[str] = []

        if "{injected_project_id}" not in template or "{injected_current_phase}" not in template:
            sections.append(
                "## Runtime Context\n"
                f"- project_id: {context.get('injected_project_id', '')}\n"
                f"- current_phase: {context.get('injected_current_phase', '')}"
            )

        if "{injected_shared_state}" not in template:
            sections.append(
                "## Scoped Shared State\n"
                f"{context.get('injected_shared_state', '')}".rstrip()
            )

        # Preserve inquirer behavior: no wire instruction injection.
        if "{injected_wire_instruction}" not in template:
            wire = context.get("injected_wire_instruction", "").strip()
            if wire:
                sections.append(wire)

        optional_keys = (
            "injected_consultation_question",
            "injected_consultation_context",
            "injected_consultation_synthesis",
            "injected_grounded_context",
            "injected_domain_context",
            "injected_recent_events",
            "injected_graph_context",
        )
        for key in optional_keys:
            if f"{{{key}}}" in template:
                continue
            val = (context.get(key) or "").strip()
            if not val:
                continue
            label = key.replace("injected_", "").replace("_", " ").title()
            sections.append(f"## {label}\n{val}")

        if not sections:
            return prompt
        return f"{prompt.rstrip()}\n\n" + "\n\n".join(sections) + "\n"

    def assemble(self, agent_id: str, state: dict,
                 extra_context: dict | None = None) -> str:
        """
        Assemble a complete prompt for an agent.
        Injects scoped state and any extra context.

        After assembly, self.last_token_count holds the estimated
        token count of the assembled prompt.
        """
        canonical_agent_id = normalize_agent_id(agent_id) or agent_id
        template = self.load_template(canonical_agent_id)
        stable_prefix = template.rstrip()
        projected = _project_state(state, canonical_agent_id)
        compact = _compact_projection(projected)

        # Compress large state projections to stay within token budget
        state_yaml = yaml.dump(compact, default_flow_style=False,
                               allow_unicode=True, sort_keys=False)
        if estimate_tokens(state_yaml) > _COMPRESSION_TOKEN_THRESHOLD:
            compact = compress(compact, mode="summary")
            state_yaml = yaml.dump(
                compact,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

        # Wire protocol instruction (agent-to-agent outputs only; never for human-facing)
        # Inquirer is excluded — its output is natural language for humans.
        _WIRE_INSTRUCTION = (
            "\n\n## Output Format\n"
            "For all agent-to-agent outputs (handoff payloads, consultation responses), "
            "use MAS wire protocol v1.0:\n"
            "- Status: compact code, e.g. `\"s\": \"task:complete\"`\n"
            "- Version: `\"_v\": \"1.0\"` in every payload\n"
            "- Omit empty lists and null fields\n"
            "- Optional reasoning (`rsn`): max 100 words\n"
            "- Human-facing text (CHECKPOINT.md, reports) uses expand() — stay structured here.\n"
        ) if canonical_agent_id != "inquirer_agent" else ""

        context = {
            "injected_project_id": state.get("core_identity", {}).get("project_id", ""),
            "injected_current_phase": state.get("core_identity", {}).get("current_phase", ""),
            "injected_shared_state": state_yaml,
            "injected_wire_instruction": _WIRE_INSTRUCTION,
        }

        # Add section-specific convenience keys
        if "workflow" in compact:
            context["injected_pending_items"] = yaml.dump(
                compact["workflow"].get("pending_assignments", []),
                default_flow_style=False, allow_unicode=True,
            )
            context["injected_recent_handoffs"] = yaml.dump(
                compact["workflow"].get("handoff_history", [])[-2:],
                default_flow_style=False, allow_unicode=True,
            )

        if "consultation" in compact:
            context["injected_active_consultation"] = yaml.dump(
                compact["consultation"].get("consultation_requests", [])[-1:],
                default_flow_style=False, allow_unicode=True,
            )

        if "project_definition" in compact:
            spec = compact["project_definition"].get("clarified_specification")
            context["injected_clarified_specification"] = (
                yaml.dump(spec, default_flow_style=False, allow_unicode=True)
                if spec else "(not yet available)"
            )
            context["injected_original_brief"] = (
                compact["project_definition"].get("original_brief") or "(not yet available)"
            )

        # Skill access context (authorization + runtime discoverability)
        authorized_skills = self._authorized_skills(canonical_agent_id)
        context["injected_authorized_skills"] = yaml.dump(
            authorized_skills, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        context["injected_authorized_skill_names"] = (
            ", ".join(s.get("name", "") for s in authorized_skills if s.get("name"))
            if authorized_skills else "(none)"
        )
        context["injected_recommended_skill_use"] = self._build_recommended_skill_block(
            state, extra_context
        )

        # Graph memory context injection (replaces part of state dump when available)
        # Only used when graph has ≥ 5 nodes — not enough data otherwise.
        graph_context = self._graph_context(canonical_agent_id, state)
        if graph_context:
            context["injected_graph_context"] = graph_context

        # SQLite recent-events injection — agents see what happened before them.
        # Phase is passed as the semantic search query: finds relevant past events
        # from the same phase across projects, not just the most recent ones.
        project_id = state.get("core_identity", {}).get("project_id", "")
        memory_query = self._memory_query(state)
        sqlite_ctx = self._sqlite_context(project_id, query=memory_query)
        if sqlite_ctx:
            context["injected_recent_events"] = sqlite_ctx

        if extra_context:
            context.update(extra_context)

        prompt = _fill_placeholders(template, context)
        prompt = self._append_missing_context_sections(prompt, template, context)
        if ("{injected_authorized_skills}" not in template and
                "{injected_authorized_skill_names}" not in template):
            prompt = f"{prompt.rstrip()}\n\n{self._build_skill_access_block(canonical_agent_id, authorized_skills)}"
        if ("{injected_recommended_skill_use}" not in template and
                context.get("injected_recommended_skill_use")):
            prompt = f"{prompt.rstrip()}\n\n{context['injected_recommended_skill_use']}\n"
        self.last_token_count: int = _token_counter.count(prompt)
        skill_text = self._build_skill_access_block(
            canonical_agent_id, authorized_skills
        )
        skill_text += context.get("injected_recommended_skill_use", "")
        memory_text = "\n".join(
            part for part in (graph_context, sqlite_ctx) if part
        )
        runtime_text = "\n".join(
            part
            for part in (
                context.get("injected_project_id", ""),
                context.get("injected_current_phase", ""),
                context.get("injected_wire_instruction", ""),
            )
            if part
        )
        components = {
            "static_prefix": _token_counter.count(stable_prefix),
            "state": _token_counter.count(state_yaml),
            "memory": _token_counter.count(memory_text),
            "skills": _token_counter.count(skill_text),
            "runtime": _token_counter.count(runtime_text),
        }
        components["unclassified"] = max(
            0, self.last_token_count - sum(components.values())
        )
        self.last_prompt_metadata: dict[str, Any] = {
            "schema_version": "1.0",
            "measurement": "heuristic_estimate",
            "billable": False,
            "cache_ready": True,
            "total_estimated_tokens": self.last_token_count,
            "components": components,
            "stable_prefix": {
                "sha256": hashlib.sha256(
                    stable_prefix.encode("utf-8")
                ).hexdigest(),
                "characters": len(stable_prefix),
                "estimated_tokens": components["static_prefix"],
            },
            "memory_query": memory_query,
        }
        return prompt

    @staticmethod
    def _memory_query(state: dict) -> str:
        """Build a bounded, project-specific FTS query for episodic recall."""
        ci = state.get("core_identity", {}) or {}
        definition = state.get("project_definition", {}) or {}
        raw = " ".join(
            str(value or "")
            for value in (
                ci.get("current_phase"),
                definition.get("target_area"),
                definition.get("project_goal"),
            )
        )
        stop = {
            "and", "the", "for", "with", "from", "into", "that", "this",
            "project", "current", "phase", "without",
        }
        terms: list[str] = []
        for token in re.findall(r"[A-Za-z0-9_-]{3,}", raw.lower()):
            if token in stop or token in terms:
                continue
            terms.append(token)
            if len(terms) >= 8:
                break
        return " OR ".join(terms)

    @staticmethod
    def _dedupe_memory_events(
        events: list[dict], *, cross_project: bool = False
    ) -> list[dict]:
        """Remove generic cross-project phase noise and repeated event summaries."""
        filtered: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            action = str(event.get("action_type") or "")
            intent = str(event.get("intent") or "").strip()
            if cross_project and action == "phase_transition":
                try:
                    payload = json.loads(event.get("payload") or "{}")
                except Exception:
                    payload = {}
                payload = payload.get("params", {}).get("inputs", payload)
                if payload.get("reconciled") or intent.lower().startswith(
                    "phase completed:"
                ):
                    continue
            signature = (action, intent.lower())
            if signature in seen:
                continue
            seen.add(signature)
            filtered.append(event)
            if len(filtered) >= 3:
                break
        return filtered

    def _sqlite_context(
        self,
        project_id: str,
        query: str = "",
        *,
        phase: str | None = None,
    ) -> str:
        """
        Query relevant agent events from SQLite for prompt injection.

        Strategy:
          1. Search locally with phase + project goal/target terms.
          2. Add recent local events when semantic recall is sparse.
          3. Use at most three deduplicated cross-project results, excluding generic
             reconciled phase-transition noise.

        Returns a compact formatted string, or "" if no events or DB unavailable.
        Never raises — all errors are swallowed to protect prompt assembly.
        """
        # ``phase`` is the legacy public keyword. Keep it as a compatibility
        # alias while new callers pass the richer bounded query.
        if phase is not None and not query:
            query = phase
        if not project_id:
            return ""
        try:
            from core.db import semantic_search, query_project_history, format_events_for_prompt
            local = (
                semantic_search(query, project_id=project_id, limit=5)
                if query
                else []
            )
            recent = query_project_history(project_id, limit=5)
            events = self._dedupe_memory_events([*local, *recent])
            if len(events) < 2 and query:
                cross = semantic_search(query, project_id=None, limit=8)
                other = [e for e in cross if e.get("project_id") != project_id]
                events.extend(
                    self._dedupe_memory_events(other, cross_project=True)
                )
                events = self._dedupe_memory_events(events)
            return format_events_for_prompt(events[:3])
        except Exception:
            return ""

    def _graph_context(self, agent_id: str, state: dict) -> str:
        """
        Inject agent graph context into the prompt.

        Strategy:
          1. Query ChromaDB-backed vector context when configured.
          2. Otherwise query agent_graph SQLite tables for this agent's node + direct edges.
        Returns a compact string or "" if unavailable.
        Never raises.
        """
        project_id = state.get("core_identity", {}).get("project_id", "")
        phase = state.get("core_identity", {}).get("current_phase", "")
        try:
            from core.runtime_config import query_vector_context
            vector_ctx = query_vector_context(project_id, agent_id, phase=phase)
            if vector_ctx:
                return vector_ctx
        except Exception as exc:
            logger.debug("vector-context query failed (non-blocking): %s", exc)

        try:
            from core.db import query_graph_node, query_graph_edges
            node = query_graph_node(agent_id)
            edges = query_graph_edges(agent_id, limit=5)
            if node or edges:
                lines = ["## Agent Graph Context"]
                if node:
                    lines.append(f"Node: {node.get('label', agent_id)} (type={node.get('type', '?')})")
                for e in edges:
                    rel = e.get("relation", "?")
                    other = e.get("target") if e.get("source") == agent_id else e.get("source")
                    lines.append(f"  → {rel} → {other}")
                return "\n".join(lines)
        except Exception:
            return ""
        return ""

    def get_state_projection(self, agent_id: str) -> list[str]:
        """Return the list of state paths this agent is authorized to read."""
        canonical_agent_id = normalize_agent_id(agent_id) or agent_id
        return STATE_PROJECTIONS.get(canonical_agent_id, [])
