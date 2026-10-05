"""
Prompt Assembler
Loads agent .md templates and injects scoped shared state.
Each agent receives ONLY the shared state fields it is authorized to read.
This prevents attention pollution and enforces information boundaries.
"""

import hashlib
import json
import os
import re
import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from core.utils.token_counter import TokenCounter
from core.engine.context_compressor import compress, estimate_tokens
from core.engine.agent_ids import normalize_agent_id
from core.engine.skill_trigger import SKILL_REQUEST_HINT

# Threshold (tokens) above which we compress the state projection before injection
_COMPRESSION_TOKEN_THRESHOLD = 2000

# Skill delivery (IOP-22). A client with a skill loader of its own (Claude Code, Copilot,
# opencode, or any MCP client through the mas_skill tool) loads a skill when the agent asks
# for one. The AgentRunner API adapters make no tool calls, so a prompt sent through them
# can name no command the agent could run. Callers on that path pass
# extra_context={SKILL_DELIVERY_KEY: SKILL_DELIVERY_INLINE}, and the assembler then includes
# the text of each REQUIRED skill the agent is authorized for, after the stable prefix, as
# far as the caps below allow. Any other skill reaches the master or a single sub-agent
# through a skill_request in its wire block, which the orchestration loop answers on that
# agent's next step; SKILL_REQUEST_KEY covers the exchanges where it does not.
SKILL_DELIVERY_KEY = "skill_delivery"
SKILL_DELIVERY_INLINE = "inline"

# Per-skill bound on inlined SKILL.md text, about 2,000 tokens. Every REQUIRED skill in the
# shipped trigger policy is under 3,500 characters and inlines whole; the bound stops a long
# skill that is later marked REQUIRED from flooding every API-runtime prompt.
INLINE_SKILL_CHAR_CAP = 8000

# Bound on all inlined skill text in one prompt, about 3,000 tokens. The shipped REQUIRED
# skills run 2,300 to 3,400 characters, so four inline whole when they fire together. A
# skill that would cross the bound is named with its file instead, and with a
# skill_request where MAS answers one.
INLINE_TOTAL_CHAR_CAP = 12000

# Whether MAS answers a skill_request sent from this exchange. The orchestration loop
# answers one from the master or from a single sub-agent, on that agent's next step. It
# answers none from a consultation, which is one exchange whose answer it never reads
# for a skill_request. Parallel sub-agents share one pending slot, so a request from one
# of them can be lost or reach another agent. A caller may set this key to False; the
# assembler also infers False for a consultation prompt (one that carries
# injected_consultation_question) and for a prompt assembled on a parallel dispatch
# thread. Only inline delivery reads it.
SKILL_REQUEST_KEY = "skill_request_supported"

# OrchestrationLoop._dispatch_agents_parallel runs each agent on a pool thread whose
# name starts with this prefix (its ThreadPoolExecutor thread_name_prefix).
_PARALLEL_THREAD_PREFIX = "mas_parallel"

NO_SKILL_REQUEST_CONSULTATION = (
    "A consultation is a single exchange, and MAS reads no skill_request from a "
    "consultation answer, so no skill text beyond what this prompt includes can reach "
    "you. Answer from what is here."
)
NO_SKILL_REQUEST_PARALLEL = (
    "You run in parallel with other agents in this step, and MAS cannot make sure that "
    "a skill_request reaches the agent that sent it, so do not send one. Work from the "
    "skill text this prompt includes."
)
NO_SKILL_REQUEST = (
    "MAS answers no skill_request in this exchange, so no skill text beyond what this "
    "prompt includes can reach you."
)

# The orchestration loop hands a requested skill on as "[skill_prompt:<name>]" and the
# prompt SkillBridge.render_skill_prompt built, at the start of injected_grounded_context.
_SKILL_PROMPT_MARKER = re.compile(r"\[skill_prompt:([^\]\r\n]+)\]\r?\n")

