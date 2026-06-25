"""
MAS CLI — Governed Multi-Agent Delivery System
Entry point: uv run mas <command>

Commands
--------
  init      Create and initialize a new project
  doctor    Run runtime and environment diagnostics
  resume    Resume a project from checkpoint/state
  status    Show project status and workflow state
  state     Read a value from shared state
  pending   List pending handoffs
  snapshot  Save a timestamped snapshot of shared state
  roster    Show the capability registry summary
"""

import re
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

import click
import yaml

from core.paths import mas_root
ROOT = mas_root()

# Load .env at the REPO ROOT (ROOT is mas/; the .env lives one level up) so
# ANTHROPIC_API_KEY is available to agent_runner / `mas run`. (Previously loaded
# ROOT/.env = mas/.env which never exists, so the key was never read from .env.)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(ROOT.parent / ".env")
except Exception:
    pass  # optional dependency / no .env present — proceed without it

# Ensure CLI output (which uses — / → / box chars) doesn't crash on a Windows
# cp1252 console. Reconfigure stdio to UTF-8 with replacement as a safe fallback.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass  # best-effort console UTF-8; older streams lack reconfigure()

# Max slug length (lowercase alphanum + hyphens)
_MAX_SLUG_LEN = 40
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
_FULL_ID_RE = re.compile(r"^proj-\d{8}-\d{3}-.+$")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_projects_dir() -> Path:
    from core.config import get_projects_dir
    return get_projects_dir()


def _resolve_project_dir(project_id: str) -> Path:
    """Resolve a project_id to its dir (flat or family-nested). See config.resolve_project_dir."""
    from core.utils.config import resolve_project_dir
    return resolve_project_dir(project_id, projects_root=_get_projects_dir())


def _execution_mode_label() -> str:
    """Which execution path the engine will use by default.

    'manual (no API calls)' — the default Claude Code workflow; only `mas run` calls the
    Anthropic API, and only when ANTHROPIC_API_KEY is set.
    """
    if os.getenv("ANTHROPIC_API_KEY"):
        return "manual (no API calls) — `mas run` available (ANTHROPIC_API_KEY set)"
    return "manual (no API calls) — default; `mas run` needs ANTHROPIC_API_KEY"


def _slugify(text: str) -> str:
    """Turn free-form text into a URL-safe slug (max _MAX_SLUG_LEN chars)."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:_MAX_SLUG_LEN]


def _next_sequence(projects_dir: Path, date_str: str) -> int:
    """Scan existing project dirs for today's date prefix and return max+1.

    Walks both flat and family-nested layouts so sequence numbers stay unique
    regardless of how folders are organized (F2b)."""
    from core.utils.config import iter_project_dirs
    prefix = f"proj-{date_str}-"
    max_seq = 0
    for d in iter_project_dirs(projects_root=projects_dir):
        if d.name.startswith(prefix):
            # Extract sequence: proj-YYYYMMDD-NNN-slug → NNN
            parts = d.name.split("-", 3)  # ['proj', 'YYYYMMDD', 'NNN', 'slug...']
            if len(parts) >= 3:
                try:
                    max_seq = max(max_seq, int(parts[2]))
                except ValueError:
                    pass
    return max_seq + 1


def _generate_project_id(slug: str) -> str:
    """Generate proj-YYYYMMDD-NNN-slug from a slug."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    seq = _next_sequence(_get_projects_dir(), date_str)
    return f"proj-{date_str}-{seq:03d}-{slug}"


def _require_project(project_id: str) -> Path:
    """Return project dir or exit with a clear error. Resolves flat or family-nested."""
    from core.utils.config import resolve_project_dir
    p = resolve_project_dir(project_id, projects_root=_get_projects_dir())
    if not p.exists():
        click.echo(f"[error] Project '{project_id}' not found in {_get_projects_dir()}", err=True)
        sys.exit(1)
    return p


def _load_state(project_id: str) -> dict:
    from core.engine.shared_state_manager import SharedStateManager
    sm = SharedStateManager(project_id)
    return sm.load()


def _handoff_acceptance_status(handoff: dict) -> str:
    """Read handoff acceptance status across expanded and compact variants."""
    acceptance = handoff.get("acceptance")
    if isinstance(acceptance, dict):
        status = acceptance.get("status")
        if isinstance(status, str) and status:
            return status
    compact = handoff.get("acc")
    if isinstance(compact, str) and compact:
        return compact
    status = handoff.get("status")
    if isinstance(status, str):
        return status
    return ""


def _pending_handoffs_from_state(state: dict) -> list[dict]:
    wf = state.get("workflow", {})
    history = wf.get("handoff_history", [])
    return [h for h in history if _handoff_acceptance_status(h) == "pending"]


def _assemble_prompt(project_id: str, agent_id: str, state: dict) -> str:
    agents_dir = ROOT.parent / "agents"
    from core.engine.prompt_assembler import PromptAssembler
    from core.engine.agent_ids import normalize_agent_id
    canonical_agent_id = normalize_agent_id(agent_id) or agent_id
    assembler = PromptAssembler(agents_dir=agents_dir)
    try:
        prompt = assembler.assemble(canonical_agent_id, state)
    except FileNotFoundError:
        click.echo(f"[error] Agent template not found for '{canonical_agent_id}' in {agents_dir}", err=True)
        sys.exit(1)
    # The assembler already computed the exact token count of the prompt it built.
    # In manual (Claude Code) mode this is the INPUT-token cost of the upcoming turn —
    # capture it so manual-mode work isn't counted as zero (ip-drift-004). Output/
    # reasoning tokens live in the Claude Code session and must be added via
    # `mas log-tokens --completion`. Non-fatal.
    try:
        from core.db import record_manual_tokens
        record_manual_tokens(
            project_id, canonical_agent_id,
            tokens_prompt=int(getattr(assembler, "last_token_count", 0) or 0),
            tokens_completion=0,
            note=f"prompt assembled for {canonical_agent_id} (manual-mode input)",
        )
    except Exception as exc:  # never block prompt assembly on telemetry
        logger.debug("prompt-token capture failed (non-blocking): %s", exc)
    return prompt


def _emit_prompt(project_id: str, agent_id: str, assembled: str) -> None:
    header = f"# Agent: {agent_id}  |  Project: {project_id}\n# Prompt length: {len(assembled)} chars\n#" + "-" * 60
    out = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout
    out.write((header + "\n" + assembled + "\n").encode("utf-8", errors="replace"))


def _resolve_sqlite_path(db_url: str) -> Path:
    raw = db_url.replace("sqlite:///", "", 1)
    p = Path(raw)
    if not p.is_absolute():
        p = (ROOT.parent / p).resolve()
    return p


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

