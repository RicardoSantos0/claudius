"""
MAS MCP server (M-d).

Exposes the MAS engine over the Model Context Protocol so ANY MCP client (Claude
Code, Codex, an IDE, ...) can drive governed project state — the surface that makes
"Claude Code as one surface over the package" (d-009 destination) reachable.

Run:  mas-server         (stdio transport)

Tools are thin wrappers over the same engine the CLI uses (SharedStateManager,
OrchestrationLoop, PromptAssembler, manual_loop.apply_ingest), so the MCP surface
and the CLI share one tested code path. `mcp` is an optional dependency
(`pip install 'claudius[server]'` / the `server` extra).
"""

from __future__ import annotations

import dataclasses

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mas-server", instructions=(
    "MAS (Multi-Agent System) engine over MCP. Inspect and drive governed project "
    "state, assemble the next agent prompt, and apply an LLM response from any "
    "provider (the provider-agnostic manual loop)."
))


def _sm(project_id: str):
    from core.engine.shared_state_manager import SharedStateManager
    return SharedStateManager(project_id)


def _yaml(obj) -> str:
    import yaml
    return yaml.safe_dump(obj, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _is_pending(h: dict) -> bool:
    acc = h.get("acceptance")
    if isinstance(acc, dict):
        return acc.get("status") == "pending"
    return h.get("acc") == "pending"


@mcp.tool()
def mas_list_projects() -> str:
    """List known MAS project IDs."""
    from core.config import get_projects_dir
    from core.utils.config import iter_project_dirs
    ids = sorted(p.name for p in iter_project_dirs(projects_root=get_projects_dir()))
    return _yaml(ids)


@mcp.tool()
def mas_status(project_id: str) -> str:
    """Status summary for a project: phase, status, mode, owner, pending-handoff count."""
    st = _sm(project_id).load()
    ci = st.get("core_identity", {})
    wf = st.get("workflow", {})
    pending = [h for h in wf.get("handoff_history", []) if _is_pending(h)]
    return _yaml({
        "project_id": project_id,
        "phase": ci.get("current_phase"),
        "status": ci.get("status"),
        "mode": wf.get("mode", "standard"),
        "owner": wf.get("current_owner"),
        "completed_phases": wf.get("completed_phases", []),
        "pending_handoffs": len(pending),
    })


@mcp.tool()
def mas_state(project_id: str, path: str = "") -> str:
    """Read shared state. Optional dot-path, e.g. 'core_identity.current_phase'."""
    cur = _sm(project_id).load()
    if path:
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return f"[not found] {path}"
    return _yaml(cur)


@mcp.tool()
def mas_pending(project_id: str) -> str:
    """List pending handoffs for a project."""
    st = _sm(project_id).load()
    pending = [h for h in st.get("workflow", {}).get("handoff_history", []) if _is_pending(h)]
    return _yaml([
        {"handoff_id": h.get("handoff_id"), "from": h.get("from_agent"),
         "to": h.get("to_agent"), "task": h.get("task_description")}
        for h in pending
    ])


@mcp.tool()
def mas_decisions(project_id: str) -> str:
    """Return the decision log for a project."""
    return _yaml(_sm(project_id).load().get("decisions", {}).get("decision_log", []))


@mcp.tool()
def mas_milestones(project_id: str) -> str:
    """Return execution milestones and tasks for a project."""
    ex = _sm(project_id).load().get("execution", {})
    return _yaml({"milestones": ex.get("milestones", []), "tasks": ex.get("tasks", [])})


@mcp.tool()
def mas_roster(filter_status: str = "") -> str:
    """Capability registry summary: agent_id, trust_tier, status."""
    import yaml
    from core.paths import mas_root
    registry_path = mas_root() / "roster" / "registry_index.yaml"
    if not registry_path.exists():
        return "[error] registry_index.yaml not found"
    with open(registry_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    agents = data.get("registry", {}).get("agents", [])
    if filter_status:
        agents = [a for a in agents if a.get("status") == filter_status]
    return _yaml([
        {"agent_id": a.get("agent_id"), "trust_tier": a.get("trust_tier"), "status": a.get("status")}
        for a in agents
    ])


@mcp.tool()
def mas_next_agent(project_id: str) -> str:
    """Determine the next agent for the current phase."""
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    state = _sm(project_id).load()
    loop = OrchestrationLoop(LoopConfig(project_id=project_id))
    return loop._determine_next_agent(state)


@mcp.tool()
def mas_prompt(project_id: str, agent_id: str = "") -> str:
    """Assemble the next agent prompt (or a specific agent's) for manual/MCP execution."""
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    from core.engine.prompt_assembler import PromptAssembler
    from core.engine.agent_ids import normalize_agent_id
    from core.paths import mas_root
    state = _sm(project_id).load()
    if agent_id:
        aid = normalize_agent_id(agent_id) or agent_id
    else:
        aid = OrchestrationLoop(LoopConfig(project_id=project_id))._determine_next_agent(state)
    agents_dir = mas_root().parent / "agents"
    return PromptAssembler(agents_dir=agents_dir).assemble(aid, state)


@mcp.tool()
def mas_snapshot(project_id: str, phase: str = "") -> str:
    """Save a timestamped snapshot of shared state. Returns the snapshot path."""
    sm = _sm(project_id)
    ph = phase or sm.load().get("core_identity", {}).get("current_phase", "manual")
    return str(sm.snapshot(ph))


@mcp.tool()
def mas_ingest(project_id: str, response: str, agent_id: str = "") -> str:
    """Apply an LLM response (from ANY provider) to governed state — the manual loop.

    Parses the wire block, records an accepted handoff, then advances/delegates/etc.
    Returns the structured outcome (phase_before/after, action, handoff_id, ...).
    """
    from core.engine.manual_loop import apply_ingest
    res = apply_ingest(project_id, response, agent_id or None)
    return _yaml(dataclasses.asdict(res))


# ===========================================================================
# FULL GOVERNANCE TOOLSET (harvested from codex-mas, proj-YYYYMMDD-NNN).
#
# The tools above are claude-config's read + provider-agnostic manual loop
# (mas_prompt / mas_ingest). The tools below expose the rest of the governed
# lifecycle — state writes, handoffs, intake, capability/HR, planning,
# evaluation, training, spawn, wire, knowledge, observability — so every MCP
# surface (Codex / Gemini / local / Claude Code) reaches the same engine the
# CLI does. Re-pathed to claude-config's layout; the codex-specific fatal-error
# "incidents" subsystem and the auto/guidance OrchestrationLoop tools were left
# out (they depend on schema/APIs claude-config deliberately does not have, and
# are already covered by mas_next_agent / mas_prompt / `mas run`).
# ===========================================================================

import json
import re
from datetime import datetime, timezone

from core.config import load_config, get_projects_dir, resolve_project_dir
from core.utils.log_helpers import _get_connection, DB_PATH
from core.utils.wire_protocol import encode as wire_encode, decode as wire_decode
from core.engine.metrics_engine import MetricsEngine
from core.engine.training_engine import TrainingEngine
from core.engine.spawn_policy import SpawnPolicyEngine
from core.engine.capability_registry import CapabilityRegistry
from core.engine.intake_checker import IntakeChecker
from core.engine.task_board import TaskBoard
from core.engine.handoff_engine import HandoffEngine
from core.engine.observability import build_slo_report, list_metric_samples
from core.engine.backend_adapters import (
    resolve_relational_backend,
    resolve_vector_backend,
    relational_backend_ready,
    vector_backend_ready,
)

_metrics_engine = MetricsEngine()
_training_engine = TrainingEngine()
_spawn_policy_engine = SpawnPolicyEngine()
_intake_checker = IntakeChecker()
_handoff_engine = HandoffEngine()
mcp_registry = CapabilityRegistry()

_get_sm = _sm  # codex tool bodies use _get_sm

_MAX_SLUG_LEN = 40
_FULL_ID_RE = re.compile(r"^proj-\d{8}-\d{3}-.+$")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")
    return s[:_MAX_SLUG_LEN]


def _next_sequence(projects_dir, date_str: str) -> int:
    prefix = f"proj-{date_str}-"
    max_seq = 0
    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                parts = d.name.split("-", 3)
                if len(parts) >= 3:
                    try:
                        max_seq = max(max_seq, int(parts[2]))
                    except ValueError:
                        pass
    return max_seq + 1


def _generate_project_id(slug: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"proj-{date_str}-{_next_sequence(get_projects_dir(), date_str):03d}-{slug}"


# --- Evaluation (Evaluator) ------------------------------------------------

@mcp.tool()
def mas_score_project(project_id: str, shared_state_json: str, task_board_json: str) -> str:
    """Compute all project-level metrics. Returns YAML list of MetricResult."""
    import yaml
    try:
        shared_state = json.loads(shared_state_json)
        task_board_data = json.loads(task_board_json)
    except Exception as e:
        return f"Error: Invalid input JSON: {e}"
    results = _metrics_engine.evaluate_project(
        project_id, shared_state, resolve_project_dir(project_id), task_board_data)
    return yaml.dump([r.__dict__ for r in results], default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_score_agent(project_id: str, agent_id: str, shared_state_json: str, task_board_json: str) -> str:
    """Compute all agent-level metrics for one agent. Returns YAML AgentEvaluation."""
    import yaml
    try:
        shared_state = json.loads(shared_state_json)
        task_board_data = json.loads(task_board_json)
    except Exception as e:
        return f"Error: Invalid input JSON: {e}"
    result = _metrics_engine.evaluate_agent(agent_id, shared_state, task_board_data)
    return yaml.dump(result.to_dict(), default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_eval_report(project_id: str, shared_state_json: str, task_board_json: str, agents_json: str) -> str:
    """Produce a full EvaluationReport for a project. Returns YAML EvaluationReport."""
    import yaml
    try:
        shared_state = json.loads(shared_state_json)
        task_board_data = json.loads(task_board_json)
        agents_to_evaluate = json.loads(agents_json)
    except Exception as e:
        return f"Error: Invalid input JSON: {e}"
    report = _metrics_engine.produce_report(
        project_id, shared_state, resolve_project_dir(project_id), task_board_data, agents_to_evaluate)
    return yaml.dump(report.to_dict(), default_flow_style=False, allow_unicode=True)


# --- Training (Trainer) ----------------------------------------------------

@mcp.tool()
def mas_analyze_training(project_id: str, report_json: str) -> str:
    """Produce training proposals from a single evaluation report. Returns YAML list of TrainingProposal."""
    import yaml
    try:
        report_data = json.loads(report_json)
    except Exception as e:
        return f"Error: Invalid report_json: {e}"
    proposals = _training_engine.analyze_evaluation_report(report_data, project_id=project_id)
    return yaml.dump([p.__dict__ for p in proposals], default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_training_brief(project_id: str, proposals_json: str) -> str:
    """Write training brief to disk for a project. Returns path."""
    try:
        proposals = [type('TP', (), p)() for p in json.loads(proposals_json)]
    except Exception as e:
        return f"Error: Invalid proposals_json: {e}"
    path = _training_engine.produce_training_brief(project_id, proposals, resolve_project_dir(project_id))
    return str(path)


@mcp.tool()
def mas_training_backlog() -> str:
    """Return the current training backlog as YAML."""
    import yaml
    return yaml.dump(_training_engine.load_backlog(), default_flow_style=False, allow_unicode=True)


# --- Spawn policy (Spawner) ------------------------------------------------

@mcp.tool()
def mas_spawn_validate(project_id: str, request_json: str, registry_json: str, gap_cert_json: str = "") -> str:
    """Validate a spawn request against policy. Returns YAML ValidationResult."""
    import yaml
    try:
        request = json.loads(request_json)
        registry = json.loads(registry_json)
        gap_cert = json.loads(gap_cert_json) if gap_cert_json else None
    except Exception as e:
        return f"Error: Invalid input JSON: {e}"
    result = _spawn_policy_engine.validate(request, registry, resolve_project_dir(project_id), gap_cert)
    return yaml.dump(result.__dict__, default_flow_style=False, allow_unicode=True)


# --- Task board / planning (Project Manager) -------------------------------

@mcp.tool()
def mas_create_milestone(project_id: str, milestone_json: str) -> str:
    """Create a milestone. milestone_json is a JSON dict. Returns the new milestone_id."""
    try:
        milestone_data = json.loads(milestone_json)
    except Exception as e:
        return f"Error: Invalid milestone_json: {e}"
    return f"[ok] Created milestone: {TaskBoard(project_id).create_milestone(milestone_data)}"


@mcp.tool()
def mas_create_task(project_id: str, task_json: str) -> str:
    """Add a task to the board. task_json is a JSON dict. Returns the new task_id."""
    try:
        task_data = json.loads(task_json)
    except Exception as e:
        return f"Error: Invalid task_json: {e}"
    return f"[ok] Created task: {TaskBoard(project_id).create_task(task_data)}"


@mcp.tool()
def mas_update_status(project_id: str, task_id: str, status: str, notes: str = "",
                      blocker_description: str = "", actual_effort: str = "") -> str:
    """Update a task's status. Returns '[ok]' if updated, error otherwise."""
    try:
        found = TaskBoard(project_id).update_status(
            task_id, status, notes=notes or None,
            blocker_description=blocker_description or None, actual_effort=actual_effort or None)
    except Exception as e:
        return f"Error: {e}"
    return f"[ok] Status updated for {task_id}" if found else f"[error] Task not found: {task_id}"


@mcp.tool()
def mas_list_tasks(project_id: str, status: str = "", milestone: str = "", assigned_to: str = "") -> str:
    """List tasks, optionally filtered by status, milestone, or assignee. Returns YAML list."""
    import yaml
    tasks = TaskBoard(project_id).list_tasks(
        status=status or None, milestone=milestone or None, assigned_to=assigned_to or None)
    return yaml.dump(tasks, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_show_task(project_id: str, task_id: str) -> str:
    """Show a task in detail as YAML."""
    import yaml
    task = TaskBoard(project_id).get_task(task_id)
    if not task:
        return f"[error] Task not found: {task_id}"
    return yaml.dump(task, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_blocked_tasks(project_id: str) -> str:
    """Show all blocked tasks as YAML."""
    import yaml
    return yaml.dump(TaskBoard(project_id).get_blocked(), default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_milestone_status(project_id: str, milestone_id: str) -> str:
    """Show milestone completion status as YAML."""
    import yaml
    return yaml.dump(TaskBoard(project_id).get_milestone_status(milestone_id),
                     default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_progress_report(project_id: str, milestone_id: str = "") -> str:
    """Generate a progress report. If milestone_id is given, scope to that milestone. Returns YAML."""
    import yaml
    report = TaskBoard(project_id).produce_progress_report(milestone_id=milestone_id or None)
    return yaml.dump(report, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_deps(project_id: str, task_id: str) -> str:
    """Show full dependency chain for a task as YAML."""
    import yaml
    try:
        deps = TaskBoard(project_id).get_dependency_chain(task_id)
    except Exception as e:
        return f"Error: {e}"
    return yaml.dump(deps, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_plan(project_id: str, product_plan_path: str) -> str:
    """Compile and write the execution plan. Returns YAML plan."""
    import yaml
    return yaml.dump(TaskBoard(project_id).produce_execution_plan(product_plan_path),
                     default_flow_style=False, allow_unicode=True)


# --- Capability registry (HR) ----------------------------------------------

@mcp.tool()
def mas_capability_search(tags: str, min_score: float = 0.0) -> str:
    """Search for agents by capability tags (comma-separated). Returns YAML list of matches."""
    import yaml
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    results = [r for r in mcp_registry.search(tag_list) if r.score >= min_score]
    return yaml.dump([{
        "agent_id": r.agent_id, "name": r.name, "trust_tier": r.trust_tier,
        "status": r.status, "capabilities": r.capabilities, "score": r.score,
        "match_type": r.match_type, "recommendation": r.recommendation,
    } for r in results], default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_capability_gap_cert(project_id: str, requested_by: str, need: str, tags: str, save: bool = True) -> str:
    """Produce a Capability Gap Certificate. Returns YAML certificate and saves to disk if save=True."""
    import yaml
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    cert = mcp_registry.produce_gap_certificate(need, tag_list, project_id, requested_by)
    out = yaml.dump(cert.to_dict(), default_flow_style=False, allow_unicode=True)
    if save:
        return f"{out}\n[ok] Certificate saved: {mcp_registry.save_gap_certificate(cert, project_id)}"
    return out


@mcp.tool()
def mas_capability_register(entry_json: str, authorized_by: str = "master_orchestrator") -> str:
    """Register a new agent entry in the capability registry. entry_json is a JSON dict."""
    try:
        entry = json.loads(entry_json)
    except Exception as e:
        return f"Error: Invalid entry_json: {e}"
    mcp_registry.register_agent(entry, authorized_by=authorized_by)
    return f"[ok] Registered: {entry.get('agent_id','?')}"


@mcp.tool()
def mas_capability_retire(agent_id: str, reason: str, authorized_by: str = "master_orchestrator") -> str:
    """Retire an agent in the capability registry."""
    found = mcp_registry.retire_agent(agent_id, reason, authorized_by=authorized_by)
    return f"[ok] Retired: {agent_id}" if found else f"[error] Agent not found: {agent_id}"


@mcp.tool()
def mas_capability_show(agent_id: str) -> str:
    """Show a specific agent's registry entry as YAML."""
    import yaml
    agent = mcp_registry.get_agent(agent_id)
    if agent is None:
        return f"[error] Agent not found: {agent_id}"
    return yaml.dump(agent, default_flow_style=False, allow_unicode=True)


# --- Intake (Inquirer) -----------------------------------------------------

@mcp.tool()
def mas_intake_analyze(spec_json: str) -> str:
    """Analyze a project specification for completeness and readiness. Returns YAML."""
    import yaml
    try:
        spec = json.loads(spec_json)
    except Exception as e:
        return f"Error: Invalid spec_json: {e}"
    r = _intake_checker.analyze(spec)
    return yaml.dump({
        "complete": r.complete, "score": r.score, "ready_for_handoff": r.ready_for_handoff,
        "required_present": r.required_present, "required_missing": r.required_missing,
        "recommended_present": r.recommended_present, "recommended_missing": r.recommended_missing,
        "ambiguous": r.ambiguous,
    }, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_intake_questions(spec_json: str, round_number: int = 1, max_questions: int = 7) -> str:
    """Generate clarification questions for a spec. Returns YAML list of questions."""
    import yaml
    try:
        spec = json.loads(spec_json)
    except Exception as e:
        return f"Error: Invalid spec_json: {e}"
    result = _intake_checker.analyze(spec)
    questions = _intake_checker.generate_questions(result, round_number, max_questions)
    return yaml.dump(questions, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_intake_record_qa(project_id: str, round_number: int, qa_json: str) -> str:
    """Record a Q&A round for intake clarification. qa_json is a JSON array of Q&A dicts."""
    try:
        qa_entries = json.loads(qa_json)
    except Exception as e:
        return f"Error: Invalid qa_json: {e}"
    return f"OK {_intake_checker.record_qa(project_id, round_number, qa_entries)}"


@mcp.tool()
def mas_intake_write_spec(project_id: str, spec_json: str) -> str:
    """Write the clarified specification to disk and return the path, score and readiness."""
    try:
        spec = json.loads(spec_json)
    except Exception as e:
        return f"Error: Invalid spec_json: {e}"
    result = _intake_checker.analyze(spec)
    path = _intake_checker.write_spec(project_id, spec, result)
    return f"OK {path} score={result.score:.4f} ready={result.ready_for_handoff}"


# --- Project init + backend status -----------------------------------------

@mcp.tool()
def mas_init(name_or_id: str, request_id: str = "", mode: str = "standard") -> str:
    """Initialize a new MAS project. Provide a slug (e.g. 'website-redesign')
    or a full project ID. Returns the created project ID and path."""
    if _FULL_ID_RE.match(name_or_id):
        project_id = name_or_id
    else:
        slug = _slugify(name_or_id)
        if not slug:
            return "Error: Invalid slug — must contain at least one alphanumeric character."
        project_id = _generate_project_id(slug)

    if not request_id:
        request_id = f"req-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    mode = mode.lower() if isinstance(mode, str) else "standard"
    if mode not in {"standard", "lite"}:
        mode = "standard"

    sm = _get_sm(project_id)
    if sm.project_dir.exists():
        return f"Project '{project_id}' already exists at {sm.project_dir}"
    sm.initialize(request_id=request_id, mode=mode)
    return (f"Project initialized.\n  Project ID : {project_id}\n"
            f"  State file : {sm.state_path}\n  Request ID : {request_id}\n  Mode       : {mode}")


@mcp.tool()
def mas_backend_status() -> str:
    """Show configured relational/vector backend targets and readiness (sqlite default)."""
    rel = resolve_relational_backend()
    vec = resolve_vector_backend()
    rel_ok, rel_msg = relational_backend_ready()
    vec_ok, vec_msg = vector_backend_ready()
    return (
        "Backend status\n"
        f"  relational.provider : {rel.provider}\n"
        f"  relational.url      : {rel.url}\n"
        f"  relational.ready    : {rel_ok} ({rel_msg})\n"
        f"  vector.provider     : {vec.provider}\n"
        f"  vector.enabled      : {vec.enabled}\n"
        f"  vector.sqlite_url   : {vec.sqlite_url}\n"
        f"  vector.chroma_dir   : {vec.chroma_persist_directory}\n"
        f"  vector.collection   : {vec.chroma_collection}\n"
        f"  vector.ready        : {vec_ok} ({vec_msg})"
    )


# --- State management ------------------------------------------------------

@mcp.tool()
def mas_state_read(project_id: str, path: str) -> str:
    """Read a value from shared state by dot-notation path,
    e.g. mas_state_read('proj-001', 'core_identity.current_phase')."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    value = sm.read(path)
    if isinstance(value, (dict, list)):
        return _yaml(value)
    return str(value) if value is not None else "(null)"


@mcp.tool()
def mas_state_write(project_id: str, agent_id: str, section: str, field: str, value: str) -> str:
    """Write a value to a shared state field (access control enforced).
    value is a JSON-encoded string for complex types, or a plain string for scalars."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed_value = value
    result = sm.write(agent_id, section, field, parsed_value)
    return f"OK: {section}.{field} updated." if result else f"DENIED: {getattr(result, 'reason', 'denied')}"


@mcp.tool()
def mas_state_append(project_id: str, agent_id: str, section: str, field: str, item: str) -> str:
    """Append an item to an append-only list field. The item is JSON-encoded."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    try:
        parsed_item = json.loads(item)
    except (json.JSONDecodeError, TypeError):
        parsed_item = item
    result = sm.append(agent_id, section, field, parsed_item)
    return f"OK: item appended to {section}.{field}." if result else f"DENIED: {getattr(result, 'reason', 'denied')}"


@mcp.tool()
def mas_state_approve(project_id: str, agent_id: str, section: str, field: str) -> str:
    """Approve (lock) a field so it becomes immutable. Only master_orchestrator can do this."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    result = sm.approve(agent_id, section, field)
    return f"OK: {section}.{field} approved and locked." if result else f"DENIED: {getattr(result, 'reason', 'denied')}"


@mcp.tool()
def mas_state_snapshot(project_id: str, phase: str) -> str:
    """Save a timestamped snapshot of the current shared state for a phase boundary."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    return f"Snapshot saved: {sm.snapshot(phase)}"


@mcp.tool()
def mas_state_show(project_id: str) -> str:
    """Show the full shared state for a project as YAML."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    return _yaml(sm.load())


# --- Knowledge retrieval (authoritative DB index) --------------------------

def _knowledge_query(sql: str, params: tuple):
    try:
        with _get_connection(DB_PATH) as conn:
            return conn.execute(sql, params).fetchall()
    except Exception:
        return None


@mcp.tool()
def mas_search_knowledge(query: str, category: str = "", codebase: str = "") -> str:
    """Search indexed repository knowledge (policies/foundation/domains/roster/code) by keyword.
    The knowledge_index is the authoritative reference; populate it with sync_all_knowledge."""
    import yaml
    sql = "SELECT codebase, path_id, category, metadata FROM knowledge_index WHERE (content LIKE ? OR path_id LIKE ?)"
    params = [f"%{query}%", f"%{query}%"]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if codebase:
        sql += " AND codebase = ?"
        params.append(codebase)
    rows = _knowledge_query(sql, tuple(params))
    if rows is None:
        return "No knowledge index found. Run sync_all_knowledge first."
    results = [{"codebase": r[0], "path_id": r[1], "category": r[2],
                "metadata": json.loads(r[3] or "{}")} for r in rows]
    if not results:
        return f"No knowledge results found for query: {query}"
    return yaml.dump(results, default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_read_knowledge(path_id: str, codebase: str = "") -> str:
    """Read the full content of an indexed knowledge document (e.g. 'mas/policies/governance_policy.yaml')."""
    sql = "SELECT codebase, content FROM knowledge_index WHERE path_id = ?"
    params = [path_id]
    if codebase:
        sql += " AND codebase = ?"
        params.append(codebase)
    rows = _knowledge_query(sql, tuple(params))
    if not rows:
        return f"Error: Knowledge document '{path_id}' not found in DB index."
    if len(rows) > 1 and not codebase:
        return ("Error: Multiple documents matched this path_id across codebases. "
                f"Provide codebase. Matches: {', '.join(r[0] for r in rows)}")
    return rows[0][1]


@mcp.tool()
def mas_list_codebases() -> str:
    """List distinct codebase identifiers available in the knowledge_index."""
    import yaml
    rows = _knowledge_query(
        "SELECT codebase, COUNT(*) FROM knowledge_index GROUP BY codebase ORDER BY COUNT(*) DESC", ())
    if not rows:
        return "No codebases found in knowledge index. Run sync_all_knowledge first."
    return yaml.dump([{"codebase": r[0], "row_count": r[1]} for r in rows],
                     default_flow_style=False, allow_unicode=True)


# --- Handoffs --------------------------------------------------------------

@mcp.tool()
def mas_handoff_create(project_id: str, from_agent: str, to_agent: str, phase: str,
                       task_description: str, payload_json: str,
                       authorized_by: str = "master_orchestrator") -> str:
    """Create a formal agent-to-agent handoff. payload_json is a JSON object with keys:
    summary, artifacts_produced, decisions_made, open_questions,
    constraints_for_next, shared_state_fields_modified."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid payload JSON: {e}"
    handoff = _handoff_engine.create(
        sm, from_agent=from_agent, to_agent=to_agent, phase=phase,
        task_description=task_description, payload=payload, authorized_by=authorized_by)
    return (f"Handoff created.\n  ID       : {handoff['handoff_id']}\n  From     : {from_agent}\n"
            f"  To       : {to_agent}\n  Phase    : {phase}\n  Task     : {task_description}\n  Status   : pending")


@mcp.tool()
def mas_handoff_accept(project_id: str, handoff_id: str, follow_up_questions: str = "") -> str:
    """Accept a pending handoff. Optionally provide follow-up questions as a JSON array."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    questions = None
    if follow_up_questions:
        try:
            questions = json.loads(follow_up_questions)
        except json.JSONDecodeError:
            return "Error: Invalid follow_up_questions JSON."
    ok = _handoff_engine.accept(sm, handoff_id, follow_up_questions=questions)
    return f"Handoff {handoff_id} accepted." if ok else f"Error: Handoff {handoff_id} not found or already resolved."


@mcp.tool()
def mas_handoff_reject(project_id: str, handoff_id: str, reason: str) -> str:
    """Reject a pending handoff with a reason."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    ok = _handoff_engine.reject(sm, handoff_id, reason)
    return f"Handoff {handoff_id} rejected: {reason}" if ok else f"Error: Handoff {handoff_id} not found or already resolved."


@mcp.tool()
def mas_handoff_pending(project_id: str, to_agent: str = "") -> str:
    """List all pending handoffs for a project, optionally filtered by recipient agent."""
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    pending = _handoff_engine.get_pending(sm, to_agent=to_agent or None)
    if not pending:
        return "No pending handoffs."
    lines = [f"Pending handoffs ({len(pending)}):"]
    for h in pending:
        lines.append(f"  [{h['handoff_id']}] {h['from_agent']} → {h['to_agent']}: {h.get('task_description', '')}")
    return "\n".join(lines)


# --- Wire protocol ---------------------------------------------------------

@mcp.tool()
def mas_wire_encode(payload_json: str) -> str:
    """Encode an expanded payload to compact wire-protocol format."""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON: {e}"
    return json.dumps(wire_encode(payload), indent=2, ensure_ascii=False)


@mcp.tool()
def mas_wire_decode(wire_json: str) -> str:
    """Decode a compact wire-protocol payload back to expanded format."""
    try:
        wire = json.loads(wire_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON: {e}"
    return json.dumps(wire_decode(wire), indent=2, ensure_ascii=False)


# --- Observability ---------------------------------------------------------

@mcp.tool()
def mas_observability_recent(project_id: str, operation: str = "", limit: int = 50) -> str:
    """Return recent observability metric samples for a project as JSON."""
    op = operation.strip() or None
    samples = list_metric_samples(project_id=project_id, operation=op, limit=max(1, min(limit, 500)))
    return json.dumps({
        "project_id": project_id, "operation_filter": op, "count": len(samples),
        "samples": [s.to_dict() for s in samples],
    }, indent=2, ensure_ascii=False)


@mcp.tool()
def mas_slo_report(project_id: str, operation: str = "", limit: int = 500) -> str:
    """Return a summarized SLO report (p95/p99, success-rate, breaches) for a project as JSON."""
    op = operation.strip() or None
    return json.dumps(build_slo_report(project_id=project_id, operation=op, limit=limit), indent=2)


# --- Lifecycle / lint / skill / consultation -------------------------------

@mcp.tool()
def mas_lifecycle_check(project_id: str, phase: str) -> str:
    """Check lifecycle artifact invariants for a project phase. Returns YAML: passed, violations."""
    import yaml
    from core.engine.lifecycle_guard import LifecycleGuard
    result = LifecycleGuard().check_phase_artifacts(phase, resolve_project_dir(project_id))
    return yaml.dump({"passed": result.passed, "violations": result.violations},
                     default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_output_lint(agent_output: str) -> str:
    """Lint an agent-output string for verbosity / wire-protocol issues. Returns YAML: passed, findings."""
    import yaml
    from core.engine.output_linter import OutputLinter
    result = OutputLinter().lint(agent_output)
    return yaml.dump({"passed": result.passed, "findings": result.findings},
                     default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_skill_recommendations(project_id: str, phase: str = "") -> str:
    """Skill recommendations triggered by a project's current state/phase. Returns YAML list."""
    import yaml
    from core.engine.skill_trigger import SkillTriggerPolicy
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    recs = SkillTriggerPolicy().recommendations_for(
        state=sm.load(), phase=phase or None, project_dir=resolve_project_dir(project_id))
    return yaml.dump([{"skill": r.skill, "reason": r.reason, "required": r.required, "rule_id": r.rule_id}
                      for r in recs], default_flow_style=False, allow_unicode=True)


@mcp.tool()
def mas_consultation_required(project_id: str) -> str:
    """Return the consultation requirements triggered by a project's current state. Returns YAML list."""
    import yaml
    from core.engine.consultation_gate import ConsultationGate
    sm = _get_sm(project_id)
    if not sm.exists():
        return f"Error: Project '{project_id}' not found."
    reqs = ConsultationGate().required_for(state=sm.load())
    return yaml.dump([{"rule_id": r.rule_id, "decision_type": r.decision_type,
                       "consultants": r.consultants, "required": r.required} for r in reqs],
                     default_flow_style=False, allow_unicode=True)


def main() -> None:
    """Console entrypoint for the `mas-server` script (MCP stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