# Markdown blocks that would swallow a truncation notice placed after a cut through them
# (CommonMark 0.31). A fenced code block opens and closes on a run of three or more
# backticks or tildes.
_FENCE_LINE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
# A list item marker: a bullet, or one to nine digits and a dot or parenthesis, then
# whitespace or the end of the line.
_LIST_ITEM = re.compile(r"^([ \t]*)([-+*]|\d{1,9}[.)])([ \t]+|$)")
# An ATX heading, read from a line's first non-blank character.
_HEADING = re.compile(r"#{1,6}(?:[ \t]|$)")
# HTML blocks of types 1 to 5 run until a line holds their end marker, blank lines and
# all; types 6 and 7 end at a blank line. Each entry: start, end, closing text to emit.
_HTML_RAW_START = re.compile(r"<(script|pre|style|textarea)(?=[ \t>]|$)", re.I)
_HTML_RAW_END = re.compile(r"</(?:script|pre|style|textarea)>", re.I)
_HTML_MARKED = (
    (re.compile(r"<!--"), re.compile(r"-->"), "-->"),
    (re.compile(r"<\?"), re.compile(r"\?>"), "?>"),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>"), "]]>"),
    (re.compile(r"<![A-Za-z]"), re.compile(r">"), ">"),
)
_HTML_BLOCK_TAGS = frozenset(
    "address article aside base basefont blockquote body caption center col colgroup "
    "dd details dialog dir div dl dt fieldset figcaption figure footer form frame "
    "frameset h1 h2 h3 h4 h5 h6 head header hr html iframe legend li link main menu "
    "menuitem nav noframes ol optgroup option p param search section summary table "
    "tbody td tfoot th thead title tr track ul".split()
)
_HTML_TAG_START = re.compile(r"</?([A-Za-z][A-Za-z0-9-]*)(?=[ \t/>]|$)")
_HTML_LONE_TAG = re.compile(
    r"(?:<[A-Za-z][A-Za-z0-9-]*"
    r"(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*"
    r"(?:[ \t]*=[ \t]*(?:[^ \t\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
    r"[ \t]*/?>|</[A-Za-z][A-Za-z0-9-]*[ \t]*>)[ \t]*")
# Leading whitespace and an optional list item marker: what precedes a block's opener.
_OPENER_PREFIX = re.compile(r"^[ \t]*(?:(?:[-+*]|\d{1,9}[.)])[ \t]+)?")

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


def _skill_label(skill: dict) -> str:
    """The name to show for an authorized skill: its folder key, the name a request uses."""
    return str(skill.get("key") or skill.get("name") or "")


def _inline_skill_delivery(extra_context: dict | None) -> bool:
    """True when the caller sends this prompt to a runtime with no skill loader."""
    value = (extra_context or {}).get(SKILL_DELIVERY_KEY)
    return str(value or "").strip().lower() == SKILL_DELIVERY_INLINE


def _skill_request_note(extra_context: dict | None) -> str:
    """The sentence that tells an inline-delivery agent how, or whether, to request a skill.

    The skill_request hint when MAS answers a request from this exchange; otherwise a
    sentence that says no further skill text can reach the agent, so the prompt never
    promises a delivery that will not happen. An explicit SKILL_REQUEST_KEY wins over
    what is inferred.
    """
    context = extra_context or {}
    if SKILL_REQUEST_KEY in context:
        value = context[SKILL_REQUEST_KEY]
        if isinstance(value, str):
            value = value.strip().lower() not in ("", "0", "false", "no", "off")
        return SKILL_REQUEST_HINT if value else NO_SKILL_REQUEST
    if "injected_consultation_question" in context:
        return NO_SKILL_REQUEST_CONSULTATION
    if threading.current_thread().name.startswith(_PARALLEL_THREAD_PREFIX):
        return NO_SKILL_REQUEST_PARALLEL
    return SKILL_REQUEST_HINT