class _MasGroup(click.Group):
    """CLI group that turns 'workspace not initialized' into friendly guidance.

    In installed (pip) mode the framework's config lives in a workspace created by
    `mas init-workspace`. Before that exists, config loads raise FileNotFoundError
    on system_config.yaml; convert those to a clean message, not a traceback.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except FileNotFoundError as exc:
            from core.paths import is_installed, workspace_root
            if is_installed() and "system_config.yaml" in str(exc):
                raise click.ClickException(
                    "No MAS workspace found. Run `mas init-workspace` to create one "
                    f"(default {workspace_root()}, or set MAS_HOME), then retry."
                ) from exc
            raise


@click.group(cls=_MasGroup)
@click.version_option("0.3.0", prog_name="mas")
def main():
    """Governed Multi-Agent Delivery System."""


# ---------------------------------------------------------------------------
# mas db (subgroup)
# ---------------------------------------------------------------------------

@main.group()
def db():
    """Database maintenance commands."""


# ---------------------------------------------------------------------------
# mas init
# ---------------------------------------------------------------------------

@main.command()
@click.argument("name_or_id")
@click.option("--request-id", default=None,
              help="Optional request ID (auto-generated if omitted)")
@click.option("--mode", default="lite",
              type=click.Choice(["standard", "lite"], case_sensitive=False),
              help="Project mode: 'lite' (default — 3-phase: intake → execution → "
                   "closed, no consultation; best for small/well-scoped tasks) or "
                   "'standard' (9-phase, full governance; escalate for high-risk, "
                   "multi-deliverable, or ambiguous work).")
@click.option("--target-area", default=None, metavar="AREA",
              help="Repo area this project mainly touches (e.g. mas/core/engine, "
                   "skills/notebooklm, docs). Enables `mas rollup --area <AREA>` "
                   "grouping/filtering so related work is easy to find.")
def init(name_or_id: str, request_id: str, mode: str, target_area: str | None):
    """Initialize a new project and its shared state.

    NAME_OR_ID can be either a human-readable slug (e.g. 'website-redesign')
    or a full project ID (e.g. 'proj-YYYYMMDD-NNN-website-redesign').

    If a slug is provided, the system generates the full project ID
    with today's date and next available sequence number.

    Examples:
        mas init session-scheduler
        mas init --mode=lite quick-fix
        mas init proj-YYYYMMDD-NNN-session-scheduler
    """
    from core.engine.shared_state_manager import SharedStateManager

    # Determine project_id: if it looks like a full ID, use as-is; else generate
    if _FULL_ID_RE.match(name_or_id):
        project_id = name_or_id
    else:
        slug = _slugify(name_or_id)
        if not slug:
            click.echo("[error] Invalid slug — must contain at least one alphanumeric character.", err=True)
            sys.exit(1)
        project_id = _generate_project_id(slug)

    if request_id is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        request_id = f"req-{ts}"

    # Auto-allocate into a family subfolder so new projects don't drift back to flat
    # (Q1). Family = target_area > theme keyword > slug heuristic. Nest when the family
    # folder already exists OR a target_area was given; otherwise stay flat (singleton).
    projects_root = _get_projects_dir()
    slug_for_family = project_id.split("-", 3)[-1] if project_id.startswith("proj-") else project_id
    try:
        from core.engine.cross_project import family_for
        family = family_for(slug_for_family, target_area=target_area)
        family_dir = projects_root / family
        if target_area or family_dir.is_dir():
            projects_root = family_dir   # create the project nested under its family
    except Exception:
        pass

    sm = SharedStateManager(project_id, projects_root=projects_root)
    if sm.project_dir.exists():
        click.echo(f"[warn] Project '{project_id}' already exists — skipping init.", err=True)
        sys.exit(0)

    sm.initialize(request_id=request_id, mode=mode)

    # Record the target repo area (if given) so projects can be grouped/queried by
    # where they make changes — `mas rollup --area <AREA>` (F2).
    if target_area:
        sm.write("master_orchestrator", "project_definition", "target_area", target_area)

    # Mirror `close`: record an init event to the queryable event store so the
    # project lifecycle is symmetric in episodic.db (not only in the flat audit.log).
    try:
        from core.engine.event_recorder import EventRecorder
        EventRecorder().record_simple(
            project_id=project_id,
            actor="master_orchestrator",
            action_type="project_initialized",
            intent=f"Project initialized in {mode} mode",
            phase="intake",
            payload={"request_id": request_id, "mode": mode, "target_area": target_area},
        )
    except Exception as exc:
        click.echo(f"[warn] init event not recorded to DB: {exc}", err=True)

    # Lineage → reuse (P4): if a prior project in the same family has a PROJECT_SUMMARY,
    # surface it so the new project builds on prior work instead of cold re-deriving.
    # Recorded as an informational assumption (NOT an open_question) so it never trips
    # the close-time open-questions lifecycle guard — it's a hint, not a blocker.
    predecessor = None
    try:
        from core.engine.cross_project import find_predecessor
        predecessor = find_predecessor(project_id)
        if predecessor:
            sm.append("master_orchestrator", "decisions", "assumptions", {
                "assumption_id": "lineage-reuse",
                "stated_by": "master_orchestrator",
                "description": (f"Related prior work available: latest sibling in family "
                                f"'{predecessor['family']}' is {predecessor['project_id']} "
                                f"— review {predecessor['summary_path']} during intake to "
                                f"reuse context instead of re-deriving."),
                "context": "lineage_reuse",
            })
    except Exception:
        predecessor = None

    mode_tag = f" [{mode}]" if mode == "lite" else ""
    click.echo(f"[ok] Project initialized{mode_tag}: {sm.project_dir}")
    click.echo(f"     Project ID  : {project_id}")
    click.echo(f"     State file  : {sm.state_path}")
    click.echo(f"     Request ID  : {request_id}")
    click.echo(f"     Mode        : {mode}")
    click.echo(f"     Execution   : {_execution_mode_label()}")
    if predecessor:
        click.echo(f"     Related     : {predecessor['project_id']} "
                   f"(family '{predecessor['family']}') — reuse {predecessor['summary_path']}")


# ---------------------------------------------------------------------------
# mas init-workspace — scaffold a writable workspace from the installed wheel
# ---------------------------------------------------------------------------

# Read-only framework assets a workspace needs, grouped by their source root.
_WORKSPACE_REPO_ITEMS = ["agents", "skills"]                  # under repo root
_WORKSPACE_MAS_ITEMS = [
    "system_config.yaml", "policies", "templates",
    "roster", "foundation", "domains",
]                                                             # under mas/
_WORKSPACE_RUNTIME_DIRS = ["projects", "data", "logs", "working_state"]  # writable, empty


@main.command("init-workspace")
@click.option("--path", "target", default=None, metavar="DIR",
              help="Workspace directory (default: $MAS_HOME, else ~/.mas).")
@click.option("--force", is_flag=True, default=False,
              help="Overwrite framework files that already exist in the workspace.")
def init_workspace(target: str | None, force: bool):
    """Create a writable MAS workspace from the framework's bundled files.

    A pip-installed wheel ships agents/skills/policies/templates/roster as
    read-only package data. This copies them into a workspace ($MAS_HOME, default
    ~/.mas) that mirrors the source-tree layout and creates the runtime dirs, so
    `mas init/status/prompt/doctor` work outside a git clone. Safe to re-run:
    existing files are kept unless --force is given.

    Example: mas init-workspace
             mas init-workspace --path ./my-workspace --force
    """
    import shutil
    from core.paths import bundled_dir, workspace_root, mas_root, repo_root

    dest = Path(target).expanduser().resolve() if target else workspace_root()

    bundled = bundled_dir()
    if bundled.exists():
        repo_src, mas_src = bundled, bundled / "mas"
        source_label = f"bundled package data ({bundled})"
    else:
        repo_src, mas_src = repo_root(), mas_root()
        source_label = f"source tree ({repo_src})"

    if dest == repo_src or dest == mas_src.parent:
        click.echo(f"[error] Workspace target {dest} is the source itself — choose another --path.", err=True)
        sys.exit(1)

    # Never copy runtime/secret junk that may sit on disk in a source-tree copy.
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", ".venv", "browser_state",
        "auth_info.json", "*.db", "*.sqlite", "*.sqlite3", "*.log",
    )

    click.echo(f"[mas init-workspace] dest : {dest}")
    click.echo(f"                     from : {source_label}")
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    skipped: list[Path] = []
    missing: list[str] = []

    def _place(src: Path, dst: Path) -> None:
        if not src.exists():
            missing.append(src.name)
            return
        if dst.exists() and not force:
            skipped.append(dst)
            return
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied.append(dst)

    for name in _WORKSPACE_REPO_ITEMS:
        _place(repo_src / name, dest / name)
    for name in _WORKSPACE_MAS_ITEMS:
        _place(mas_src / name, dest / "mas" / name)
    for name in _WORKSPACE_RUNTIME_DIRS:
        (dest / "mas" / name).mkdir(parents=True, exist_ok=True)

    click.echo(f"[ok] copied {len(copied)} item(s); skipped {len(skipped)} existing; runtime dirs ready.")
    if skipped and not force:
        click.echo("     (re-run with --force to overwrite existing framework files)")
    if missing:
        click.echo(f"[warn] source missing: {', '.join(sorted(set(missing)))}", err=True)

    if (dest / "mas" / "system_config.yaml").exists():
        click.echo("[ok] workspace ready.")
        if target:
            click.echo(f"     Point MAS at it: set MAS_HOME={dest}, then run `mas doctor`.")
        else:
            click.echo("     Run `mas doctor` to verify.")
    else:
        click.echo("[error] system_config.yaml missing after init — workspace incomplete.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# mas doctor
# ---------------------------------------------------------------------------

def _doctor_project_health(project_id: str) -> list[tuple[str, str, str]]:
    """Return health check tuples (status, name, detail) for a project."""
    from core.utils.log_helpers import query_events as _qe
    checks: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append((status, name, detail))

    from core.utils.config import resolve_project_dir
    projects_dir = _get_projects_dir()
    project_dir = resolve_project_dir(project_id, projects_root=projects_dir)

    # 1. Project exists
    if not project_dir.exists():
        add("fail", "project_exists", f"Project directory not found: {project_dir}")
        return checks
    add("ok", "project_exists", str(project_dir))

    # 2. Load shared state
    state_path = project_dir / "shared_state.yaml"
    try:
        with state_path.open(encoding="utf-8") as f:
            state = yaml.safe_load(f) or {}
    except Exception as exc:
        add("fail", "shared_state", f"Cannot read shared_state.yaml: {exc}")
        return checks
    add("ok", "shared_state", "shared_state.yaml readable")

    phase = state.get("core_identity", {}).get("current_phase", "unknown")
    status_val = state.get("core_identity", {}).get("status", "unknown")
    add("ok", "phase", f"phase={phase}  status={status_val}")

    # 3. Artifact contracts check
    contracts_path = ROOT / "policies" / "artifact_contracts.yaml"
    if contracts_path.exists():
        try:
            with contracts_path.open(encoding="utf-8") as f:
                contracts = yaml.safe_load(f) or {}
            phase_contract = contracts.get("phases", {}).get(phase, {})
            required = phase_contract.get("required", [])
            missing = [r for r in required if not (project_dir / r).exists()]
            if missing:
                for m in missing:
                    add("fail", "artifact", f"Missing required artifact: {m}")
            else:
                add("ok", "artifacts", f"All {len(required)} required artifacts present for phase={phase}")
        except Exception as exc:
            add("warn", "artifacts", f"Cannot check artifact contracts: {exc}")
    else:
        add("warn", "artifacts", "artifact_contracts.yaml not found")

    # 4. Open handoffs
    pending_handoffs = state.get("workflow", {}).get("pending_handoffs", [])
    if pending_handoffs:
        add("warn", "open_handoffs", f"{len(pending_handoffs)} open handoff(s): {[h.get('handoff_id','?') for h in pending_handoffs]}")
    else:
        add("ok", "open_handoffs", "No open handoffs")

    # 5. Snapshots after close
    if status_val == "closed":
        snapshots = list(project_dir.glob("shared_state_snapshot_*.yaml"))
        if snapshots:
            add("warn", "snapshots_after_close", f"{len(snapshots)} snapshot(s) remain after close")
        else:
            add("ok", "snapshots_after_close", "No stale snapshots")
        final = project_dir / "final_shared_state.yaml"
        if final.exists():
            add("ok", "final_state", "final_shared_state.yaml present")
        else:
            add("warn", "final_state", "final_shared_state.yaml missing for closed project")

    # 6. Required consultation from episodic.db
    try:
        req_events = _qe(project_id=project_id, action_type="consultation_required", limit=20)
        synth_events = _qe(project_id=project_id, action_type="consultation_synthesis", limit=20)
        if req_events and not synth_events:
            add("warn", "consultation", f"{len(req_events)} required consultation(s) with no synthesis recorded")
        elif req_events:
            add("ok", "consultation", f"{len(req_events)} required, {len(synth_events)} synthesis recorded")
        else:
            add("ok", "consultation", "No required consultation events recorded")
    except Exception as exc:
        add("warn", "consultation", f"Cannot query consultation events: {exc}")

    # 7. Required skills skipped
    try:
        rec_events = _qe(project_id=project_id, action_type="skill_recommended", limit=50)
        used_events = _qe(project_id=project_id, action_type="skill_invoked", limit=50)
        skipped_events = _qe(project_id=project_id, action_type="skill_skipped", limit=50)
        required_rec = [e for e in rec_events if (e.get("payload") or {}).get("required")]
        if required_rec and not used_events:
            add("warn", "skills", f"{len(required_rec)} required skill(s) recommended but none invoked")
        elif skipped_events:
            add("warn", "skills", f"{len(skipped_events)} skill invocation(s) skipped")
        else:
            add("ok", "skills", f"{len(rec_events)} recommended, {len(used_events)} invoked")
    except Exception as exc:
        add("warn", "skills", f"Cannot query skill events: {exc}")

    # 8. Decision / task store consistency (G3)
    try:
        from core.engine.consistency_check import check_project as _consistency
        report = _consistency(project_id, projects_root=projects_dir)
        if report.ok:
            add("ok", "consistency", "decision and task stores consistent")
        else:
            for f in report.findings:
                sev = "fail" if f.get("severity") == "high" else "warn"
                if f.get("check") == "decisions":
                    add(sev, "consistency", f"{f['direction']} decisions {f.get('ids')}: {f['detail']}")
                else:
                    add(sev, "consistency",
                        f"task store drift state_only={f.get('state_only')} board_only={f.get('board_only')}")
    except Exception as exc:
        add("warn", "consistency", f"Cannot check consistency: {exc}")

    return checks


def _doctor_next_action(checks: list[tuple[str, str, str]], project_id: str, state: dict | None = None) -> str:
    """Return one recommended next action based on health checks."""
    fails = [name for s, name, _ in checks if s == "fail"]
    warns = [name for s, name, _ in checks if s == "warn"]
    if "project_exists" in fails:
        return f"Run `mas init {project_id}` or check the project ID."
    if "artifact" in fails:
        return "Create missing required artifacts before advancing the phase."
    if "consistency" in fails:
        return "Reconcile decision/task store drift (decisions on disk are missing from canonical state)."
    if "consultation" in warns:
        return "Trigger required consultation: add `consultation_trigger` to next Master response."
    if "skills" in warns:
        return "Run recommended required skill(s) before the next phase action."
    if "open_handoffs" in warns:
        return "Resolve or accept pending handoffs via `mas pending`."
    if "snapshots_after_close" in warns:
        return "Run `mas close` again or manually delete stale snapshot files."
    if "final_state" in warns:
        return "Re-run `mas close` to write final_shared_state.yaml."
    if warns:
        return f"Address warnings: {', '.join(warns[:3])}."
    return "Project looks healthy. Continue with the current phase."


@main.command()
@click.argument("project_id", required=False, default=None)
def doctor(project_id: str | None):
    """Run runtime and environment diagnostics for MAS CLI usage.

    When PROJECT_ID is provided, also performs project-health checks:
    artifact contracts, open handoffs, consultation requirements,
    skill usage, and post-close state.

    Example: mas doctor
             mas doctor proj-YYYYMMDD-NNN-my-project
    """
    checks: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append((status, name, detail))

    # Default execution path: manual (no API calls). Only `mas run` calls the Anthropic API.
    add("ok", "execution_mode", _execution_mode_label())

    # API key is optional for manual Claude Code flow, required for `mas run`.
    if os.getenv("ANTHROPIC_API_KEY"):
        add("ok", "api_key", "ANTHROPIC_API_KEY detected")
    else:
        add("warn", "api_key", "ANTHROPIC_API_KEY missing (required for `mas run`, optional for manual `mas prompt` flow)")

    env_path = ROOT.parent / ".env"
    if env_path.exists():
        add("ok", "env_file", f"Found {env_path}")
    else:
        add("warn", "env_file", f"Missing {env_path}; copy from .env.example if needed")

    # Required paths for CLI/project flow.
    projects_dir = _get_projects_dir()
    required_paths = [
        ("projects_dir", projects_dir),
        ("policies_dir", ROOT / "policies"),
        ("templates_dir", ROOT / "templates"),
        ("foundation_dir", ROOT / "foundation"),
        ("agents_dir", ROOT.parent / "agents"),
    ]
    for name, path in required_paths:
        if path.exists() and path.is_dir():
            add("ok", name, str(path))
        else:
            add("fail", name, f"Missing required directory: {path}")

    master_template = ROOT.parent / "agents" / "master_orchestrator.md"
    if master_template.exists():
        add("ok", "master_template", str(master_template))
    else:
        add("fail", "master_template", f"Missing required agent template: {master_template}")

    # Registry + inventory + runtime mode (P5 enrichment — all non-fatal)
    registry_path = ROOT / "roster" / "registry_index.yaml"
    try:
        _reg = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        _ra = len(_reg.get("registry", {}).get("agents", []))
        _rs = _reg.get("counts", {}).get("active_skills", 0)
        add("ok", "registry", f"{_ra} agents, {_rs} skills registered")
    except Exception:
        add("warn", "registry", f"registry_index.yaml not readable at {registry_path}")

    agents_dir = ROOT.parent / "agents"
    skills_dir = ROOT.parent / "skills"
    _da = len([p for p in agents_dir.glob("*.md") if p.name != "_utilities.md"]) if agents_dir.is_dir() else 0
    _ds = len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.is_dir() else 0
    add("ok", "inventory", f"{_da} agent docs, {_ds} skill packages on disk")

    # Project-folder layout audit (non-fatal): surface ungrouped, split-brain,
    # and stub project dirs so they can be cleaned up deliberately.
    try:
        from core.utils.config import audit_project_layout
        layout = audit_project_layout(Path(projects_dir))
        nfam = len(layout["families"])
        add("ok", "project_layout",
            f"{nfam} family folder(s); {len(layout['ungrouped'])} ungrouped project(s)")
        if layout["split_brain"]:
            add("warn", "project_split_brain",
                f"{len(layout['split_brain'])} project id(s) in multiple locations: "
                f"{layout['split_brain']}")
        if layout["stubs"]:
            add("warn", "project_stubs",
                f"{len(layout['stubs'])} project dir(s) with no shared_state.yaml: "
                f"{layout['stubs']}")
    except Exception as exc:
        add("warn", "project_layout", f"layout audit failed: {exc}")

    # Runtime mode: source-tree clone, or installed wheel backed by a workspace.
    from core.paths import is_installed, is_workspace_initialized, workspace_root
    if not is_installed():
        add("ok", "runtime_mode", "source-tree (run via uv / activated venv from repo root)")
    elif is_workspace_initialized():
        add("ok", "runtime_mode", f"installed wheel + workspace at {workspace_root()}")
    else:
        add("warn", "runtime_mode",
            f"installed wheel but no workspace — run `mas init-workspace` (creates {workspace_root()})")

    # Backend checks
    try:
        from core.runtime_config import get_database_backend, get_vector_backend
        db_backend = get_database_backend()
        vector_backend = get_vector_backend()
    except Exception as exc:
        add("fail", "runtime_config", str(exc))
        db_backend = {"active_provider": "sqlite", "url": "sqlite:///mas/data/episodic.db"}
        vector_backend = {"enabled": False, "provider": "chromadb"}

    try:
        from core.utils.log_helpers import init_db
        provider = db_backend.get("active_provider")
        url = db_backend.get("url", "")
        if provider == "sqlite":
            db_path = _resolve_sqlite_path(url)
            init_db(db_path=db_path)
            add("ok", "database_backend", f"sqlite ready at {db_path}")
        elif provider == "postgresql":
            from core.adapters import postgres_store
            with postgres_store.connect(url) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            add("ok", "database_backend", "postgresql reachable")
        else:
            add("warn", "database_backend", f"Unknown provider '{provider}'")
    except Exception as exc:
        add("fail", "database_backend", str(exc))

    try:
        provider = vector_backend.get("provider")
        enabled = bool(vector_backend.get("enabled"))
        if not enabled:
            add("ok", "vector_backend", "disabled")
        elif provider != "chromadb":
            add("warn", "vector_backend", f"enabled with unsupported provider '{provider}'")
        else:
            import chromadb  # type: ignore
            host = vector_backend.get("host")
            if host:
                client = chromadb.HttpClient(host=host, port=vector_backend.get("port") or 8000)
                client.list_collections()
                add("ok", "vector_backend", f"chromadb http reachable at {host}:{vector_backend.get('port') or 8000}")
            else:
                persist = Path(vector_backend.get("persist_directory"))
                persist.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(persist))
                client.get_or_create_collection(vector_backend.get("collection", "mas-agent-context"))
                add("ok", "vector_backend", f"chromadb persistent store ready at {persist}")
    except Exception as exc:
        add("fail", "vector_backend", str(exc))

    # Project-health checks (only when project_id supplied)
    project_state: dict | None = None
    health_checks: list[tuple[str, str, str]] = []
    if project_id:
        health_checks = _doctor_project_health(project_id)
        # Extract state for next-action recommendation
        from core.utils.config import resolve_project_dir
        state_path2 = resolve_project_dir(project_id, projects_root=_get_projects_dir()) / "shared_state.yaml"
        try:
            with state_path2.open(encoding="utf-8") as f:
                project_state = yaml.safe_load(f) or {}
        except Exception:
            project_state = None

    status_icons = {"ok": "[ok]", "warn": "[warn]", "fail": "[fail]"}
    ok_count = warn_count = fail_count = 0
    click.echo("\nMAS Doctor — Environment")
    for status, name, detail in checks:
        if status == "ok":
            ok_count += 1
        elif status == "warn":
            warn_count += 1
        else:
            fail_count += 1
        click.echo(f"{status_icons.get(status, '[?]')} {name}: {detail}")

    if health_checks:
        click.echo(f"\nProject Health — {project_id}")
        h_ok = h_warn = h_fail = 0
        for status, name, detail in health_checks:
            if status == "ok":
                h_ok += 1
            elif status == "warn":
                h_warn += 1
            else:
                h_fail += 1
            click.echo(f"{status_icons.get(status, '[?]')} {name}: {detail}")

        overall = "ok" if h_fail == 0 and h_warn == 0 else ("degraded" if h_fail == 0 else "critical")
        click.echo(f"\nProject health: {overall}  ok={h_ok} warn={h_warn} fail={h_fail}")

        next_action = _doctor_next_action(health_checks, project_id, project_state)
        click.echo(f"Suggested next action: {next_action}")

        ok_count += h_ok
        warn_count += h_warn
        fail_count += h_fail

    click.echo(f"\nSummary: ok={ok_count} warn={warn_count} fail={fail_count}")
    if fail_count:
        sys.exit(1)


# ---------------------------------------------------------------------------
# mas resume
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--show-prompt", is_flag=True, default=False,
              help="Print the assembled prompt for the next suggested agent.")
def resume(project_id: str, show_prompt: bool):
    """Resume a project from checkpoint + shared state with a concrete next step."""
    project_dir = _require_project(project_id)
    checkpoint_path = project_dir / "CHECKPOINT.md"
    generated_checkpoint = False
    if not checkpoint_path.exists():
        try:
            from core.engine.checkpoint_writer import CheckpointWriter
            checkpoint_path = CheckpointWriter(project_id).write()
            generated_checkpoint = True
        except Exception as exc:
            click.echo(f"[warn] Could not generate checkpoint: {exc}")

    state = _load_state(project_id)
    _record_resume_skill_recommendations(project_id, state, project_dir)
    ci = state.get("core_identity", {})
    phase = ci.get("current_phase", "—")
    status = ci.get("status", "—")
    pending_handoffs = _pending_handoffs_from_state(state)

    click.echo(f"\n[mas resume] {project_id}")
    click.echo(f"  checkpoint: {checkpoint_path}")
    if generated_checkpoint:
        click.echo("  checkpoint generated from current shared state")
    click.echo(f"  status    : {status}")
    click.echo(f"  phase     : {phase}")
    click.echo(f"  pending   : {len(pending_handoffs)}")

    if pending_handoffs:
        click.echo("\nPending handoffs:")
        for h in pending_handoffs:
            handoff_id = h.get("handoff_id") or h.get("id") or "unknown"
            from_agent = h.get("from_agent") or h.get("from") or "—"
            to_agent = h.get("to_agent") or h.get("to") or "—"
            task_description = h.get("task_description") or h.get("task") or ""
            click.echo(f"  [{handoff_id}] {from_agent} → {to_agent} ({task_description})")
        click.echo("\nNext action: resolve pending handoff(s) before progressing phases.")
        return

    if status == "closed":
        click.echo("\nNext action: project is already closed; no further orchestration required.")
        return

    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
    loop = OrchestrationLoop(LoopConfig(project_id=project_id))
    next_agent = loop._determine_next_agent(state)
    click.echo(f"\nNext action: invoke {next_agent}.")

    if show_prompt:
        assembled = _assemble_prompt(project_id, next_agent, state)
        _emit_prompt(project_id, next_agent, assembled)


def _record_resume_skill_recommendations(project_id: str, state: dict, project_dir: Path) -> None:
    """Record project-resume skill recommendations as typed DB events."""
    try:
        from core.engine.skill_trigger import SkillTriggerPolicy
        from core.engine.event_recorder import EventRecorder
        policy = SkillTriggerPolicy()
        recs = policy.recommendations_for(
            state=state,
            project_dir=project_dir,
            event="project_resume",
        )
        recorder = EventRecorder()
        phase = state.get("core_identity", {}).get("current_phase", "")
        for rec in recs:
            recorder.record_simple(
                project_id=project_id,
                actor="system",
                action_type="skill_recommended",
                intent=f"Recommended skill on resume: {rec.skill}",
                phase=phase,
                rule_id=rec.rule_id,
                payload={
                    "skill": rec.skill,
                    "required": rec.required,
                    "reason": rec.reason,
                    "event": "project_resume",
                },
            )
    except Exception as exc:
        logger.debug("resume event recording failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# mas status
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
def status(project_id: str):
    """Show the current status and phase of a project.

    Example: mas status proj-YYYYMMDD-NNN
    """
    _require_project(project_id)
    state = _load_state(project_id)

    ci = state.get("core_identity", {})
    wf = state.get("workflow", {})
    meta = state.get("_meta", {})

    phase = ci.get("current_phase", "—")
    owner = wf.get("current_owner", "—")
    proj_status = ci.get("status", "—")
    updated = ci.get("updated_at") or meta.get("updated_at", "—")
    proj_mode = wf.get("mode", "standard")

    pending_handoffs = _pending_handoffs_from_state(state)
    completed_phases = wf.get("completed_phases", [])
    violations = state.get("_meta", {}).get("governance_violations", [])

    mode_tag = " [lite]" if proj_mode == "lite" else ""
    # Token usage summary (D1/D3)
    from core.db import query_token_usage
    usage = query_token_usage(project_id)
    calls     = usage.get("calls", 0)
    total_tok = usage.get("total", 0)
    try:
        from core.runtime_config import get_database_backend, get_vector_backend
        db_backend = get_database_backend()
        vector_backend = get_vector_backend()
    except Exception:
        db_backend = {"active_provider": "sqlite"}
        vector_backend = {"enabled": False, "provider": "chromadb"}

    click.echo(f"\nProject  : {project_id}")
    click.echo(f"Status   : {proj_status}")
    click.echo(f"Phase    : {phase}{mode_tag}")
    click.echo(f"Mode     : {proj_mode}")
    click.echo(f"Owner    : {owner}")
    click.echo(f"Updated  : {updated}")
    _phase_names = [
        p.get("phase", "?") if isinstance(p, dict) else str(p)
        for p in completed_phases
    ]
    click.echo(f"Completed phases : {', '.join(_phase_names) or 'none'}")
    click.echo(f"Pending handoffs : {len(pending_handoffs)}")
    click.echo(f"Violations       : {len(violations)}")
    vector_label = vector_backend["provider"] if vector_backend.get("enabled") else "disabled"
    click.echo(f"Storage          : db={db_backend['active_provider']} vector={vector_label}")

    if calls > 0:
        click.echo(f"Agent calls      : {calls}")
    click.echo(f"Tokens (total)   : {total_tok:,}")

    if pending_handoffs:
        click.echo("\nPending handoffs:")
        for h in pending_handoffs:
            handoff_id = h.get("handoff_id") or h.get("id") or "unknown"
            from_agent = h.get("from_agent") or h.get("from") or "—"
            to_agent = h.get("to_agent") or h.get("to") or "—"
            task_description = h.get("task_description") or h.get("task") or ""
            click.echo(
                f"  [{handoff_id}] "
                f"{from_agent} → {to_agent} "
                f"({task_description})"
            )


# ---------------------------------------------------------------------------
# mas state
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.argument("path")
def state(project_id: str, path: str):
    """Read a value from shared state using dot-notation path.

    Example: mas state proj-YYYYMMDD-NNN project_definition.project_goal
    """
    _require_project(project_id)
    from core.engine.shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    value = sm.read(path)

    if value is None:
        click.echo(f"[none] {path} not set")
    else:
        click.echo(yaml.dump({path: value}, default_flow_style=False,
                              allow_unicode=True).strip())


# ---------------------------------------------------------------------------
# mas pending
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
def pending(project_id: str):
    """List all pending handoffs for a project.

    Example: mas pending proj-YYYYMMDD-NNN
    """
    _require_project(project_id)
    from core.engine.handoff_engine import HandoffEngine
    from core.engine.shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    engine = HandoffEngine()
    pending_list = engine.get_pending(sm)

    if not pending_list:
        click.echo("[ok] No pending handoffs.")
        return

    click.echo(f"\n{len(pending_list)} pending handoff(s):")
    for h in pending_list:
        click.echo(
            f"\n  ID      : {h['handoff_id']}"
            f"\n  From    : {h['from_agent']}"
            f"\n  To      : {h['to_agent']}"
            f"\n  Phase   : {h.get('phase', '—')}"
            f"\n  Task    : {h.get('task_description', '—')}"
            f"\n  Created : {h.get('created_at', '—')}"
        )


# ---------------------------------------------------------------------------
# mas snapshot
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--phase", default="manual",
              help="Phase label for the snapshot filename (default: manual)")
def snapshot(project_id: str, phase: str):
    """Save a timestamped snapshot of shared state.

    Example: mas snapshot proj-YYYYMMDD-NNN --phase pre-planning
    """
    _require_project(project_id)
    from core.engine.shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    snap_path = sm.snapshot(phase=phase)
    click.echo(f"[ok] Snapshot saved: {snap_path}")


# ---------------------------------------------------------------------------
# mas close
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
def close(project_id: str):
    """Finalize and close a project.

    Example: mas close proj-YYYYMMDD-NNN-mas-run-fixes-db-consolidation
    """
    _require_project(project_id)
    from core.engine.shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    state = sm.load()

    current_phase = state.get("core_identity", {}).get("current_phase", "")
    current_status = state.get("core_identity", {}).get("status", "active")

    if _pending_handoffs_from_state(state):
        click.echo("[error] Cannot close project with pending handoffs.", err=True)
        sys.exit(1)

    if current_status == "closed":
        click.echo(f"[info] Project is already closed — ensuring final artifacts and cleanup.")
    else:
        sm.snapshot(current_phase or "pre-close")
        sm.write("master_orchestrator", "core_identity", "status", "closed")
        if current_phase not in ("closed", ""):
            sm.write("master_orchestrator", "core_identity", "current_phase", "closed")
            sm.system_append("workflow", "completed_phases", current_phase)
        click.echo(f"[ok] Project closed.")

    # Reload state after writes and preserve final human/readable artifacts.
    state = sm.load()
    closed_at = datetime.now(timezone.utc).isoformat()
    try:
        from core.utils.config import load_config
        storage_cfg = load_config().get("storage", {})
    except Exception:
        storage_cfg = {}
    retention = storage_cfg.get("snapshot_retention", {}) or {}

    if retention.get("write_final_state_copy", True):
        final_state_path = sm.project_dir / "final_shared_state.yaml"
        with final_state_path.open("w", encoding="utf-8") as f:
            yaml.dump(state, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        click.echo(f"[ok] Final state written: {final_state_path}")

    closed_path = sm.project_dir / "CLOSED.md"
    if not closed_path.exists():
        closed_path.write_text(
            "\n".join([
                "# Project Closed",
                "",
                f"- project_id: {project_id}",
                f"- closed_at: {closed_at}",
                f"- final_phase: {state.get('core_identity', {}).get('current_phase', 'closed')}",
                f"- status: {state.get('core_identity', {}).get('status', 'closed')}",
                "",
            ]),
            encoding="utf-8",
        )
    click.echo(f"[ok] Closure report: {closed_path}")

    # imp-002: surface task-board drift — warn (do not block) when tasks remain
    # in a non-terminal state at close, so evaluation metrics stay honest.
    try:
        from core.engine.task_board import TaskBoard

        _terminal = {"completed", "failed", "cancelled"}
        _open_tasks = [
            t for t in TaskBoard(project_id).list_tasks()
            if str(t.get("status")) not in _terminal
        ]
        if _open_tasks:
            _example = _open_tasks[0]
            click.echo(
                f"[warn] task-board: {len(_open_tasks)} task(s) not in a terminal "
                f"state at close (e.g. {_example.get('task_id')}="
                f"{_example.get('status')}). Reconcile statuses to keep evaluation "
                "metrics accurate.",
                err=True,
            )
    except Exception as exc:
        click.echo(f"[warn] task-board reconciliation check skipped: {exc}", err=True)

    try:
        from core.engine.lifecycle_guard import LifecycleGuard
        guard_result = LifecycleGuard().check_close(sm.project_dir, state)
        for warning in guard_result.warnings:
            click.echo(f"[warn] {warning.get('invariant')}: {warning.get('detail', '')}", err=True)
        if guard_result.blocked:
            for violation in guard_result.violations:
                click.echo(f"[error] {violation.get('invariant')}: {violation}", err=True)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"[warn] Close invariant check failed: {exc}", err=True)

    try:
        from core.engine.event_recorder import EventRecorder
        er = EventRecorder()
        delete_on_close = retention.get("delete_on_close", True)
        keep_latest = int(retention.get("keep_latest_after_close", 0) or 0)
        deleted = sm.cleanup_snapshots(keep_latest=keep_latest) if delete_on_close else []
        er.record_simple(
            project_id=project_id,
            actor="master_orchestrator",
            action_type="snapshots_cleaned",
            intent=f"Cleaned {len(deleted)} snapshot(s) on project close",
            payload={
                "deleted_count": len(deleted),
                "deleted_paths": [str(p) for p in deleted],
                "keep_latest": keep_latest,
            },
        )
        if deleted:
            click.echo(f"[ok] Cleaned {len(deleted)} snapshot(s).")
        er.record_simple(
            project_id=project_id,
            actor="master_orchestrator",
            action_type="project_closed",
            intent="Project finalized and closed",
            phase="closed",
            payload={
                "closed_at": closed_at,
                "closed_path": str(closed_path),
                "final_state_path": str(sm.project_dir / "final_shared_state.yaml"),
            },
        )
        er.record_simple(
            project_id=project_id,
            actor="master_orchestrator",
            action_type="phase_transition",
            intent="Project transitioned to closed status",
            phase="closed",
        )
    except Exception as exc:
        click.echo(f"[warn] Post-close event recording failed: {exc}", err=True)

    try:
        from core.utils.registry_seed import seed
        seed()
        click.echo("[ok] DB registry re-seeded.")
    except Exception as exc:
        logger.debug("registry re-seed failed (best-effort; close not blocked): %s", exc)


# ---------------------------------------------------------------------------
# mas rebuild-state
# ---------------------------------------------------------------------------

@main.command("rebuild-state")
@click.argument("project_id")
@click.option("--limit", default=25, show_default=True,
              help="Number of recent DB events to project into shared state.")
def rebuild_state(project_id: str, limit: int):
    """Rebuild compact working-state projection from DB events and artifacts.

    This preserves the existing shared_state.yaml schema and adds/refreshes
    compact `current` and `recent` projection sections.
    """
    _require_project(project_id)
    from core.engine.shared_state_manager import SharedStateManager
    from core.db import query_project_history, query_events
    import json

    sm = SharedStateManager(project_id)
    state = sm.load()
    ci = state.get("core_identity", {})
    pd = state.get("project_definition", {})
    wf = state.get("workflow", {})
    decisions = state.get("decisions", {})
    artifacts = state.get("artifacts", {})
    execution = state.get("execution", {})

    recent_events = query_project_history(project_id, limit=limit)

    def _payload(row: dict) -> dict:
        try:
            raw = json.loads(row.get("payload") or "{}")
            return raw.get("params", {}).get("inputs", raw)
        except Exception:
            return {}

    skill_rows = query_events(project_id=project_id, action_type="skill_recommended", limit=50)
    required_skills = []
    for row in skill_rows:
        payload = _payload(row)
        if payload.get("required"):
            required_skills.append({
                "skill": payload.get("skill"),
                "rule_id": payload.get("rule_id") or payload.get("rule"),
                "reason": payload.get("reason", ""),
            })

    consult_rows = query_events(project_id=project_id, action_type="consultation_required", limit=50)
    required_consultations = []
    for row in consult_rows:
        payload = _payload(row)
        if not payload.get("satisfied", False):
            required_consultations.append({
                "rule_id": payload.get("rule_id") or _payload(row).get("rule"),
                "decision_type": payload.get("decision_type", ""),
                "consultants": payload.get("consultants", []),
            })

    pending_handoffs = _pending_handoffs_from_state(state)
    state["current"] = {
        "objective": pd.get("project_goal") or pd.get("problem_statement") or "",
        "next_action": "resolve pending handoffs" if pending_handoffs else "continue orchestration",
        "active_agent": wf.get("current_owner", "master_orchestrator"),
        "active_handoffs": pending_handoffs,
        "open_questions": decisions.get("open_questions", []),
        "active_risks": execution.get("delivery_risks", []),
        "required_consultations": required_consultations,
        "required_skills": required_skills,
    }
    state["recent"] = {
        "last_checkpoint": str((_resolve_project_dir(project_id) / "CHECKPOINT.md")),
        "last_decision": (decisions.get("decision_log") or [])[-1] if decisions.get("decision_log") else None,
        "last_artifacts": (artifacts.get("documents") or [])[-5:],
        "last_events": [
            {
                "timestamp": row.get("timestamp"),
                "actor": row.get("agent_id"),
                "action_type": row.get("action_type"),
                "intent": row.get("intent"),
            }
            for row in recent_events[-5:]
        ],
    }
    state.setdefault("_meta", {})["rebuilt_at"] = datetime.now(timezone.utc).isoformat()
    sm._save(state)

    try:
        from core.engine.event_recorder import EventRecorder
        EventRecorder().record_simple(
            project_id=project_id,
            actor="system",
            action_type="shared_state_rebuilt",
            intent="Rebuilt compact shared-state projection from episodic events",
            payload={"event_count": len(recent_events), "limit": limit},
            phase=ci.get("current_phase"),
        )
    except Exception as exc:
        logger.debug("events-command telemetry failed (non-blocking): %s", exc)

    click.echo(f"[ok] Rebuilt shared_state.yaml projection for {project_id}")


# ---------------------------------------------------------------------------
# mas roster
# ---------------------------------------------------------------------------

@main.command()
@click.option("--status", "filter_status", default=None,
              help="Filter by status: active | probation | retired")
def roster(filter_status: str):
    """Show the capability registry summary.

    Example: mas roster
             mas roster --status active
    """
    registry_path = ROOT / "roster" / "registry_index.yaml"
    if not registry_path.exists():
        click.echo("[error] registry_index.yaml not found", err=True)
        sys.exit(1)

    with open(registry_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    reg = data.get("registry", {})
    counts = data.get("counts", {})

    click.echo(f"\nRoster Registry  v{reg.get('version', '?')}")
    click.echo(f"Last updated     : {reg.get('last_updated') or '—'}")
    click.echo(f"Active agents    : {counts.get('active_agents', 0)}")
    click.echo(f"Active skills    : {counts.get('active_skills', 0)}")
    click.echo(f"Retired agents   : {counts.get('retired_agents', 0)}")
    click.echo(f"Spawned total    : {counts.get('spawned_total', 0)}")

    agents = reg.get("agents", [])
    if filter_status:
        agents = [a for a in agents if a.get("status") == filter_status]

    if agents:
        click.echo(f"\nAgents ({len(agents)}):")
        for a in agents:
            perf = a.get("performance_score")
            perf_str = f"  score={perf:.1f}" if perf is not None else ""
            click.echo(
                f"  [{a.get('status', '?'):10}] {a['agent_id']}  "
                f"{a.get('trust_tier', '?')}{perf_str}"
            )
    else:
        click.echo("\nNo agents registered yet.")


# ---------------------------------------------------------------------------
# mas events
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--limit", default=20, show_default=True, help="Max events to show")
@click.option("--action-type", default=None, metavar="TYPE",
              help="Filter by action type (e.g. handoff_created)")
def events(project_id: str, limit: int, action_type: str | None):
    """Show recent agent events for a project.

    Example: mas events proj-YYYYMMDD-NNN
             mas events proj-YYYYMMDD-NNN --action-type handoff_created --limit 5
    """
    _require_project(project_id)
    from core.db import query_project_history

    rows = query_project_history(project_id, limit=limit)
    if action_type:
        rows = [e for e in rows if e.get("action_type") == action_type]

    if not rows:
        click.echo("[ok] No events found.")
        return

    click.echo(f"\nEvents — {project_id}  ({len(rows)} shown)")
    click.echo(f"{'Timestamp':<20} {'Actor':<25} {'Action Type':<30} Intent")
    click.echo("-" * 100)
    for e in rows:
        ts = str(e.get("timestamp") or e.get("created_at") or "—")[:19]
        actor = str(e.get("agent_id") or "—")[:24]
        atype = str(e.get("action_type") or "—")[:29]
        intent = str(e.get("intent") or "")[:50]
        click.echo(f"{ts:<20} {actor:<25} {atype:<30} {intent}")


# ---------------------------------------------------------------------------
# mas tokens
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
def tokens(project_id: str):
    """Show token usage summary for a project.

    Example: mas tokens proj-YYYYMMDD-NNN
    """
    _require_project(project_id)
    from core.db import query_token_usage

    usage = query_token_usage(project_id)
    total      = usage.get("total", 0)
    prompt     = usage.get("total_prompt", 0)
    completion = usage.get("total_completion", 0)
    calls      = usage.get("calls", 0)

    click.echo(f"\nToken usage — {project_id}")
    click.echo(f"  Total calls      : {calls}")
    click.echo(f"  Prompt tokens    : {prompt:,}")
    click.echo(f"  Completion tokens: {completion:,}")
    click.echo(f"  Total tokens     : {total:,}")


# ---------------------------------------------------------------------------
# mas log-tokens — record manual-mode (Claude Code) token usage (ip-drift-004)
# ---------------------------------------------------------------------------

@main.command("log-tokens")
@click.argument("project_id")
@click.option("--agent", default="master_orchestrator", show_default=True,
              help="Agent the tokens are attributed to.")
@click.option("--prompt", "prompt_tokens", type=int, default=0, help="Prompt/input tokens.")
@click.option("--completion", "completion_tokens", type=int, default=0, help="Completion/output tokens.")
@click.option("--estimate-file", type=click.Path(exists=True), default=None,
              help="Estimate prompt tokens from a text/prompt file (heuristic count).")
@click.option("--note", default="", help="Optional note for the log entry.")
def log_tokens(project_id: str, agent: str, prompt_tokens: int,
               completion_tokens: int, estimate_file: str | None, note: str):
    """Record manual-mode (Claude Code) token usage so it isn't counted as zero.

    Manual mode burns real tokens the engine never auto-records (it only tracks tokens
    when it calls the API itself in `mas run`). Log them so `mas tokens` and the
    comms-efficiency metric reflect actual cost.

    Example: mas log-tokens proj-... --prompt 12000 --completion 3000
             mas log-tokens proj-... --estimate-file intake/brief.md --completion 800
    """
    _require_project(project_id)
    from core.db import record_manual_tokens

    if estimate_file:
        from core.utils.token_counter import TokenCounter
        text = Path(estimate_file).read_text(encoding="utf-8", errors="replace")
        prompt_tokens = (prompt_tokens or 0) + TokenCounter().count(text)

    if prompt_tokens <= 0 and completion_tokens <= 0:
        click.echo("[error] Provide --prompt/--completion counts or --estimate-file.", err=True)
        sys.exit(1)

    record_manual_tokens(project_id, agent, prompt_tokens, completion_tokens, note)
    total = prompt_tokens + completion_tokens
    click.echo(f"[ok] Logged {total:,} manual tokens "
               f"(prompt={prompt_tokens:,}, completion={completion_tokens:,}) to {project_id}.")


# ---------------------------------------------------------------------------
# mas rollup — cross-project aggregation & lineage (ip-audit-001)
# ---------------------------------------------------------------------------

@main.command()
@click.option("--lineage", "show_lineage", is_flag=True, default=False,
              help="Group related projects into families instead of listing each.")
@click.option("--area", "area_filter", default=None, metavar="AREA",
              help="Filter to projects whose target_area contains AREA (substring); "
                   "with --lineage, group by target_area instead of slug family.")
@click.option("--limit", default=30, show_default=True, help="Max rows to show.")
def rollup(show_lineage: bool, area_filter: str | None, limit: int):
    """Aggregate all projects across the event store (cross-project view).

    Without flags: one row per project (area, events, handoffs, decisions, closed?).
    With --lineage: groups related efforts into families so duplicate/sequential
    projects (e.g. the many ml-autograder or data-pipeline runs) surface as one chain.
    With --area <AREA>: filter to projects that touch that repo area (e.g. mas/core/engine).

    Example: mas rollup
             mas rollup --lineage
             mas rollup --area mas/core/engine
    """
    from core.engine.cross_project import rollup as _rollup, lineage as _lineage

    def _matches(area_val: str | None) -> bool:
        if not area_filter:
            return True
        return bool(area_val) and area_filter.lower() in area_val.lower()

    if show_lineage and area_filter:
        # Group projects by target_area (the area-centric view the user asked for).
        rows = [s for s in _rollup() if _matches(s.get("target_area"))]
        by_area: dict[str, list] = {}
        for s in rows:
            by_area.setdefault(s.get("target_area") or "(unset)", []).append(s)
        if not by_area:
            click.echo(f"[ok] No projects with target_area matching '{area_filter}'.")
            return
        click.echo(f"\nProject Rollup by Area — filter '{area_filter}'")
        click.echo(f"{'Area':<32} {'#':>3} {'Closed':>6} {'Events':>7}")
        click.echo("-" * 56)
        for area, items in sorted(by_area.items(), key=lambda kv: -len(kv[1])):
            closed = sum(1 for i in items if i["closed"])
            ev = sum(i["events"] for i in items)
            click.echo(f"{area:<32} {len(items):>3} {closed:>6} {ev:>7}")
        return

    if show_lineage:
        fams = _lineage()[:limit]
        if not fams:
            click.echo("[ok] No projects found.")
            return
        click.echo(f"\nProject Lineage — {len(fams)} families")
        click.echo(f"{'Family':<28} {'#':>3} {'Closed':>6} {'Events':>7}  Chain")
        click.echo("-" * 100)
        for f in fams:
            chain = " -> ".join(f["chain"])
            if len(chain) > 50:
                chain = chain[:47] + "..."
            click.echo(f"{f['family']:<28} {f['count']:>3} {f['closed']:>6} {f['total_events']:>7}  {chain}")
        return

    rows = [s for s in _rollup() if _matches(s.get("target_area"))][:limit]
    if not rows:
        msg = f" matching area '{area_filter}'" if area_filter else ""
        click.echo(f"[ok] No projects found{msg}.")
        return
    title = f"Project Rollup — {len(rows)} projects" + (f" (area ~ '{area_filter}')" if area_filter else "")
    click.echo(f"\n{title}")
    click.echo(f"{'Project':<46} {'Area':<18} {'Events':>6} {'HO':>4} {'Dec':>4} {'Closed':>6}")
    click.echo("-" * 92)
    for s in rows:
        area = (s.get("target_area") or "-")[:17]
        click.echo(
            f"{s['project_id']:<46} {area:<18} {s['events']:>6} {s['handoffs']:>4} "
            f"{s['decisions']:>4} {('yes' if s['closed'] else '-'):>6}"
        )


# ---------------------------------------------------------------------------
# mas reorg — organize project folders into family subfolders (F2b)
# ---------------------------------------------------------------------------

@main.command()
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="Actually move folders (default is a dry-run preview).")
def reorg(do_apply: bool):
    """Reorganize mas/projects into family subfolders (projects/<family>/<id>/).

    Groups related projects (e.g. all ml-autograder runs) under one folder so related
    work is easy to find. Singletons stay flat. The path resolver handles both layouts,
    so this is safe and reversible. Default is a dry-run; pass --apply to move.

    Example: mas reorg            # preview
             mas reorg --apply     # perform
    """
    from core.engine.cross_project import reorg_projects
    summary = reorg_projects(_get_projects_dir(), dry_run=not do_apply)
    verb = "Would move" if summary["dry_run"] else "Moved"
    click.echo(f"\n{verb} {summary['moves']} project(s) into "
               f"{len(summary['families'])} family folder(s): {', '.join(summary['families'])}")
    for m in summary["detail"]:
        click.echo(f"  {m['project_id']:<48} -> {m['family']}/")
    if summary["dry_run"] and summary["moves"]:
        click.echo("\nRe-run with --apply to perform the move.")


# ---------------------------------------------------------------------------
# mas sync — reconcile manual-mode project state into the queryable event store (P2)
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id", required=False, default=None)
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be synced without writing.")
def sync(project_id: str | None, dry_run: bool):
    """Reconcile manual-mode project state into agent_events (cross-project queryable).

    Claude Code manual mode logs state/decisions/phases to the flat audit.log, not the
    queryable DB. This synthesizes the missing canonical events from each project's
    shared_state.yaml so manual projects appear in `mas rollup` / `mas events`.
    Idempotent — re-running never double-counts.

    Example: mas sync                 # reconcile all projects
             mas sync --dry-run        # preview
             mas sync proj-2026... # reconcile one project
    """
    from core.engine.state_reconciler import reconcile_all, reconcile_project
    from core.engine.shared_state_manager import SharedStateManager

    if project_id:
        _require_project(project_id)
        state = SharedStateManager(project_id).load()
        res = reconcile_project(project_id, state, dry_run=dry_run)
        verb = "would add" if dry_run else "added"
        click.echo(f"[ok] {project_id}: {verb} {res.get('added', 0)} event(s) "
                   f"{res.get('kinds', res.get('skipped',''))}")
        return

    summary = reconcile_all(dry_run=dry_run)
    verb = "Would sync" if dry_run else "Synced"
    click.echo(f"\n{verb}: {summary['events_added']} event(s) across "
               f"{summary['projects_updated']}/{summary['projects_scanned']} project(s)")
    for d in summary["details"]:
        click.echo(f"  {d['project_id']:<48} +{d['added']} {d.get('kinds', [])}")


# ---------------------------------------------------------------------------
# mas explain
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.argument("action_id", required=False)
@click.option("--last", "show_last", is_flag=True, default=False,
              help="Explain the most recent event for the project.")
def explain(project_id: str, action_id: str | None, show_last: bool):
    """Show full detail for a single agent event by action_id.

    Example: mas explain proj-YYYYMMDD-NNN abc123def456
             mas explain proj-YYYYMMDD-NNN --last
    """
    _require_project(project_id)
    from core.db import query_by_action_id, query_project_history

    if show_last or not action_id:
        rows = query_project_history(project_id, limit=1)
        event = rows[-1] if rows else None
    else:
        event = query_by_action_id(action_id)
    if not event:
        click.echo(f"[error] Event '{action_id or 'last'}' not found.", err=True)
        sys.exit(1)

    click.echo(f"\nEvent Detail — {action_id or event.get('id') or 'last'}")
    click.echo("-" * 60)
    for key, value in event.items():
        if isinstance(value, (dict, list)):
            import json
            click.echo(f"  {key}:\n{yaml.dump(value, default_flow_style=False, allow_unicode=True).rstrip()}")
        else:
            click.echo(f"  {key}: {value}")


# ---------------------------------------------------------------------------
# mas check-artifacts
# ---------------------------------------------------------------------------

@main.command("check-artifacts")
@click.argument("project_id")
@click.option("--phase", default=None, metavar="PHASE",
              help="Override phase to check (default: current phase from state)")
def check_artifacts(project_id: str, phase: str | None):
    """Check that required phase artifacts exist on disk.

    Example: mas check-artifacts proj-YYYYMMDD-NNN
             mas check-artifacts proj-YYYYMMDD-NNN --phase planning
    """
    _require_project(project_id)
    project_dir = _resolve_project_dir(project_id)

    state = _load_state(project_id)
    current_phase = phase or state.get("core_identity", {}).get("current_phase", "")
    if not current_phase:
        click.echo("[error] Could not determine project phase.", err=True)
        sys.exit(1)

    contracts_path = ROOT / "policies" / "artifact_contracts.yaml"
    if not contracts_path.exists():
        click.echo(f"[warn] artifact_contracts.yaml not found at {contracts_path}")
        return

    with open(contracts_path, encoding="utf-8") as f:
        contracts = yaml.safe_load(f)

    phase_contract = contracts.get("phases", {}).get(current_phase)
    if phase_contract is None:
        click.echo(f"[info] No artifact contract defined for phase '{current_phase}'.")
        return

    required = phase_contract.get("required", [])
    optional = phase_contract.get("optional", [])

    missing = [a for a in required if not (project_dir / a).exists()]
    present = [a for a in required if (project_dir / a).exists()]
    opt_present = [a for a in optional if (project_dir / a).exists()]

    click.echo(f"\nArtifact check — {project_id}  phase={current_phase}")
    click.echo(f"Required : {len(present)}/{len(required)} present")
    for a in present:
        click.echo(f"  [ok]   {a}")
    for a in missing:
        click.echo(f"  [MISS] {a}")
    if opt_present:
        click.echo(f"Optional : {len(opt_present)} found")
        for a in opt_present:
            click.echo(f"  [opt]  {a}")

    if missing:
        click.echo(f"\n[fail] {len(missing)} required artifact(s) missing.")
        sys.exit(1)
    else:
        click.echo("\n[ok] All required artifacts present.")


# ---------------------------------------------------------------------------
# mas check-config
# ---------------------------------------------------------------------------

@main.command("check-config")
def check_config():
    """Validate MAS YAML configuration files (policies, system_config, foundation).

    Example: mas check-config
    """
    checks: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append((status, name, detail))

    # System config
    sc_path = ROOT / "system_config.yaml"
    if sc_path.exists():
        try:
            with open(sc_path, encoding="utf-8") as f:
                yaml.safe_load(f)
            add("ok", "system_config.yaml", str(sc_path))
        except yaml.YAMLError as e:
            add("fail", "system_config.yaml", f"YAML parse error: {e}")
    else:
        add("fail", "system_config.yaml", f"Missing: {sc_path}")

    # Policies
    policies_dir = ROOT / "policies"
    if policies_dir.exists():
        for p in sorted(policies_dir.glob("*.yaml")):
            try:
                with open(p, encoding="utf-8") as f:
                    yaml.safe_load(f)
                add("ok", f"policies/{p.name}", str(p))
            except yaml.YAMLError as e:
                add("fail", f"policies/{p.name}", f"YAML parse error: {e}")
    else:
        add("fail", "policies_dir", f"Missing directory: {policies_dir}")

    # Foundation
    foundation_dir = ROOT / "foundation"
    if foundation_dir.exists():
        for p in sorted(foundation_dir.glob("*.yaml")):
            try:
                with open(p, encoding="utf-8") as f:
                    yaml.safe_load(f)
                add("ok", f"foundation/{p.name}", str(p))
            except yaml.YAMLError as e:
                add("fail", f"foundation/{p.name}", f"YAML parse error: {e}")
    else:
        add("warn", "foundation_dir", f"Missing: {foundation_dir}")

    # Registry
    reg_path = ROOT / "roster" / "registry_index.yaml"
    if reg_path.exists():
        try:
            with open(reg_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            agent_count = len(data.get("registry", {}).get("agents", []))
            add("ok", "registry_index.yaml", f"{agent_count} agents registered")
        except yaml.YAMLError as e:
            add("fail", "registry_index.yaml", f"YAML parse error: {e}")
    else:
        add("fail", "registry_index.yaml", f"Missing: {reg_path}")

    status_icons = {"ok": "[ok]", "warn": "[warn]", "fail": "[fail]"}
    ok_count = warn_count = fail_count = 0
    click.echo("\nMAS Config Check")
    for status, name, detail in checks:
        if status == "ok":
            ok_count += 1
        elif status == "warn":
            warn_count += 1
        else:
            fail_count += 1
        click.echo(f"{status_icons.get(status, '[?]')} {name}: {detail}")

    click.echo(f"\nSummary: ok={ok_count} warn={warn_count} fail={fail_count}")
    if fail_count:
        sys.exit(1)


# ---------------------------------------------------------------------------
# mas skill-usage
# ---------------------------------------------------------------------------

@main.command("skill-usage")
@click.argument("project_id")
def skill_usage(project_id: str):
    """Show per-project skill recommendation and invocation history from episodic.db.

    Example: mas skill-usage proj-YYYYMMDD-NNN
    """
    _require_project(project_id)
    from core.db import query_events
    import json

    action_types = [
        "skill_recommended",
        "skill_requested",
        "skill_invoked",
        "skill_completed",
        "skill_skipped",
    ]
    rows = []
    for action_type in action_types:
        rows.extend(query_events(project_id=project_id, action_type=action_type, limit=200))
    rows.sort(key=lambda r: r.get("id", 0))

    if not rows:
        click.echo("[ok] No skill usage events recorded for this project.")
        return

    click.echo(f"\nSkill usage — {project_id}  ({len(rows)} events)")
    click.echo(f"{'Timestamp':<20} {'Actor':<24} {'Event':<20} {'Skill':<22} Detail")
    click.echo("-" * 110)
    for row in rows:
        payload = {}
        try:
            raw = json.loads(row.get("payload") or "{}")
            payload = raw.get("params", {}).get("inputs", raw)
        except Exception:
            payload = {}
        ts = str(row.get("timestamp", "—"))[:19]
        actor = str(row.get("agent_id", "—"))[:23]
        action_type = str(row.get("action_type", "—"))[:19]
        skill = str(payload.get("skill") or payload.get("skill_name") or payload.get("name") or "—")[:21]
        detail = str(payload.get("outcome") or payload.get("reason") or row.get("intent") or "")[:55]
        click.echo(f"{ts:<20} {actor:<24} {action_type:<20} {skill:<22} {detail}")


# ---------------------------------------------------------------------------
# mas consultation-status
# ---------------------------------------------------------------------------

@main.command("consultation-status")
@click.argument("project_id")
def consultation_status(project_id: str):
    """Show consultation lifecycle state for a project from episodic.db.

    Example: mas consultation-status proj-YYYYMMDD-NNN
    """
    _require_project(project_id)
    from core.db import query_events
    import json

    state = _load_state(project_id)
    consult = state.get("consultation", {})

    requests = consult.get("consultation_requests", [])
    responses = consult.get("consultation_responses", [])
    synthesis = consult.get("synthesis", [])

    def _rows(action_type: str) -> list[dict]:
        return query_events(project_id=project_id, action_type=action_type, limit=100)

    def _payload(row: dict) -> dict:
        try:
            raw = json.loads(row.get("payload") or "{}")
            return raw.get("params", {}).get("inputs", raw)
        except Exception:
            return {}

    required_rows = _rows("consultation_required")
    requested_rows = _rows("consultation_requested")
    response_rows = _rows("consultation_response")
    synthesis_rows = _rows("consultation_synthesis")

    click.echo(f"\nConsultation status — {project_id}")
    click.echo(f"  Required events : {len(required_rows)}")
    click.echo(f"  Requested events: {len(requested_rows)}")
    click.echo(f"  Response events : {len(response_rows)}")
    click.echo(f"  Synthesis events: {len(synthesis_rows)}")
    click.echo(f"  Shared state    : requests={len(requests)} responses={len(responses)} syntheses={len(synthesis)}")

    if not (required_rows or requested_rows or requests):
        click.echo("\n[ok] No consultations initiated or required.")
        return

    if required_rows:
        click.echo("\nRequired consultations:")
        for row in sorted(required_rows, key=lambda r: r.get("id", 0)):
            payload = _payload(row)
            rule = payload.get("rule_id") or "—"
            dtype = payload.get("decision_type") or "—"
            satisfied = payload.get("satisfied", False)
            consultants = ", ".join(payload.get("consultants", [])) or "—"
            click.echo(f"  - {rule}  type={dtype}  satisfied={satisfied}")
            click.echo(f"    consultants: {consultants}")

    if requested_rows:
        click.echo("\nRequested consultations:")
        for row in sorted(requested_rows, key=lambda r: r.get("id", 0)):
            payload = _payload(row)
            rid = payload.get("request_id") or "—"
            dtype = payload.get("decision_type") or "—"
            consultants = ", ".join(payload.get("consultants", [])) or "—"
            click.echo(f"  - {rid}  type={dtype}")
            click.echo(f"    consultants: {consultants}")


# ---------------------------------------------------------------------------
# mas reopen
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--phase", default="execution",
              help="Phase to reopen into (default: execution)")
@click.option("--reason", default="", help="Reason for reopening the project.")
def reopen(project_id: str, phase: str, reason: str):
    """Reopen a closed project for additional work.

    Sets status back to 'active' and current_phase to the specified phase.
    Use with care — reopened projects lose their closed audit trail integrity.

    Example: mas reopen proj-YYYYMMDD-NNN
             mas reopen proj-YYYYMMDD-NNN --phase review
    """
    _require_project(project_id)
    from core.engine.shared_state_manager import SharedStateManager

    sm = SharedStateManager(project_id)
    state = sm.load()
    current_status = state.get("core_identity", {}).get("status", "active")

    if current_status != "closed":
        click.echo(f"[warn] Project is not closed (status={current_status}). No change made.")
        return

    sm.write("master_orchestrator", "core_identity", "status", "active")
    sm.write("master_orchestrator", "core_identity", "current_phase", phase)

    try:
        from core.engine.event_recorder import EventRecorder
        EventRecorder().record_simple(
            project_id=project_id,
            actor="master_orchestrator",
            action_type="project_reopened",
            intent=reason or f"Project reopened into phase={phase}",
            phase=phase,
            payload={"reason": reason, "phase": phase},
        )
    except Exception as exc:
        logger.debug("reject event recording failed (non-blocking): %s", exc)

    click.echo(f"[ok] Project reopened: status=active  phase={phase}")
    click.echo(f"     Note: re-run `mas snapshot` to capture the reopen state.")


# ---------------------------------------------------------------------------
# mas db rebuild-fts
# ---------------------------------------------------------------------------

@db.command("rebuild-fts")
def rebuild_fts():
    """Rebuild the FTS5 index from agent_events (safe to run at any time).

    Example: mas db rebuild-fts
    """
    from core.runtime_config import get_database_backend
    from core.utils.log_helpers import _get_connection, DB_PATH

    backend = get_database_backend()
    if backend["active_provider"] != "sqlite":
        click.echo("[warn] FTS rebuild is only relevant for the SQLite fallback store.")
        return
    conn = _get_connection(DB_PATH)
    try:
        conn.execute("INSERT INTO agent_events_fts(agent_events_fts) VALUES ('rebuild')")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0]
        click.echo(f"[ok] FTS5 index rebuilt — {count} rows indexed.")
    except Exception as exc:
        click.echo(f"[error] rebuild-fts failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@db.command("migrate-postgres")
def migrate_postgres():
    """Copy local SQLite events/shared-state/graph tables into configured PostgreSQL."""
    from core.runtime_config import get_database_backend
    from core.db import migrate_sqlite_to_postgres
    from core.utils.log_helpers import DB_PATH

    backend = get_database_backend()
    postgres_url = backend["url"] if backend["active_provider"] == "postgresql" else ""
    if not postgres_url.startswith("postgres"):
        click.echo(
            "[error] PostgreSQL is not active. Set MAS_DATABASE_PROVIDER=postgresql and MAS_DATABASE_URL first.",
            err=True,
        )
        sys.exit(1)
    try:
        stats = migrate_sqlite_to_postgres(DB_PATH, postgres_url)
    except Exception as exc:
        click.echo(f"[error] migrate-postgres failed: {exc}", err=True)
        sys.exit(1)
    click.echo(
        "[ok] PostgreSQL migration complete — "
        f"events={stats['agent_events']} shared_states={stats['shared_states']} "
        f"graph_nodes={stats['agent_graph']} graph_edges={stats['agent_graph_edges']}"
    )


# ---------------------------------------------------------------------------
# mas db migrate-graph
# ---------------------------------------------------------------------------

@db.command("migrate-graph")
def migrate_graph():
    """One-time import of legacy global_graph.yaml into agent_graph SQLite tables.

    GraphStore now writes to SQLite directly on every save() — this command is
    only needed to seed the tables from any pre-existing YAML that was written
    before the DB consolidation (proj-007).

    Creates agent_graph and agent_graph_edges tables if they do not exist.
    Migration is idempotent (INSERT OR REPLACE).

    Example:
        mas db migrate-graph
    """
    import yaml as _yaml
    from core.utils.log_helpers import _get_connection, DB_PATH

    # Try mas/global_graph.yaml first, then mas/data/global_graph.yaml (legacy)
    graph_path = ROOT / "global_graph.yaml"
    if not graph_path.exists():
        graph_path = ROOT / "data" / "global_graph.yaml"
    if not graph_path.exists():
        click.echo("[warn] No global_graph.yaml found — nothing to migrate. "
                   "(GraphStore now writes to SQLite directly on every save.)")
        return

    with open(graph_path, encoding="utf-8") as f:
        graph_data = _yaml.safe_load(f) or {}

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    conn = _get_connection(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_graph (
                id      TEXT PRIMARY KEY,
                type    TEXT,
                label   TEXT,
                meta    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_graph_edges (
                id          TEXT PRIMARY KEY,
                source      TEXT,
                target      TEXT,
                relation    TEXT,
                meta        TEXT
            )
        """)

        import json as _json
        node_count = 0
        for node in nodes:
            nid = node.get("id") or node.get("node_id", "")
            if not nid:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO agent_graph(id, type, label, meta) VALUES (?, ?, ?, ?)",
                (nid, node.get("type", ""), node.get("label", nid),
                 _json.dumps({k: v for k, v in node.items() if k not in ("id", "node_id", "type", "label")})),
            )
            node_count += 1

        edge_count = 0
        for edge in edges:
            eid = edge.get("id") or edge.get("edge_id", "")
            if not eid:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO agent_graph_edges(id, source, target, relation, meta) VALUES (?, ?, ?, ?, ?)",
                (eid, edge.get("source", ""), edge.get("target", ""),
                 edge.get("relation", edge.get("type", "")),
                 _json.dumps({k: v for k, v in edge.items()
                              if k not in ("id", "edge_id", "source", "target", "relation", "type")})),
            )
            edge_count += 1

        conn.commit()
        click.echo(f"[ok] Graph migrated — {node_count} nodes, {edge_count} edges written to {DB_PATH}.")
    except Exception as exc:
        click.echo(f"[error] migrate-graph failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# mas run — autonomous orchestration loop
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--max-steps", default=50, show_default=True,
              help="Hard stop after N agent steps.")
