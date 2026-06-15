"""Clutter purge for episodic.db — classify project_ids as KEEP vs PURGE.

DRY-RUN by default (counts only). Pass --apply to delete.
Purges test/scratch projects from agent_events, shared_states, agent_graph,
agent_graph_edges; strips junk graph nodes. Real dated work-projects are KEPT.

Usage:
    python mas/tools/purge_test_noise.py            # dry-run report
    python mas/tools/purge_test_noise.py --apply    # perform deletion
"""
import sqlite3, os, re, sys, glob

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "mas", "data", "episodic.db")
APPLY = "--apply" in sys.argv

# A project_id is KEPT iff it has a real on-disk folder OR matches the dated work pattern.
folders = set(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "mas", "projects", "*")) if os.path.isdir(d))
DATED = re.compile(r"^proj-2026\d{4}-\d{3}-")

# Explicit test/scratch prefixes/ids that should NEVER be kept even if a folder exists.
TEST_PATTERNS = [
    re.compile(r"^proj-test"), re.compile(r"^proj-gov-\d"), re.compile(r"^proj-gate-"),
    re.compile(r"^proj-handoff"), re.compile(r"^proj-wire"), re.compile(r"^proj-loop"),
    re.compile(r"^proj-trainer-(test|analysis_engineer|canonical_engineer|integration_engineer|reliability_engineer)"),
    re.compile(r"^proj-hook-test"), re.compile(r"^proj-resume-cli"), re.compile(r"^proj-gm-integ"),
    re.compile(r"^proj-test-improvements"), re.compile(r"^proj-test-sprint"),
]
EXPLICIT_PURGE = {"__system__", "unknown", "proj-001", "proj-002", "proj-test", "proj-gov-001", "proj-gov-003"}


def classify(pid: str) -> str:
    if pid in EXPLICIT_PURGE:
        return "PURGE"
    for pat in TEST_PATTERNS:
        if pat.match(pid):
            return "PURGE"
    if DATED.match(pid):
        return "KEEP"
    if pid in folders:
        return "KEEP"
    return "PURGE"  # un-dated, no folder, not recognized → scratch


def main():
    con = sqlite3.connect(DB); cur = con.cursor()
    # gather all project_ids seen across the data tables
    pids = set()
    for tbl, col in [("agent_events", "project_id"), ("shared_states", "project_id")]:
        try:
            pids |= set(r[0] for r in cur.execute(f"SELECT DISTINCT {col} FROM {tbl}"))
        except Exception:
            pass
    keep = sorted(p for p in pids if classify(p) == "KEEP")
    purge = sorted(p for p in pids if classify(p) == "PURGE")

    def count(tbl, col, ids):
        if not ids:
            return 0
        q = f"SELECT COUNT(*) FROM {tbl} WHERE {col} IN (%s)" % ",".join("?" * len(ids))
        try:
            return cur.execute(q, ids).fetchone()[0]
        except Exception as e:
            return f"ERR:{e}"

    print(f"DB: {DB}")
    print(f"APPLY: {APPLY}")
    print(f"total distinct project_ids: {len(pids)}  KEEP={len(keep)}  PURGE={len(purge)}")
    print("\n-- rows that WOULD be deleted --")
    ev = count("agent_events", "project_id", purge)
    ss = count("shared_states", "project_id", purge)
    print(f"  agent_events:  {ev}")
    print(f"  shared_states: {ss}")

    # junk graph nodes (by label/type) + edges referencing purged-ish nodes
    junk_nodes = cur.execute(
        "SELECT COUNT(*) FROM agent_graph WHERE type IN ('related_to','unknown_type') "
        "OR label LIKE '%random event%' OR id LIKE 'ep-unknown%'"
    ).fetchone()[0]
    print(f"  agent_graph junk nodes (related_to/unknown_type/'random event'): {junk_nodes}")

    print("\n-- KEEP (real work) sample --")
    for p in keep[:12]:
        print(f"   KEEP  {p}")
    print(f"   ... ({len(keep)} kept)")
    print("\n-- PURGE (test/scratch) --")
    for p in purge:
        print(f"   PURGE {p}")

    if not APPLY:
        print("\nDRY-RUN only. Re-run with --apply to delete.")
        con.close()
        return

    # ---- APPLY ----
    print("\nAPPLYING DELETION...")
    if purge:
        ph = ",".join("?" * len(purge))
        cur.execute(f"DELETE FROM agent_events WHERE project_id IN ({ph})", purge)
        print(f"  deleted agent_events: {cur.rowcount}")
        cur.execute(f"DELETE FROM shared_states WHERE project_id IN ({ph})", purge)
        print(f"  deleted shared_states: {cur.rowcount}")
    cur.execute(
        "DELETE FROM agent_graph WHERE type IN ('related_to','unknown_type') "
        "OR label LIKE '%random event%' OR id LIKE 'ep-unknown%'"
    )
    print(f"  deleted agent_graph junk nodes: {cur.rowcount}")
    # orphan edges: any edge whose endpoint no longer exists
    cur.execute(
        "DELETE FROM agent_graph_edges WHERE source NOT IN (SELECT id FROM agent_graph) "
        "OR target NOT IN (SELECT id FROM agent_graph)"
    ) if _has_cols(cur, "agent_graph_edges", ("source", "target")) else None
    con.commit()
    print("COMMIT done.")
    con.close()


def _has_cols(cur, tbl, cols):
    have = set(c[1] for c in cur.execute(f"PRAGMA table_info({tbl})"))
    return all(c in have for c in cols)


if __name__ == "__main__":
    main()
