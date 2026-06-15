"""
Capability Registry
Manages the roster of agents, skills, and tools.
Used by the HR Agent to search capabilities, score matches, and produce
Capability Gap Certificates.
"""

import sys
import json
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).parent.parent.parent
REGISTRY_PATH = ROOT / "roster" / "registry_index.yaml"
VERSION_HISTORY_PATH = ROOT / "roster" / "version_history.yaml"

# Match thresholds
STRONG_MATCH_THRESHOLD = 80.0   # >= 80: recommend reuse
PARTIAL_MATCH_THRESHOLD = 50.0  # 50–79: recommend with parameterization note
PARAMETERIZATION_THRESHOLD = 70.0

# Bias against overusing broad generalists for specialized capability asks.
GENERALIST_AGENT_IDS = {
    "master_orchestrator",
    "domain_expert",
    "project_manager_agent",
    "product_manager_agent",
    "inquirer_agent",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    agent_id: str
    name: str
    trust_tier: str
    status: str
    capabilities: list
    matching_tags: list
    required_tags: list
    score: float                  # 0–100
    match_type: str               # "strong" | "partial" | "none"
    on_probation: bool = False
    performance_score: Optional[float] = None

    @property
    def recommendation(self) -> str:
        if self.match_type == "strong":
            if self.on_probation:
                return (
                    f"reuse_with_warning — Agent is on probation. "
                    f"Performance score: {self.performance_score}. "
                    f"Master must accept the risk explicitly."
                )
            return "reuse"
        if self.match_type == "partial":
            missing = [t for t in self.required_tags if t not in self.matching_tags]
            note = f"Missing capabilities: {', '.join(missing)}. "
            note += "Consider parameterization or supplemental agent."
            if self.on_probation:
                note += (
                    f" WARNING: Agent is on probation. "
                    f"Performance score: {self.performance_score}."
                )
            return f"parameterize — {note}"
        return "gap_certify"


@dataclass
class GapCertificate:
    certificate_id: str
    requested_by: str
    project_id: str
    timestamp: str
    need_description: str
    required_capabilities: list
    exact_matches_found: int
    partial_matches_found: int
    partial_match_details: list   # [{agent_id, score, gap_description}]
    nearest_agent_id: Optional[str]
    nearest_score: float
    gap_description: str
    could_be_parameterized: bool
    parameterization_rejected_because: str
    spawn_recommendation: dict    # {should_spawn, is_bounded, is_recurring, ...}
    approved_by_hr: bool = False
    hr_approved_at: Optional[str] = None
    forwarded_to_master: bool = False
    master_decision: Optional[str] = None
    master_decided_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "capability_gap_certificate": {
                "certificate_id": self.certificate_id,
                "requested_by": self.requested_by,
                "project_id": self.project_id,
                "timestamp": self.timestamp,
                "need_description": self.need_description,
                "required_capabilities": self.required_capabilities,
                "search_performed": {
                    "exact_matches_found": self.exact_matches_found,
                    "partial_matches_found": self.partial_matches_found,
                    "partial_match_details": self.partial_match_details,
                    "search_scope": "full_roster",
                },
                "why_existing_fails": {
                    "nearest_capability": self.nearest_agent_id or "",
                    "gap_description": self.gap_description,
                    "could_be_parameterized": self.could_be_parameterized,
                    "parameterization_rejected_because": self.parameterization_rejected_because,
                },
                "spawn_recommendation": self.spawn_recommendation,
                "approved_by_hr": self.approved_by_hr,
                "hr_approved_at": self.hr_approved_at,
                "forwarded_to_master": self.forwarded_to_master,
                "master_decision": self.master_decision,
                "master_decided_at": self.master_decided_at,
            }
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class CapabilityRegistry:
    """
    Manages the roster of agents/skills/tools and provides capability search.
    Reads from and writes to roster/registry_index.yaml (append-safe, no deletes).
    """

    def __init__(self,
                 registry_path: Path = REGISTRY_PATH,
                 version_history_path: Path = VERSION_HISTORY_PATH):
        self.registry_path = registry_path
        self.version_history_path = version_history_path
        self._use_default_registry = (registry_path == REGISTRY_PATH)

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------

    def load_registry(self) -> dict:
        """Load registry from disk."""
        if not self.registry_path.exists():
            raise FileNotFoundError(
                f"Registry not found: {self.registry_path}"
            )
        with open(self.registry_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_registry(self, data: dict) -> None:
        """Write registry to disk atomically (overwrite)."""
        with open(self.registry_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    def _load_version_history(self) -> dict:
        if not self.version_history_path.exists():
            return {"version_history": {"maintained_by": "hr_agent", "entries": []}}
        with open(self.version_history_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_version_history(self, data: dict) -> None:
        with open(self.version_history_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_match(self, required: list[str], agent_capabilities: list[str]) -> float:
        """
        Compute overlap score as percentage.
        score = len(matching_tags) / len(required_tags) * 100
        Returns 0.0 if required is empty.
        """
        if not required:
            return 0.0
        req_lower = {t.lower() for t in required}
        cap_lower = {t.lower() for t in agent_capabilities}
        matching = req_lower & cap_lower
        return len(matching) / len(req_lower) * 100.0

    def _classify_match(self, score: float) -> str:
        if score >= STRONG_MATCH_THRESHOLD:
            return "strong"
        if score >= PARTIAL_MATCH_THRESHOLD:
            return "partial"
        return "none"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, required_capabilities: list[str]) -> list[MatchResult]:
        """
        Search the registry for agents matching required_capabilities.
        Returns all results sorted by score desc (includes "none" matches
        so the caller can see what the best available option is).
        DB is the primary source; falls back to YAML registry if DB returns nothing.
        """
        db_agents = self._db_agents()
        if db_agents:
            agents = db_agents
        else:
            data = self.load_registry()
            agents = data.get("registry", {}).get("agents", [])

        results = []
        for agent in agents:
            if agent.get("status") == "retired":
                continue

            caps = agent.get("capabilities", [])
            score = self.score_match(required_capabilities, caps)
            if self._is_generalist(agent.get("agent_id", ""), caps):
                score = max(0.0, score - self._generalist_penalty(required_capabilities))

            req_lower = [t.lower() for t in required_capabilities]
            cap_lower = [t.lower() for t in caps]
            matching = [t for t in req_lower if t in cap_lower]

            perf = agent.get("performance_score")
            on_probation = agent.get("status") == "probation"

            results.append(MatchResult(
                agent_id=agent["agent_id"],
                name=agent.get("name", agent["agent_id"]),
                trust_tier=agent.get("trust_tier", ""),
                status=agent.get("status", "active"),
                capabilities=caps,
                matching_tags=matching,
                required_tags=req_lower,
                score=score,
                match_type=self._classify_match(score),
                on_probation=on_probation,
                performance_score=perf,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _is_generalist(self, agent_id: str, capabilities: list[str]) -> bool:
        """Heuristic: known broad roles or unusually broad capability surfaces."""
        return agent_id in GENERALIST_AGENT_IDS or len(capabilities) >= 10

    def _generalist_penalty(self, required_capabilities: list[str]) -> float:
        """
        Increase penalty as the request becomes more specialized.
        Encourages HR to raise gap certificates earlier when specialist fit is weak.
        """
        req_count = len(required_capabilities)
        if req_count >= 4:
            return 20.0
        if req_count >= 2:
            return 12.0
        return 8.0

    def get_strong_matches(self, required_capabilities: list[str]) -> list[MatchResult]:
        return [r for r in self.search(required_capabilities)
                if r.match_type == "strong"]

    def get_partial_matches(self, required_capabilities: list[str]) -> list[MatchResult]:
        return [r for r in self.search(required_capabilities)
                if r.match_type == "partial"]

    # ------------------------------------------------------------------
    # Roster mutation
    # ------------------------------------------------------------------

    def register_agent(self, entry: dict, authorized_by: str = "master_orchestrator") -> bool:
        """
        Add or update an agent entry in the registry.
        Required fields: agent_id, name, version, trust_tier, status, capabilities.
        Returns True on success.
        """
        required = ["agent_id", "name", "version", "trust_tier", "status", "capabilities"]
        missing = [f for f in required if f not in entry]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        data = self.load_registry()
        reg = data.setdefault("registry", {})
        agents = reg.setdefault("agents", [])

        agent_id = entry["agent_id"]
        existing_idx = next(
            (i for i, a in enumerate(agents) if a["agent_id"] == agent_id), None
        )

        now = datetime.now(timezone.utc).isoformat()
        entry.setdefault("created_at", now)

        if existing_idx is not None:
            old = agents[existing_idx]
            agents[existing_idx] = {**old, **entry}
            change_type = "update"
        else:
            agents.append(entry)
            change_type = "add"

        # Refresh counts
        reg["last_updated"] = now
        self._refresh_counts(data)
        self._save_registry(data)

        # Append version history
        self._append_version_history(
            change_type=change_type,
            target_id=agent_id,
            target_type="agent",
            old_values=old if existing_idx is not None else {},
            new_values=entry,
            authorized_by=authorized_by,
        )
        return True

    def retire_agent(self, agent_id: str, reason: str,
                     authorized_by: str = "master_orchestrator") -> bool:
        """
        Mark an agent as retired. Never deletes entries.
        Returns True if the agent was found, False if not found.
        """
        data = self.load_registry()
        agents = data.get("registry", {}).get("agents", [])

        for agent in agents:
            if agent["agent_id"] == agent_id:
                old = dict(agent)
                now = datetime.now(timezone.utc).isoformat()
                agent["status"] = "retired"
                agent["retired_at"] = now
                agent["retirement_reason"] = reason

                data["registry"]["last_updated"] = now
                self._refresh_counts(data)
                self._save_registry(data)

                self._append_version_history(
                    change_type="retire",
                    target_id=agent_id,
                    target_type="agent",
                    old_values=old,
                    new_values={"status": "retired", "retired_at": now,
                                "retirement_reason": reason},
                    reason=reason,
                    authorized_by=authorized_by,
                )
                return True
        return False

    def flag_probation(self, agent_id: str, reason: str,
                       authorized_by: str = "master_orchestrator") -> bool:
        """Set an agent's status to 'probation'."""
        data = self.load_registry()
        agents = data.get("registry", {}).get("agents", [])

        for agent in agents:
            if agent["agent_id"] == agent_id and agent.get("status") != "retired":
                old = dict(agent)
                now = datetime.now(timezone.utc).isoformat()
                agent["status"] = "probation"
                data["registry"]["last_updated"] = now
                self._save_registry(data)

                self._append_version_history(
                    change_type="demote",
                    target_id=agent_id,
                    target_type="agent",
                    old_values=old,
                    new_values={"status": "probation"},
                    reason=reason,
                    authorized_by=authorized_by,
                )
                return True
        return False

    def update_performance_score(self, agent_id: str, score: float,
                                 authorized_by: str = "evaluator_agent") -> bool:
        """Update running performance score for an agent."""
        if not (0.0 <= score <= 100.0):
            raise ValueError(f"Performance score must be 0–100, got {score}")

        data = self.load_registry()
        agents = data.get("registry", {}).get("agents", [])

        for agent in agents:
            if agent["agent_id"] == agent_id:
                old_score = agent.get("performance_score")
                agent["performance_score"] = round(score, 2)
                data["registry"]["last_updated"] = datetime.now(timezone.utc).isoformat()
                self._save_registry(data)

                self._append_version_history(
                    change_type="update",
                    target_id=agent_id,
                    target_type="agent",
                    old_values={"performance_score": old_score},
                    new_values={"performance_score": score},
                    reason="performance_update",
                    authorized_by=authorized_by,
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Gap Certificate
    # ------------------------------------------------------------------

    def produce_gap_certificate(
        self,
        need_description: str,
        required_capabilities: list[str],
        project_id: str,
        requested_by: str,
    ) -> GapCertificate:
        """
        Produce a Capability Gap Certificate after a full search finds no strong match.
        Automatically runs the search internally.
        """
        results = self.search(required_capabilities)

        strong = [r for r in results if r.match_type == "strong"]
        partial = [r for r in results if r.match_type == "partial"]

        nearest = results[0] if results else None
        nearest_score = nearest.score if nearest else 0.0
        nearest_id = nearest.agent_id if nearest else None

        partial_details = [
            {
                "agent_id": r.agent_id,
                "coverage_pct": round(r.score, 1),
                "gap_description": (
                    f"Missing: {', '.join(t for t in r.required_tags if t not in r.matching_tags)}"
                ),
            }
            for r in partial
        ]

        could_parameterize = bool(partial) and partial[0].score >= PARAMETERIZATION_THRESHOLD
        param_rejected = (
            "" if could_parameterize
            else f"No partial match reaches parameterization threshold ({PARAMETERIZATION_THRESHOLD:.0f}%)"
        )

        data = self.load_registry()
        vh = self._load_version_history()
        seq = len(vh.get("version_history", {}).get("entries", [])) + 1
        cert_id = f"gap-{project_id}-{seq:03d}"

        now = datetime.now(timezone.utc).isoformat()

        return GapCertificate(
            certificate_id=cert_id,
            requested_by=requested_by,
            project_id=project_id,
            timestamp=now,
            need_description=need_description,
            required_capabilities=required_capabilities,
            exact_matches_found=len(strong),
            partial_matches_found=len(partial),
            partial_match_details=partial_details,
            nearest_agent_id=nearest_id,
            nearest_score=nearest_score,
            gap_description=(
                f"Best available agent '{nearest_id}' scores "
                f"{nearest_score:.1f}% — below strong match threshold "
                f"({STRONG_MATCH_THRESHOLD}%)"
            ) if nearest_id else "No agents in roster.",
            could_be_parameterized=could_parameterize,
            parameterization_rejected_because=param_rejected,
            spawn_recommendation={
                "should_spawn": True,
                "is_bounded": True,
                "is_recurring": False,
                "is_verifiable": True,
                "risk_classification": "low",
                "rationale": (
                    f"No existing agent covers required capabilities: "
                    f"{', '.join(required_capabilities)}. "
                    f"Spawning a bounded agent is the governed path forward."
                ),
            },
        )

    def save_gap_certificate(self, cert: GapCertificate,
                             project_id: str,
                             projects_root: Optional[Path] = None) -> Path:
        """Write the gap certificate to disk under the project folder."""
        from core.config import get_projects_dir
        from core.utils.config import resolve_project_dir
        base = projects_root or get_projects_dir()
        hr_dir = resolve_project_dir(project_id, projects_root=base) / "hr"
        hr_dir.mkdir(parents=True, exist_ok=True)

        cert_path = hr_dir / f"{cert.certificate_id}.yaml"
        with open(cert_path, "w", encoding="utf-8") as f:
            yaml.dump(cert.to_dict(), f, default_flow_style=False,
                      allow_unicode=True, sort_keys=False)
        return cert_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_counts(self, data: dict) -> None:
        """Recompute counts from current agent list."""
        agents = data.get("registry", {}).get("agents", [])
        data["counts"] = {
            "active_agents": sum(1 for a in agents if a.get("status") == "active"),
            # Skills live in the top-level `skills:` list, not registry.skills.
            "active_skills": sum(
                1 for s in data.get("skills", []) if s.get("status") == "active"
            ),
            "retired_agents": sum(1 for a in agents if a.get("status") == "retired"),
            "spawned_total": sum(
                1 for a in agents if a.get("spawn_origin") is not None
            ),
        }

    def _append_version_history(
        self,
        change_type: str,
        target_id: str,
        target_type: str,
        old_values: dict,
        new_values: dict,
        reason: str = "",
        authorized_by: str = "master_orchestrator",
    ) -> None:
        """Append a change record to version_history.yaml (append-only)."""
        vh = self._load_version_history()
        entries = vh.setdefault("version_history", {}).setdefault("entries", [])

        seq = len(entries) + 1
        entries.append({
            "change_id": f"vh-{seq:04d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changed_by": "hr_agent",
            "change_type": change_type,
            "target_id": target_id,
            "target_type": target_type,
            "old_values": old_values,
            "new_values": new_values,
            "reason": reason,
            "authorized_by": authorized_by,
        })
        self._save_version_history(vh)

    def get_agent(self, agent_id: str) -> Optional[dict]:
        """Return the registry entry for a given agent, or None."""
        data = self.load_registry()
        for a in data.get("registry", {}).get("agents", []):
            if a["agent_id"] == agent_id:
                return a
        return None

    # ------------------------------------------------------------------
    # DB-first capability loading
    # ------------------------------------------------------------------

    def _db_agents(self) -> list[dict]:
        """
        Query mas_agents for active agents. Returns [] on any error or when
        a custom registry path is in use (e.g. during tests).
        tools column is a JSON array stored as text.
        """
        if not self._use_default_registry:
            return []
        try:
            from core.db import _get_connection, DB_PATH
            with _get_connection(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT agent_id, name, tier, description, tools, status, last_score"
                    " FROM mas_agents WHERE status = 'active'"
                ).fetchall()
                agents = []
                for row in rows:
                    try:
                        tools = json.loads(row["tools"] or "[]")
                    except Exception:
                        tools = []
                    entry = {
                        "agent_id": row["agent_id"],
                        "name": row["name"],
                        "trust_tier": row["tier"] or "",
                        "description": row["description"] or "",
                        "capabilities": tools,
                        "status": row["status"],
                    }
                    if row["last_score"] is not None:
                        entry["last_score"] = row["last_score"]
                        entry["performance_score"] = row["last_score"]
                    agents.append(entry)
                return agents
        except Exception:
            return []

    def list_agents(self) -> list[dict]:
        """
        Return all active agent capability dicts. DB is the primary source;
        falls back to YAML registry if DB returns nothing.
        """
        db_agents = self._db_agents()
        if db_agents:
            return db_agents
        # YAML fallback
        try:
            data = self.load_registry()
            return [
                a for a in data.get("registry", {}).get("agents", [])
                if a.get("status") != "retired"
            ]
        except Exception:
            return []

    def sync_db_capabilities(self) -> int:
        """
        Overwrite the DB mas_agents.tools column with capability tags from
        the YAML registry. This fixes the mismatch where tools stored Claude
        tool names (Read, Bash) instead of semantic capability tags.
        Returns the number of agents updated.
        """
        if not self._use_default_registry:
            raise RuntimeError("sync_db_capabilities requires the default registry path")

        data = self.load_registry()
        yaml_agents = {
            a["agent_id"]: a.get("capabilities", [])
            for a in data.get("registry", {}).get("agents", [])
            if a.get("status") != "retired"
        }
        if not yaml_agents:
            return 0

        try:
            from core.db import _get_connection, DB_PATH, init_db
            init_db(DB_PATH)
            updated = 0
            with _get_connection(DB_PATH) as conn:
                for agent_id, caps in yaml_agents.items():
                    conn.execute(
                        "UPDATE mas_agents SET tools = ? WHERE agent_id = ?",
                        (json.dumps(caps), agent_id),
                    )
                    updated += conn.execute(
                        "SELECT changes()"
                    ).fetchone()[0]
            return updated
        except Exception as exc:
            raise RuntimeError(f"DB sync failed: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="capability_registry",
        description="Capability Registry CLI — search, register, retire, gap-certify",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # search
    s = sub.add_parser("search", help="Search for agents by capability tags")
    s.add_argument("--tags", required=True,
                   help="Comma-separated list of capability tags")
    s.add_argument("--min-score", type=float, default=0.0,
                   help="Only show results >= this score (default: 0)")

    # register
    r = sub.add_parser("register", help="Register a new agent entry")
    r.add_argument("--entry-json", required=True,
                   help="JSON string of the agent entry")
    r.add_argument("--authorized-by", default="master_orchestrator")

    # retire
    rt = sub.add_parser("retire", help="Retire an agent")
    rt.add_argument("--agent-id", required=True)
    rt.add_argument("--reason", required=True)
    rt.add_argument("--authorized-by", default="master_orchestrator")

    # gap-cert
    gc = sub.add_parser("gap-cert", help="Produce a Capability Gap Certificate")
    gc.add_argument("--project-id", required=True)
    gc.add_argument("--requested-by", required=True)
    gc.add_argument("--need", required=True,
                    help="Plain-text description of the needed capability")
    gc.add_argument("--tags", required=True,
                    help="Comma-separated capability tags for the need")
    gc.add_argument("--save", action="store_true",
                    help="Write the certificate to disk")

    # show
    sh = sub.add_parser("show", help="Show a specific agent's registry entry")
    sh.add_argument("--agent-id", required=True)

    # sync-db-from-yaml
    sub.add_parser(
        "sync-db-from-yaml",
        help="Overwrite DB mas_agents.tools column with capability tags from YAML registry",
    )

    return p


def main_cli(args=None) -> int:
    p = _build_parser()
    ns = p.parse_args(args)
    registry = CapabilityRegistry()

    if ns.command == "search":
        tags = [t.strip() for t in ns.tags.split(",") if t.strip()]
        results = registry.search(tags)
        filtered = [r for r in results if r.score >= ns.min_score]

        if not filtered:
            print("[none] No agents found matching the given tags.")
            return 0

        for r in filtered:
            print(
                f"  [{r.match_type:8}] {r.agent_id:30} "
                f"score={r.score:.1f}%  "
                f"matching={r.matching_tags}"
            )
            print(f"    recommendation: {r.recommendation}")
        return 0

    if ns.command == "register":
        entry = json.loads(ns.entry_json)
        registry.register_agent(entry, authorized_by=ns.authorized_by)
        print(f"[ok] Registered: {entry['agent_id']}")
        return 0

    if ns.command == "retire":
        found = registry.retire_agent(ns.agent_id, ns.reason,
                                      authorized_by=ns.authorized_by)
        if found:
            print(f"[ok] Retired: {ns.agent_id}")
        else:
            print(f"[error] Agent not found: {ns.agent_id}", file=sys.stderr)
            return 1
        return 0

    if ns.command == "gap-cert":
        tags = [t.strip() for t in ns.tags.split(",") if t.strip()]
        cert = registry.produce_gap_certificate(
            need_description=ns.need,
            required_capabilities=tags,
            project_id=ns.project_id,
            requested_by=ns.requested_by,
        )
        print(yaml.dump(cert.to_dict(), default_flow_style=False,
                        allow_unicode=True, sort_keys=False))
        if ns.save:
            path = registry.save_gap_certificate(cert, ns.project_id)
            print(f"[ok] Certificate saved: {path}")
        return 0

    if ns.command == "show":
        agent = registry.get_agent(ns.agent_id)
        if agent is None:
            print(f"[error] Agent not found: {ns.agent_id}", file=sys.stderr)
            return 1
        print(yaml.dump(agent, default_flow_style=False, allow_unicode=True))
        return 0

    if ns.command == "sync-db-from-yaml":
        try:
            updated = registry.sync_db_capabilities()
            print(f"[ok] Synced capability tags for {updated} agent(s) from YAML registry to DB")
        except RuntimeError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main_cli())