@click.option("--auto", is_flag=True, default=False,
              help="Skip human confirmation at phase boundaries.")
@click.option("--phase", "target_phase", default=None, metavar="PHASE",
              help="Stop after this phase completes (e.g. 'specification').")
def run(project_id: str, max_steps: int, auto: bool, target_phase: str | None):
    """Run the autonomous orchestration loop for a project.

    Drives the project through intake -> specification -> planning phases,
    pausing at each phase boundary for human confirmation (unless --auto).
    Integrates consultation and NotebookLM knowledge requests.

    Examples:

    \b
        mas run proj-YYYYMMDD-NNN-my-project
        mas run proj-YYYYMMDD-NNN-my-project --auto --max-steps 10
        mas run proj-YYYYMMDD-NNN-my-project --phase specification
    """
    _require_project(project_id)

    from core.engine.agent_runner import AgentRunner
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig, StopReason
    from core.runtime_config import get_database_backend, get_vector_backend

    runner = AgentRunner()
    if not runner.available:
        click.echo(
            "[error] Live execution is mandatory. Set ANTHROPIC_API_KEY before running `mas run`.",
            err=True,
        )
        sys.exit(1)

    db_backend = get_database_backend()
    vector_backend = get_vector_backend()

    config = LoopConfig(
        project_id=project_id,
        max_steps=max_steps,
        auto=auto,
        target_phase=target_phase,
    )

    click.echo(f"\n[mas run] {project_id}")
    if auto:
        click.echo("  mode: auto (phase boundaries skipped)")
    click.echo(f"  storage: db={db_backend['active_provider']} vector={'chromadb' if vector_backend.get('enabled') else 'disabled'}")
    click.echo("")

    loop = OrchestrationLoop(config)
    result = loop.run()

    click.echo(f"\n[mas run] stopped at step {result.stopped_at_step}")
    click.echo(f"  reason : {result.reason.value}")
    click.echo(f"  agent  : {result.last_agent}")
    click.echo(f"  phase  : {result.last_phase}")
    if result.message:
        click.echo(f"  message: {result.message}")

    if result.reason == StopReason.UNANIMOUS_RISK:
        click.echo("\n[GOVERNANCE] Unanimous high-risk — human review required.", err=True)
        sys.exit(2)
    elif result.reason == StopReason.CONSULTATION_REQUIRED:
        click.echo("\n[GOVERNANCE] Required consultation is pending.", err=True)
        sys.exit(2)
    elif result.reason == StopReason.HUMAN_ESCALATION:
        click.echo("\n[GOVERNANCE] Human escalation required.", err=True)
        sys.exit(2)
    elif result.reason == StopReason.ERROR:
        sys.exit(1)


