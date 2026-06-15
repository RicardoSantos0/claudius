"""
Cross-Project Rollup & Lineage (ip-audit-001)

Aggregates the (now-clean) event store + shared_states across all projects so related
efforts are visible as families instead of isolated islands. This is the aggregation
surface the self-audit found missing — projects were treated as separate because nothing
linked or summarised them.

Two views:
  - rollup():  per-project summary (status, phase, events, handoffs, decisions, closed?)
  - lineage(): groups projects into families by slug stem, so the N ml-autograder /
               data-pipeline / self-audit efforts surface as one lineage with a count
               and a chronological chain.

Pure-ish: reads the DB via the existing core.db helpers; no writes.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from core.db import DB_PATH, _get_connection

# proj-YYYYMMDD-NNN-<slug>  → capture date, seq, slug
_PROJ_RE = re.compile(r"^proj-(\d{8})-(\d{3})-(.+)$")

# Tokens stripped when deriving a lineage "family" key from a slug, so that
# e.g. ml-autograder-v2 / ml-autograder-sprint5 / ml-autograder-hardening all
# collapse to the family "ml-autograder".
_FAMILY_STOPWORDS = {
    "v1", "v2", "v3", "sprint", "sprint2", "sprint5", "sprint6", "sprint7",
    "milestone", "phase", "impl", "fix", "fixes", "cleanup", "refactor",
    "hardening", "calibration", "improvements", "improvement", "audit",
    "full", "lite", "ev", "a", "b", "c", "d", "002", "003",
}


def _family_key(slug: str) -> str:
    """Derive a coarse family key from a project slug.

    Keeps the leading 2 meaningful tokens (the stable theme), dropping trailing
    version/sprint/qualifier tokens. ml-autograder-milestone-c → 'ml-autograder'.
    """
    tokens = [t for t in slug.split("-") if t]
    meaningful = [t for t in tokens if t.lower() not in _FAMILY_STOPWORDS and not t.isdigit()]
    if not meaningful:
        meaningful = tokens
    return "-".join(meaningful[:2]) if meaningful else slug


# Theme map for coarse, stable family grouping. First matching theme wins (order
# matters: more specific patterns before generic ones). Keys are checked as substrings
# against the slug (and the project goal/brief as a fallback signal). This groups
# thematically-related projects whose slugs don't share leading tokens
# (e.g. db-semantic / db-ops / db-registry → "database"). Extend as themes emerge.
_FAMILY_THEMES = [
    ("ml-autograder",   ["ml-autograder", "autograder"]),
    ("mas-engine",      ["mas-self", "mas-project", "mas-analysis", "mas-improvement",
                          "mas-improvements", "mas-effectiveness", "mas-quality",
                          "mas-drift", "mas-runtime", "mas-run", "mas-followups",
                          "mas-trainer", "true-mas", "orchestration", "loop",
                          "drift", "quality-hardening",
                          # MAS-engine infrastructure work (folded in from singletons):
                          "session-scheduler", "session", "commsopt", "comms",
                          "agent-knowledge", "knowledge", "memory-and", "memory",
                          "copilot"]),
    ("database",        ["db-semantic", "db-ops", "db-registry", "capability-db",
                          "autograder-db", "db-", "episodic", "sqlite"]),
    ("config-repo",     ["config-repo", "claude-config", "repo-cleanup", "repo"]),
    ("infarmed",        ["infarmed"]),
    ("testing",         ["smoke-test", "smoke", "operational-readiness", "evidence"]),
    ("text-normalization", ["text-normalization", "normaliz"]),
]


def family_for(slug: str, target_area: str | None = None, goal: str | None = None) -> str:
    """Single source of truth for a project's family folder name.

    Priority: explicit target_area > theme keyword (slug, then goal) > slug heuristic.
    Used by both `mas init` (auto-allocate new projects) and `mas reorg` (migrate
    existing ones) so allocation is consistent and doesn't drift back to flat.
    """
    if target_area:
        # turn a repo path into a stable folder token: mas/core/engine -> mas-core-engine
        ta = target_area.strip().strip("/").replace("/", "-").replace("\\", "-")
        if ta:
            return ta
    hay = slug.lower()
    # 1) Slug match is authoritative (the slug is the deliberate identifier).
    for family, needles in _FAMILY_THEMES:
        for n in needles:
            if n in hay:
                return family
    # 2) Goal/brief text is only a weak tiebreaker — match ONLY distinctive compound
    #    needles (containing a hyphen). Generic single words like "drift"/"loop"/"memory"
    #    appear in unrelated prose and cause false positives, so they're slug-only.
    goal_hay = (goal or "").lower()
    if goal_hay:
        for family, needles in _FAMILY_THEMES:
            for n in needles:
                if "-" in n and n in goal_hay:
                    return family
    return _family_key(slug)


def _parse(project_id: str):
    m = _PROJ_RE.match(project_id)
    if m:
        return {"date": m.group(1), "seq": m.group(2), "slug": m.group(3)}
    return {"date": "", "seq": "", "slug": project_id}


def rollup(db_path: Path = DB_PATH) -> list[dict]:
    """Per-project summary across the whole event store. Newest projects first."""
    with _get_connection(db_path) as conn:
        # Event counts + lifecycle markers per project from agent_events.
        ev_rows = conn.execute(
            """
            SELECT project_id,
                   COUNT(*) AS events,
                   SUM(CASE WHEN action_type='handoff_created'   THEN 1 ELSE 0 END) AS handoffs,
                   SUM(CASE WHEN action_type='decision_recorded' THEN 1 ELSE 0 END) AS decisions,
                   SUM(CASE WHEN action_type='project_initialized' THEN 1 ELSE 0 END) AS inits,
                   SUM(CASE WHEN action_type='project_closed'      THEN 1 ELSE 0 END) AS closes,
                   MAX(CASE WHEN action_type='project_initialized'
                            THEN json_extract(payload, '$.params.inputs.target_area') END) AS target_area,
                   MIN(timestamp) AS first_ts,
                   MAX(timestamp) AS last_ts
            FROM agent_events
            GROUP BY project_id
            """
        ).fetchall()

    summaries: dict[str, dict] = {}
    for r in ev_rows:
        pid = r["project_id"]
        meta = _parse(pid)
        summaries[pid] = {
            "project_id": pid,
            "date": meta["date"],
            "slug": meta["slug"],
            "family": _family_key(meta["slug"]),
            "target_area": r["target_area"],
            "events": r["events"],
            "handoffs": r["handoffs"] or 0,
            "decisions": r["decisions"] or 0,
            "initialized": (r["inits"] or 0) > 0,
            "closed": (r["closes"] or 0) > 0,
            "first_ts": r["first_ts"],
            "last_ts": r["last_ts"],
        }
    return sorted(summaries.values(), key=lambda s: (s["date"], s["project_id"]), reverse=True)


def find_predecessor(new_project_id: str, projects_dir: Path | None = None) -> dict | None:
    """Find the most recent prior project in the same family as new_project_id.

    Used at `mas init` to surface a predecessor's PROJECT_SUMMARY.md so a new project
    builds on prior work instead of cold re-deriving context (P4 — lineage → reuse).
    Returns {project_id, summary_path, closed} for the latest sibling that has a
    PROJECT_SUMMARY.md, or None. Pure filesystem (no DB) so it works pre-event.
    """
    from pathlib import Path as _Path
    from core.utils.config import iter_project_dirs

    base = projects_dir or (_Path(__file__).resolve().parents[2] / "projects")
    meta = _parse(new_project_id)
    fam = _family_key(meta["slug"])
    if not fam:
        return None

    candidates = []
    for d in iter_project_dirs(projects_root=base):  # walks flat + family-nested
        pid = d.name
        if pid == new_project_id:
            continue
        pm = _parse(pid)
        if _family_key(pm["slug"]) != fam:
            continue
        summary = os.path.join(str(d), "PROJECT_SUMMARY.md")
        if os.path.exists(summary):
            closed = os.path.exists(os.path.join(str(d), "CLOSED.md"))
            candidates.append((pm["date"], pid, summary, closed))

    if not candidates:
        return None
    # latest by date, then id
    candidates.sort(key=lambda c: (c[0], c[1]))
    date, pid, summary, closed = candidates[-1]
    return {"project_id": pid,
            "summary_path": os.path.relpath(summary, base.parent.parent),
            "closed": closed, "family": fam}


def lineage(db_path: Path = DB_PATH) -> list[dict]:
    """Group projects into families (related efforts). Largest families first."""
    rows = rollup(db_path)
    families: dict[str, list[dict]] = defaultdict(list)
    for s in rows:
        families[s["family"]].append(s)

    out = []
    for fam, members in families.items():
        members_sorted = sorted(members, key=lambda s: (s["date"], s["project_id"]))
        out.append({
            "family": fam,
            "count": len(members_sorted),
            "closed": sum(1 for m in members_sorted if m["closed"]),
            "total_events": sum(m["events"] for m in members_sorted),
            "chain": [m["project_id"] for m in members_sorted],
        })
    return sorted(out, key=lambda f: (f["count"], f["total_events"]), reverse=True)


def _project_goal_hint(project_dir: Path) -> tuple[str | None, str | None]:
    """Read (target_area, goal-or-brief) from a project's shared_state for theme guess."""
    import yaml as _yaml
    for fn in ("shared_state.yaml", "final_shared_state.yaml"):
        p = project_dir / fn
        if p.exists():
            try:
                j = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                pd = j.get("project_definition", {}) or {}
                return pd.get("target_area"), (pd.get("project_goal") or pd.get("original_brief"))
            except Exception:
                pass
    return None, None


