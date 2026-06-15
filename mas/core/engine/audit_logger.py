"""
Audit Logger
Appends structured events to audit.log.
All significant system events are recorded here.
"""

import yaml
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DEFAULT_LOG = ROOT / "audit.log"


class AuditLogger:
    def __init__(self, log_path: Path = DEFAULT_LOG):
        self.log_path = log_path

    _MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB — rotate beyond this

    def _maybe_rotate(self) -> None:
        """Rename audit.log → audit.log.bak if it exceeds _MAX_LOG_BYTES."""
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > self._MAX_LOG_BYTES:
                bak = self.log_path.with_suffix(".log.bak")
                self.log_path.rename(bak)
        except Exception:
            pass  # best-effort log rotation; must never break audit logging

    def log(self, event: str, **kwargs) -> None:
        """Append a structured audit event."""
        self._maybe_rotate()
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **kwargs,
        }
        line = yaml.dump(
            [entry],
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip()
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_write(self, agent_id: str, field_path: str, project_id: str,
                  success: bool, reason: str = "") -> None:
        self.log(
            "state_write",
            agent_id=agent_id,
            field_path=field_path,
            project_id=project_id,
            success=success,
            reason=reason or None,
        )

    def log_violation(self, agent_id: str, field_path: str,
                      project_id: str, reason: str) -> None:
        self.log(
            "governance_violation",
            agent_id=agent_id,
            field_path=field_path,
            project_id=project_id,
            reason=reason,
        )

    def log_handoff(self, event: str, handoff_id: str, project_id: str,
                    from_agent: str, to_agent: str, **kwargs) -> None:
        self.log(
            event,
            handoff_id=handoff_id,
            project_id=project_id,
            from_agent=from_agent,
            to_agent=to_agent,
            **kwargs,
        )

    def log_phase_transition(self, project_id: str, from_phase: str,
                             to_phase: str) -> None:
        self.log(
            "phase_transition",
            project_id=project_id,
            from_phase=from_phase,
            to_phase=to_phase,
        )

    def log_error(self, project_id: str, error_type: str,
                  description: str, **kwargs) -> None:
        self.log(
            "error",
            project_id=project_id,
            error_type=error_type,
            description=description,
            **kwargs,
        )

    def log_human_escalation(self, project_id: str, reason: str) -> None:
        self.log(
            "human_escalation",
            project_id=project_id,
            reason=reason,
        )


# Module-level default logger
_default_logger: AuditLogger | None = None


def get_logger() -> AuditLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = AuditLogger()
    return _default_logger