# ---------------------------------------------------------------------------
# mas prompt — assemble the next agent prompt for any manual surface
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.argument("agent_id", required=False, default=None)
def prompt(project_id: str, agent_id: str | None):
    """Assemble and print the next agent's prompt for manual/provider-neutral mode.

    Use this command to get the governed prompt, run it in Claude Code, Codex,
    ChatGPT, Gemini, Copilot, LM Studio, Ollama, or another LLM surface, then
    apply the model's wire-format reply with `mas ingest`.

    If AGENT_ID is omitted, determines the next agent automatically from state.

    Examples:

    \b
        mas prompt proj-YYYYMMDD-NNN-mas-self-audit
        mas prompt proj-YYYYMMDD-NNN-mas-self-audit inquirer_agent
    """
    _require_project(project_id)

    state = _load_state(project_id)

    if agent_id is None:
        from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig
        dummy = LoopConfig(project_id=project_id)
        loop = OrchestrationLoop(dummy)
        agent_id = loop._determine_next_agent(state)
    else:
        from core.engine.agent_ids import normalize_agent_id
        agent_id = normalize_agent_id(agent_id) or agent_id

    assembled = _assemble_prompt(project_id, agent_id, state)
    _emit_prompt(project_id, agent_id, assembled)


# ---------------------------------------------------------------------------
# mas ingest — the other half of provider-agnostic manual mode (M-c)
# ---------------------------------------------------------------------------