def family_of_project(project_dir: Path) -> str:
    """Resolve the family folder for an on-disk project, using shared_state signal."""
    slug = _parse(project_dir.name)["slug"]
    ta, goal = _project_goal_hint(project_dir)
    return family_for(slug, target_area=ta, goal=goal)


def plan_reorg(projects_root: Path) -> list[dict]:
    """Plan a flat -> family-nested reorganization of project folders.

    Family is resolved via `family_for` (target_area > theme keyword > slug heuristic),
    reading each project's shared_state. Flat projects move into projects/<family>/ when
    that family has >= 2 members OR the family folder already exists. Singletons in a
    unique family stay flat. Already-nested projects are left alone.
    """
    from core.utils.config import iter_project_dirs
    root = Path(projects_root)
    existing_family_dirs = {d.name for d in root.iterdir()
                            if d.is_dir() and not d.name.startswith(_PROJ)} if root.is_dir() else set()

    # family of every project (flat + already-nested), via shared_state-aware resolver
    all_dirs = list(iter_project_dirs(projects_root=root))
    fam_count: dict[str, int] = defaultdict(int)
    fam_of: dict[Path, str] = {}
    for d in all_dirs:
        fam = family_of_project(d)
        fam_count[fam] += 1
        fam_of[d] = fam

    moves = []
    for d in all_dirs:
        fam = fam_of[d]
        if not fam:
            continue
        dst = root / fam / d.name
        if dst == d:
            continue  # already in the right place
        # Move if the target family clusters (>=2) or its folder already exists.
        # This also RE-NESTS projects sitting in a now-mismatched family folder
        # (e.g. consolidating mas-self/ → mas-engine/).
        if fam_count[fam] < 2 and fam not in existing_family_dirs:
            continue
        moves.append({"project_id": d.name, "family": fam,
                      "src": str(d), "dst": str(dst)})
    return sorted(moves, key=lambda m: (m["family"], m["project_id"]))


_PROJ = "proj-"


def reorg_projects(projects_root: Path, dry_run: bool = True) -> dict:
    """Move project folders into their family subfolders. Idempotent; safe (the path
    resolver handles both layouts, so code keeps working mid-migration). Also removes
    family folders left empty after a consolidation move."""
    import shutil
    root = Path(projects_root)
    moves = plan_reorg(root)
    applied = []
    for m in moves:
        if not dry_run:
            dst = Path(m["dst"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(m["src"], str(dst))
        applied.append(m)

    pruned_dirs = []
    if not dry_run and root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and not d.name.startswith(_PROJ) and not any(d.iterdir()):
                d.rmdir()
                pruned_dirs.append(d.name)

    families = sorted({m["family"] for m in applied})
    return {"dry_run": dry_run, "moves": len(applied), "families": families,
            "detail": applied, "pruned_empty_dirs": pruned_dirs}
