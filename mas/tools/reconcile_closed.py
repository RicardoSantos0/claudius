"""Reconcile lifecycle artifacts (proj-YYYYMMDD-NNN-mas-self-audit, ms-02).

Some projects were closed via a state write (status=closed) but never ran the full
`mas close` flow, so they lack a human-readable CLOSED.md closure report. This backfills
CLOSED.md for any project where status=closed but CLOSED.md is missing. Idempotent.

DRY-RUN by default; pass --apply to write.
"""
import os, glob, sys, yaml
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPLY = "--apply" in sys.argv


def main():
    dirs = sorted(d for d in glob.glob(os.path.join(ROOT, "mas", "projects", "*")) if os.path.isdir(d))
    backfilled = []
    for d in dirs:
        sy = os.path.join(d, "shared_state.yaml")
        if not os.path.exists(sy):
            continue
        try:
            state = yaml.safe_load(open(sy, encoding="utf-8")) or {}
        except Exception:
            continue
        ci = state.get("core_identity", {})
        if ci.get("status") != "closed":
            continue
        closed_md = os.path.join(d, "CLOSED.md")
        if os.path.exists(closed_md):
            continue
        backfilled.append(os.path.basename(d))
        if APPLY:
            content = "\n".join([
                "# Project Closed",
                "",
                f"- project_id: {os.path.basename(d)}",
                f"- closed_at: {ci.get('updated_at', datetime.now(timezone.utc).isoformat())}",
                f"- final_phase: {ci.get('current_phase', 'closed')}",
                f"- status: closed",
                "- note: CLOSED.md backfilled by ms-02 lifecycle reconciliation "
                "(project was closed via state write before the full `mas close` flow existed).",
                "",
            ])
            with open(closed_md, "w", encoding="utf-8") as f:
                f.write(content)

    print(f"APPLY={APPLY}")
    print(f"projects status=closed but missing CLOSED.md: {len(backfilled)}")
    for b in backfilled:
        print(f"  {'backfilled' if APPLY else 'would backfill'} {b}")
    if not APPLY:
        print("\nDRY-RUN only. Re-run with --apply.")


if __name__ == "__main__":
    main()