@main.command()
@click.argument("project_id")
@click.option("--agent", "agent_id", default=None,
              help="Acting agent (default: the current phase's next agent).")
@click.option("--response-file", "response_file", default=None,
              help="File with the LLM's raw response (default: read stdin).")
@click.option("--show-prompt/--no-show-prompt", default=True,
              help="After applying, print the next agent prompt (default: yes).")
def ingest(project_id: str, agent_id: str | None, response_file: str | None, show_prompt: bool):
    """Ingest an LLM response (from ANY provider) and apply it to governed state.

    The provider-agnostic manual loop:  `mas prompt` -> run in any LLM -> `mas ingest`.
    Parses the wire block from the pasted/piped response, records the agent's work as a
    governed handoff, applies the next action (advance phase / delegate / escalate /
    consult / wait), then prints the next prompt so the loop continues.

    Example:

    \b
        mas prompt  proj-... > p.txt        # run p.txt in ChatGPT/Gemini/LM Studio/Claude
        mas ingest  proj-... < reply.txt    # paste that reply back; engine advances
    """
    _require_project(project_id)

    raw = Path(response_file).read_text(encoding="utf-8") if response_file else sys.stdin.read()
    if not raw.strip():
        click.echo("[error] empty response (use --response-file or pipe via stdin)", err=True)
        sys.exit(1)

    from core.engine.manual_loop import apply_ingest
    from core.engine.shared_state_manager import SharedStateManager
    from core.engine.orchestration_loop import OrchestrationLoop, LoopConfig

    try:
        res = apply_ingest(project_id, raw, agent_id)
    except Exception as exc:
        click.echo(f"[error] could not record handoff: {exc}", err=True)
        sys.exit(1)

    click.echo(
        f"[ingest] {project_id}  phase={res.phase_before} acting={res.acting_agent} "
        f"status={res.status or '—'} action={res.action}"
    )
    if res.parse_errors:
        click.echo(f"  parse warnings: {', '.join(res.parse_errors)}")
    if res.knowledge_request:
        click.echo(f"  KNOWLEDGE_REQUEST: {res.knowledge_request}")
    click.echo(
        f"  recorded handoff {res.handoff_id} "
        f"(decisions={res.decisions} artifacts={res.artifacts})"
    )

    if res.action == "advance_phase":
        click.echo(f"  phase advanced: {res.phase_before} -> {res.phase_after}")
    elif res.action == "delegate" and res.delegated_to:
        click.echo(f"  delegated to {res.delegated_to} (pending handoff {res.delegation_handoff_id or ''})")
    elif res.action == "delegate" and res.delegate_error:
        click.echo(f"[warn] delegate handoff failed: {res.delegate_error}")
    elif res.action == "escalate":
        click.echo("  ESCALATION required — human decision needed; phase not advanced.")
    elif res.action == "consult":
        click.echo("  CONSULTATION requested — run the consultant panel; phase not advanced.")
    else:
        click.echo(f"  no phase change (action={res.action}).")

    if not show_prompt:
        return

    # Emit the next prompt so the manual loop continues.
    sm = SharedStateManager(project_id)
    loop = OrchestrationLoop(LoopConfig(project_id=project_id))
    state = sm.load()
    cur_phase = state.get("core_identity", {}).get("current_phase", res.phase_after)
    if cur_phase == "closed" or state.get("core_identity", {}).get("status") == "closed":
        click.echo("\n[ingest] project closed — no further prompt.")
        return
    pend = _pending_handoffs_from_state(state)
    if pend:
        nxt = pend[0].get("to_agent") or pend[0].get("to") or loop._determine_next_agent(state)
    else:
        nxt = loop._determine_next_agent(state)
    click.echo(f"\n# NEXT: run this prompt in any LLM, then `mas ingest {project_id}` again\n")
    _emit_prompt(project_id, nxt, _assemble_prompt(project_id, nxt, state))


