"""
MAS Event Recorder

Validates event action_types against the typed taxonomy in
mas/foundation/event_types.yaml and writes to the existing
agent_events table in episodic.db via the standard append_event helper.

Usage:
    from mas.core.engine.event_recorder import EventRecorder, MASEvent

    recorder = EventRecorder()
    event_id = recorder.record_simple(
        project_id="proj-123",
        actor="master_orchestrator",
        action_type="decision_made",
        intent="Selected canonical path for Phase 1",
    )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


def _find_repo_root() -> Path:
    """Walk up from this file until pyproject.toml is found."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(
        "Could not locate repo root (pyproject.toml not found in any parent directory)"
    )


@dataclass
class MASEvent:
    project_id: str
    actor: str
    action_type: str
    intent: str
    payload: dict = field(default_factory=dict)
    phase: Optional[str] = None
    rule_id: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)
    result_shape: Optional[str] = None


class EventRecorder:
    """
    Validates and records MAS typed events to episodic.db.

    Args:
        db_path: Override the default episodic.db path. Primarily used in
                 tests to write to an in-memory or temporary database.
    """

    _EVENT_TYPES_REL = Path("mas") / "foundation" / "event_types.yaml"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._repo_root = _find_repo_root()
        self._valid_types: set[str] = self._load_valid_types()
        self._db_path: Path = (
            Path(db_path) if db_path is not None else self._get_db_path()
        )

    def _load_valid_types(self) -> set[str]:
        """Read event_types.yaml and flatten all action_type values into a set."""
        yaml_path = self._repo_root / self._EVENT_TYPES_REL
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        valid: set[str] = set()
        for category_types in data.get("event_types", {}).values():
            valid.update(category_types)
        return valid

    def _get_db_path(self) -> Path:
        """Resolve the default episodic.db path relative to the repo root."""
        return self._repo_root / "mas" / "data" / "episodic.db"

    def record(self, event: MASEvent) -> str:
        """
        Validate and write a MASEvent to agent_events in episodic.db.

        Returns the action_id (UUID string) assigned by append_event.
        Raises ValueError if action_type is not in the taxonomy.
        """
        if event.action_type not in self._valid_types:
            raise ValueError(
                f"Unknown event type: {event.action_type!r}. "
                f"Valid types: {sorted(self._valid_types)}"
            )

        # Build the normalised payload that gets stored in the JSON blob.
        # Merge structured MASEvent fields into the caller-supplied payload so
        # that the full event context is queryable from the DB.
        normalised_payload: dict = {
            **event.payload,
            "actor": event.actor,
        }
        if event.phase is not None:
            normalised_payload["phase"] = event.phase
        if event.rule_id is not None:
            normalised_payload["rule_id"] = event.rule_id
        if event.artifacts:
            normalised_payload["artifacts"] = event.artifacts

        # Import here to avoid circular imports at module load time.
        # Degrade gracefully if DB write fails — phases must not fail due to event recording.
        try:
            from core.db import append_event
            action_id = append_event(
                project_id=event.project_id,
                agent_id=event.actor,
                action_type=event.action_type,
                intent=event.intent,
                result_shape=event.result_shape or "",
                payload=normalised_payload,
                db_path=self._db_path,
            )
            return action_id
        except Exception as exc:  # noqa: BLE001
            import warnings
            warnings.warn(
                f"EventRecorder: DB write failed for {event.action_type!r} "
                f"(project={event.project_id}): {exc}. Event not persisted.",
                RuntimeWarning,
                stacklevel=2,
            )
            return ""

    def record_simple(
        self,
        project_id: str,
        actor: str,
        action_type: str,
        intent: str,
        **kwargs,
    ) -> str:
        """
        Convenience wrapper — builds a MASEvent from keyword arguments and
        calls record().

        Accepted kwargs mirror MASEvent optional fields:
            payload (dict), phase (str), rule_id (str),
            artifacts (list[str]), result_shape (str)
        """
        event = MASEvent(
            project_id=project_id,
            actor=actor,
            action_type=action_type,
            intent=intent,
            payload=kwargs.pop("payload", {}),
            phase=kwargs.pop("phase", None),
            rule_id=kwargs.pop("rule_id", None),
            artifacts=kwargs.pop("artifacts", []),
            result_shape=kwargs.pop("result_shape", None),
        )
        return self.record(event)