def _requested_skill_name(grounded: Any) -> str:
    """The skill a skill_request delivered in this grounded context, or "".

    The orchestration loop puts "[skill_prompt:<name>]" and the rendered skill prompt at
    the start of injected_grounded_context. Only a prompt rendered from a readable
    SKILL.md counts, never a denial or a knowledge answer.
    """
    from core.engine.skill_bridge import SKILL_PROMPT_PREAMBLE

    text = str(grounded or "").lstrip()
    match = _SKILL_PROMPT_MARKER.match(text)
    if match is None or not text[match.end():].startswith(SKILL_PROMPT_PREAMBLE):
        return ""
    return match.group(1).strip()


def _columns(whitespace: str, start: int = 0) -> int:
    """Columns that whitespace spans from column start, with a tab stop every four."""
    column = start
    for char in whitespace:
        column = column + 4 - column % 4 if char == "\t" else column + 1
    return column - start


def _leading(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _item_content_column(item: re.Match) -> tuple[int, bool]:
    """Where a list item's content starts, from its marker match (CommonMark).

    Returns the content column, and whether the text after the marker starts there. A
    marker at the end of its line, or followed by five or more columns (the item then
    starts with indented code), puts the content one column past the marker.
    """
    after_marker = _columns(item.group(1)) + len(item.group(2))
    gap = _columns(item.group(3), after_marker)
    if 1 <= gap <= 4:
        return after_marker + gap, True
    return after_marker + 1, False


def _interrupts_paragraph(item: re.Match, line: str) -> bool:
    """CommonMark: an empty item, or an ordered one not numbered 1, continues a paragraph."""
    marker = item.group(2)
    if not line[item.end():].strip():
        return False
    return not marker[0].isdigit() or int(marker[:-1]) == 1


def _fence_opener(content: str) -> str | None:
    """The marker that content, a line from its first non-blank character, opens a fence with."""
    run = _FENCE_LINE.match(content)
    if run is None or run.group(1):
        return None
    if run.group(2)[0] == "`" and "`" in run.group(3):
        return None  # inline code in a paragraph, not a fence
    return run.group(2)


def _html_opener(content: str, paragraph: bool) -> tuple[re.Pattern | None, str] | None:
    """The HTML block that content, a line from its first non-blank character, opens.

    Returns (end, closing text) for a block of types 1 to 5, which runs until a line
    matches end, and (None, "") for one of types 6 and 7, which a blank line ends. A
    type 7 block (a lone tag of any other name) cannot interrupt a paragraph. None
    when content opens no HTML block.
    """
    raw = _HTML_RAW_START.match(content)
    if raw is not None:
        return _HTML_RAW_END, f"</{raw.group(1).lower()}>"
    for start, end, closing in _HTML_MARKED:
        if start.match(content):
            return end, closing
    tag = _HTML_TAG_START.match(content)
    if tag is not None and tag.group(1).lower() in _HTML_BLOCK_TAGS:
        return None, ""
    if not paragraph and _HTML_LONE_TAG.fullmatch(content.rstrip()):
        return None, ""
    return None


def _block_opened(content: str, leading: str, container: int,
                  paragraph: bool) -> tuple[tuple | None, bool]:
    """Whether content opens a fence or an HTML block, and the block still open after it.

    The block is (kind, end, closing line, container column): kind "fence" with its
    opening marker as end, "html" with the pattern its last line matches, or
    "html_blank" for a block a blank line ends. An HTML block that ends on its own
    first line opens and closes at once, so it leaves no block open.
    """
    marker = _fence_opener(content)
    if marker is not None:
        return ("fence", marker, leading + marker, container), True
    html = _html_opener(content, paragraph)
    if html is None:
        return None, False
    end, closing = html
    if end is None:
        return ("html_blank", None, "", container), True
    if end.search(content):
        return None, True
    return ("html", end, leading + closing, container), True


def _open_block(text: str) -> str | None:
    """The line that closes a block still open at the end of text, or None.

    Follows CommonMark as far as skill files need, for the two kinds of block that
    would swallow what a prompt adds after a cut: fenced code, and HTML blocks that
    only an end marker closes (comments, <pre>, <script> and the like). A block opens
    on a line indented at most three columns past the content column of the list item
    it sits in (column 0 outside any list); a line indented further is indented code
    or paragraph text and opens nothing. A fence closes on a run of its own character,
    at least as long, with nothing after it and the same indentation limit; an HTML
    block closes on a line holding its end marker; either closes when a non-blank line
    falls left of its list item's content column, which ends the item. A fence-looking
    line inside an HTML block is HTML, not a fence. The closing line returned carries
    the opener's own indentation, so it lands in the same list item: a closer at
    column 0 would end the item and open a new fence instead. An HTML block that a
    blank line ends needs no closing line, because the notice follows a blank line.
    """
    items: list[int] = []  # content columns of the open list items, innermost last
    block: tuple | None = None
    paragraph = False  # the previous line was paragraph text a line may continue
    for line in text.splitlines():
        blank = not line.strip()
        if block is not None:
            kind, end, _closing, container = block
            if blank:
                if kind == "html_blank":
                    block = None
                paragraph = False
                continue
            indent = _columns(_leading(line))
            if indent >= container:
                if kind == "fence":
                    run = _FENCE_LINE.match(line)
                    if (run is not None and indent - container <= 3
                            and run.group(2)[0] == end[0] and len(run.group(2)) >= len(end)
                            and not run.group(3).strip()):
                        block = None
                elif kind == "html" and end.search(line):
                    block = None
                continue
            block = None  # The line ends the list item, and the block with it.
        if blank:
            paragraph = False
            continue
        indent = _columns(_leading(line))
        content = line.lstrip(" \t")
        item = _LIST_ITEM.match(line)
        if item is not None and paragraph and not _interrupts_paragraph(item, line):
            item = None
        lazy = paragraph and item is None and not (
            _HEADING.match(content) or _fence_opener(content)
            or _html_opener(content, True))
        if not lazy:
            while items and indent < items[-1]:
                items.pop()
        container = items[-1] if items else 0
        if indent - container >= 4:
            continue  # indented code, or the continuation of a paragraph
        leading = _leading(line)
        if item is not None:
            container, content_follows = _item_content_column(item)
            items.append(container)
            content = line[item.end():] if content_follows else ""
            leading = re.sub(r"\S", " ", line[:item.end()])
        opened, started = _block_opened(content, leading, container,
                                        paragraph and item is None)
        if started:
            block = opened
            paragraph = False
        else:
            paragraph = bool(content.strip()) and not _HEADING.match(content)
    if block is None or not block[2]:
        return None
    return block[2]


def _closing_room(line: str) -> int:
    """Characters a closing line for a block this line opens would take, or 0."""
    prefix = _OPENER_PREFIX.match(line).end()
    content = line[prefix:]
    marker = _fence_opener(content)
    if marker is not None:
        return prefix + len(marker)
    html = _html_opener(content, False)
    if html is not None and html[1]:
        return prefix + len(html[1])
    return 0


def _cut_at_line(text: str, limit: int) -> str:
    """text cut to at most limit characters, at a line break when one is near the end."""
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip()


def _bound_text(text: str, cap: int) -> tuple[str, bool]:
    """Cut text to at most cap characters, closing a block the cut leaves open.

    A cut inside a fenced code block, or inside an HTML block that only an end marker
    closes, would leave the truncation notice and everything the prompt adds after it
    inside that block. So the cut leaves room for the longest closing line any opener
    in text could need, and a block still open at the cut is closed by the line
    _open_block returns. The result never exceeds cap.
    """
    if len(text) <= cap:
        return text, False
    reserve = max((_closing_room(line) for line in text.splitlines()), default=0)
    shown = _cut_at_line(text, max(0, cap - (reserve + 1 if reserve else 0)))
    closing = _open_block(shown)
    if closing is not None:
        shown = f"{shown}\n{closing}"
    return shown, True


class PromptAssembler:
    """
    Assembles agent system prompts by injecting scoped state context
    into .md templates.
    """

    def __init__(self, agents_dir: Path = AGENTS_DIR, skill_bridge: Any = None):
        self.agents_dir = agents_dir
        # Tests inject a SkillBridge over a temporary skills tree. Otherwise each assembly
        # builds a fresh bridge, so a skill added between assemblies is seen.
        self._skill_bridge = skill_bridge
        # One bridge serves every lookup within one assembly, so the catalogue is read
        # once per prompt. Thread-local, because the orchestration loop assembles
        # parallel agents' prompts on one assembler from several threads.
        self._assembly = threading.local()
        # Safe defaults for callers/tests that replace ``assemble`` while still
        # consuming the provider-neutral envelope.
        self.last_token_count = 0
        self.last_prompt_metadata: dict[str, Any] = {}

    def _bridge(self) -> Any:
        if self._skill_bridge is not None:
            return self._skill_bridge
        shared = getattr(self._assembly, "bridge", None)
        if shared is not None:
            return shared
        from core.engine.skill_bridge import SkillBridge
        bridge = SkillBridge()
        if getattr(self._assembly, "active", False):
            self._assembly.bridge = bridge
        return bridge

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
            return [s.to_dict() for s in self._bridge().authorized_skills(agent_id)]
        except Exception:
            return []

    def _build_skill_access_block(
        self,
        agent_id: str,
        skills: list[dict],
        *,
        inline: bool = False,
        required_skills: list[str] | None = None,
        inlined: list[str] | None = None,
        delivered: list[str] | None = None,
        request_note: str = "",
    ) -> str:
        """Human-readable skill access section appended to prompts.

        The default sentence names no single client's command, because the same prompt
        reaches Claude Code, Copilot, opencode and MCP-only clients. ``inline`` replaces
        it for a runtime with no loader, where any command named would not exist. The
        block claims included text only for the skills the prompt carries: ``inlined``
        under Inlined Skills, and ``delivered`` under Grounded Context, where the
        orchestration loop puts a skill an earlier skill_request asked for.
        ``request_note`` says how, or whether, to request another skill; the caller
        passes "" when the Recommended Skill Use block that follows already says it, so
        the prompt says it once.
        """
        if not skills:
            return (
                "## Skill Access\n"
                "Authorized skills: none\n"
                "If a skill is required, delegate to an authorized agent.\n"
            )

        names = ", ".join(_skill_label(s) for s in skills if _skill_label(s))
        lines = ["## Skill Access", f"Authorized skills: {names}"]
        if inline:
            lines.append(
                "This runtime has no skill command and no skill tool, so a skill reaches "
                "you only as text MAS puts in your prompt."
            )
            if inlined:
                listed = ", ".join(f"`{name}`" for name in inlined)
                lines.append(
                    f"The text of these REQUIRED skills is included below, under "
                    f"Inlined Skills: {listed}."
                )
            if delivered:
                listed = ", ".join(f"`{name}`" for name in delivered)
                lines.append(
                    f"The full text of {listed}, delivered on a skill_request, is "
                    f"included under Grounded Context."
                )
            if not inlined and not delivered:
                if required_skills:
                    lines.append(
                        "No skill text is included in this prompt. Inlined Skills says "
                        "why for each REQUIRED skill."
                    )
                else:
                    lines.append(
                        "No REQUIRED skill applies to this step, so no skill text is "
                        "included."
                    )
            if request_note:
                lines.append(request_note)
        else:
            lines.append(
                "When a skill can improve speed or grounding, load it with your client's "
                "own skill command (for example `/skill-name` in Claude Code, Copilot or "
                "opencode) or with the `mas_skill` MCP tool."
            )
        lines.append("Reference skill outputs in your `art` list when relevant.")
        return "\n".join(lines) + "\n"

    def _build_inline_skill_block(
        self,
        agent_id: str,
        recommendations: list,
        *,
        delivered: list[str] | None = None,
        requestable: bool = True,
    ) -> tuple[str, dict]:
        """Inline the text of each REQUIRED skill the agent is authorized for.

        Returns the prompt block and a delivery record for ``last_prompt_metadata``. A
        skill the agent is not authorized for, or one that is not installed, is named
        with the reason and never inlined. A skill in ``delivered``, whose full text a
        skill_request already put under Grounded Context, is named and not repeated.
        Each body is bounded by INLINE_SKILL_CHAR_CAP, and a cut body ends with a
        notice naming the file that holds the rest, so the truncation is never silent.
        All bodies together are bounded by INLINE_TOTAL_CHAR_CAP: a skill that would
        cross it is named with its file, in policy order, so the first REQUIRED skills
        are the ones inlined. Both notices offer a skill_request only when
        ``requestable`` says MAS answers one from this exchange. Never raises.
        """
        record: dict[str, Any] = {
            "mode": SKILL_DELIVERY_INLINE,
            "agent_id": agent_id,
            "char_cap": INLINE_SKILL_CHAR_CAP,
            "total_char_cap": INLINE_TOTAL_CHAR_CAP,
            "total_characters": 0,
            "inlined": [],
            "not_inlined": [],
        }
        required: list[str] = []
        for rec in recommendations or []:
            name = str(getattr(rec, "skill", "") or "")
            if getattr(rec, "required", False) and name and name not in required:
                required.append(name)
        if not required:
            return "", record

        try:
            bridge = self._bridge()
        except Exception:
            bridge = None

        from core.engine.skill_bridge import strip_frontmatter

        already = set(delivered or ())
        sections: list[str] = []
        skipped: list[str] = []
        used = 0
        for name in required:
            reason = ""
            skill_path = ""
            text = ""
            try:
                if bridge is None:
                    reason = "skill_bridge_unavailable"
                else:
                    skill = bridge.get_skill(name)
                    # SKILL_ACCESS grants folder names, so authorize the resolved key.
                    if skill is not None:
                        name = skill.key
                    if not bridge.is_skill_authorized(agent_id, name):
                        reason = "not_authorized"
                    elif name in already:
                        reason = "delivered_on_request"
                    elif skill is None or not skill.installed:
                        reason = "not_found"
                    else:
                        skill_path = os.path.abspath(str(skill.path))
                        text = bridge.read_skill_text(skill)
            except Exception:
                reason = reason or "unreadable"

            shown = ""
            truncated = False
            body = ""
            if not reason:
                body = strip_frontmatter(text).strip()
                shown, truncated = _bound_text(body, INLINE_SKILL_CHAR_CAP)
                if used + len(shown) > INLINE_TOTAL_CHAR_CAP:
                    reason = "over_total_cap"

            if reason:
                record["not_inlined"].append({"skill": name, "reason": reason})
                if reason == "not_authorized":
                    skipped.append(
                        f"- `{name}`: you are not authorized for this skill. "
                        "Delegate the step to an agent that is."
                    )
                elif reason == "delivered_on_request":
                    skipped.append(
                        f"- `{name}`: its full text is included under Grounded Context, "
                        "so it is not repeated here."
                    )
                elif reason == "not_found":
                    skipped.append(
                        f"- `{name}`: not installed in this workspace, so its text "
                        "cannot be included."
                    )
                elif reason == "over_total_cap":
                    request = (
                        f' Request it with `"skill_request": {{"name": "{name}"}}` and '
                        "it arrives on your next step." if requestable else ""
                    )
                    skipped.append(
                        f"- `{name}`: not included, because the skill text already in "
                        f"this prompt ({used:,} characters) leaves no room for it under "
                        f"the {INLINE_TOTAL_CHAR_CAP:,}-character bound. Its text is in "
                        f"{skill_path}.{request}"
                    )
                else:
                    skipped.append(
                        f"- `{name}`: its SKILL.md could not be read"
                        + (f" ({skill_path})." if skill_path else ".")
                    )
                continue

            used += len(shown)
            section = [f"### Skill: {name}", f"Source: {skill_path}", "", shown]
            if truncated:
                request = (
                    f" A skill_request for `{name}` brings the whole file on your next "
                    "step." if requestable else ""
                )
                section += [
                    "",
                    f"[Truncated: showing {len(shown):,} of {len(body):,} characters. "
                    f"The full text is in {skill_path}.{request}]",
                ]
            sections.append("\n".join(section))
            record["inlined"].append({
                "skill": name,
                "characters": len(shown),
                "truncated": truncated,
            })

        record["total_characters"] = used
        lines = ["## Inlined Skills"]
        if sections:
            lines.append(
                "Apply each skill below before producing a final decision, and record "
                "it in `skill_used` / `sk_used`."
            )
            lines.append("")
            lines.append("\n\n".join(sections))
        elif already:
            listed = ", ".join(f"`{name}`" for name in sorted(already))
            lines.append(
                f"No skill text is inlined under this heading. The full text of {listed} "
                "is included under Grounded Context."
            )
        else:
            lines.append("No skill text is included in this prompt.")
        if skipped:
            lines.append("")
            lines.append("Not inlined:")
            lines.extend(skipped)
        return "\n".join(lines).strip() + "\n", record

    def _delivered_skill(self, agent_id: str, extra_context: dict | None) -> tuple[str, bool]:
        """The skill a skill_request delivered in this prompt's grounded context.

        Returns (folder key, or "" when the context carries no delivered skill, and
        whether the agent is denied it). The orchestration loop answers a request only
        for the agent that sent it, but parallel sub-agents share one pending slot, so
        the text can reach another agent; SKILL_ACCESS still decides who may read it.
        A bridge that fails denies nothing, so the context is kept as the caller sent it.
        """
        name = _requested_skill_name((extra_context or {}).get("injected_grounded_context"))
        if not name:
            return "", False
        try:
            bridge = self._bridge()
            skill = bridge.get_skill(name)
            key = skill.key if skill is not None else name
            return key, not bridge.is_skill_authorized(agent_id, key)
        except Exception:
            return name, False

    def _skill_recommendations(
        self,
        state: dict,
        extra_context: dict | None,
    ) -> list:
        """Phase-aware skill triggers for this state. Returns [] on any error."""
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
            return SkillTriggerPolicy().recommendations_for(
                state=state,
                project_dir=project_dir,
                event=str(event) if event else None,
                changed_paths=changed_paths,
                status=str(status) if status else None,
            )
        except Exception:
            return []

    def _build_recommended_skill_block(
        self,
        state: dict,
        extra_context: dict | None,
        recommendations: list | None = None,
        inlined: list[str] | None = None,
        delivered: list[str] | None = None,
    ) -> str:
        """Render phase-aware required/recommended skill triggers.

        ``inlined`` names the skills whose text the inline block carries and
        ``delivered`` the one a skill_request put under Grounded Context, so the
        triggers claim included text for those alone. In inline mode the block ends
        with the sentence _skill_request_note picks for this exchange.
        """
        try:
            from core.engine.skill_trigger import SkillTriggerPolicy
            if recommendations is None:
                recommendations = self._skill_recommendations(state, extra_context)
            project_id = state.get("core_identity", {}).get("project_id", "")
            return SkillTriggerPolicy.render_block(
                recommendations,
                project_id,
                inline=_inline_skill_delivery(extra_context),
                inlined=inlined,
                delivered=delivered,
                request_note=_skill_request_note(extra_context),
            )
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
        self._assembly.active = True
        self._assembly.bridge = None
        try:
            return self._assemble(agent_id, state, extra_context)
        finally:
            self._assembly.active = False
            self._assembly.bridge = None

    def _assemble(self, agent_id: str, state: dict,
                  extra_context: dict | None = None) -> str:
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
            ", ".join(_skill_label(s) for s in authorized_skills if _skill_label(s))
            if authorized_skills else "(none)"
        )
        inline_skills = _inline_skill_delivery(extra_context)
        request_note = _skill_request_note(extra_context) if inline_skills else ""
        recommendations = self._skill_recommendations(state, extra_context)
        required_skills = [
            str(rec.skill) for rec in recommendations if getattr(rec, "required", False)
        ]
        # A skill an earlier skill_request asked for arrives as grounded context. The
        # blocks below count its text as included, so none of them denies it or offers
        # to request it again. An agent the skill is not granted to never sees it.
        delivered_key, delivered_denied = self._delivered_skill(
            canonical_agent_id, extra_context
        )
        dropped_keys: set[str] = set()
        if delivered_denied:
            logger.warning(
                "Skill %r delivered to %s, which is not authorized for it; dropped "
                "from the prompt.", delivered_key, canonical_agent_id,
            )
            dropped_keys.add("injected_grounded_context")
        delivered_names = [delivered_key] if delivered_key and not delivered_denied else []
        # Inline delivery only: REQUIRED skill text goes in a block of its own, after the
        # stable prefix like every other injected block (prompt-token-contract.md). It is
        # built first, because the blocks that describe it must name what it carries.
        delivery_record: dict[str, Any] | None = None
        context["injected_inlined_skills"] = ""
        if inline_skills:
            context["injected_inlined_skills"], delivery_record = (
                self._build_inline_skill_block(
                    canonical_agent_id,
                    recommendations,
                    delivered=delivered_names,
                    requestable=request_note == SKILL_REQUEST_HINT,
                )
            )
        inlined_names = [
            str(item["skill"]) for item in (delivery_record or {}).get("inlined", [])
        ]
        context["injected_recommended_skill_use"] = self._build_recommended_skill_block(
            state, extra_context, recommendations, inlined_names, delivered_names
        )
        skill_access_block = self._build_skill_access_block(
            canonical_agent_id,
            authorized_skills,
            inline=inline_skills,
            required_skills=required_skills,
            inlined=inlined_names,
            delivered=delivered_names,
            request_note="" if context["injected_recommended_skill_use"] else request_note,
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
            # The delivery flags steer assembly; they are not prompt content.
            context.update({
                key: value for key, value in extra_context.items()
                if key not in (SKILL_DELIVERY_KEY, SKILL_REQUEST_KEY)
                and key not in dropped_keys
            })

        prompt = _fill_placeholders(template, context)
        prompt = self._append_missing_context_sections(prompt, template, context)
        if ("{injected_authorized_skills}" not in template and
                "{injected_authorized_skill_names}" not in template):
            prompt = f"{prompt.rstrip()}\n\n{skill_access_block}"
        if ("{injected_recommended_skill_use}" not in template and
                context.get("injected_recommended_skill_use")):
            prompt = f"{prompt.rstrip()}\n\n{context['injected_recommended_skill_use']}\n"
        if ("{injected_inlined_skills}" not in template and
                context.get("injected_inlined_skills")):
            prompt = f"{prompt.rstrip()}\n\n{context['injected_inlined_skills']}"
        self.last_token_count: int = _token_counter.count(prompt)
        skill_text = skill_access_block
        skill_text += context.get("injected_recommended_skill_use", "")
        skill_text += context.get("injected_inlined_skills", "")
        if delivered_names:
            # The whole grounded context is the delivered skill prompt.
            skill_text += str(context.get("injected_grounded_context") or "")
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
        if delivery_record is not None:
            # Read by the orchestration loop, which audits the inlined skills only once
            # the provider call has returned without error.
            self.last_prompt_metadata["skill_delivery"] = delivery_record
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