# ---------------------------------------------------------------------------
# mas registry (subgroup)
# ---------------------------------------------------------------------------

_REGISTRY_TABLES = {
    "mas_agents",
    "mas_skills",
    "mas_commands",
    "mas_templates",
    "mas_domains",
    "mas_codebase",
}


@main.group()
def registry():
    """Manage the MAS artifact registry (agents, skills, commands, templates, codebase)."""


@registry.command("seed")
def registry_seed():
    """Seed all registry tables from the current filesystem state.

    Example: mas registry seed
    """
    try:
        from core.utils.registry_seed import seed
        counts = seed()
        for table, n in counts.items():
            click.echo(f"  {table}: {n} rows")
        click.echo("[ok] Registry seeded.")
    except Exception as exc:
        click.echo(f"[error] {exc}", err=True)
        sys.exit(1)


@registry.command("list")
@click.option("--table", default="mas_agents", show_default=True,
              help="Registry table to list.")
def registry_list(table: str):
    """List rows from a registry table.

    Supported tables: mas_agents, mas_skills, mas_commands, mas_templates,
    mas_domains, mas_codebase.

    Example: mas registry list
             mas registry list --table mas_codebase
    """
    if table not in _REGISTRY_TABLES:
        click.echo(
            f"[error] Unknown table '{table}'. "
            f"Supported: {', '.join(sorted(_REGISTRY_TABLES))}",
            err=True,
        )
        sys.exit(1)

    from core.utils.log_helpers import DB_PATH, _get_connection
    conn = _get_connection(DB_PATH)
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        col_names = [desc[0] for desc in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]  # noqa: S608
    except Exception as exc:
        click.echo(f"[error] {exc}", err=True)
        conn.close()
        sys.exit(1)
    conn.close()

    if not rows:
        click.echo(f"[ok] No rows in {table}.")
        return

    # Select display columns per table
    if table == "mas_agents":
        display_cols = ["agent_id", "tier", "status", "last_score"]
    elif table == "mas_skills":
        display_cols = ["skill_id", "name", "status"]
    elif table == "mas_codebase":
        display_cols = ["file_id", "file_type", "project_id"]
    else:
        display_cols = col_names

    # Build index map: col_name -> position in row tuple
    col_index = {name: i for i, name in enumerate(col_names)}
    valid_cols = [c for c in display_cols if c in col_index]

    # Header
    click.echo(f"\n{table}  ({len(rows)} rows)")
    header = "  ".join(f"{c:<20}" for c in valid_cols)
    click.echo(header)
    click.echo("-" * len(header))

    for row in rows:
        parts = []
        for col in valid_cols:
            val = row[col_index[col]]
            val_str = str(val) if val is not None else ""
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            parts.append(f"{val_str:<20}")
        click.echo("  ".join(parts))


# ---------------------------------------------------------------------------
# Entry point guard (for `uv run python core/cli.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
