"""
Agent Communication Bus
All agent-to-agent communication flows through this bus.
V1: synchronous direct calls. Abstraction allows future async migration.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from core.utils.token_counter import TokenCounter

_token_counter = TokenCounter()


class MessageType(Enum):
    HANDOFF = "handoff"
    CAPABILITY_QUERY = "capability_query"
    CAPABILITY_RESPONSE = "capability_response"
    CONSULTATION_REQUEST = "consultation_request"
    CONSULTATION_RESPONSE = "consultation_response"
    RECORD = "record"
    DIRECTIVE = "directive"
    ERROR = "error"
    ESCALATION = "escalation"
    PERFORMANCE_UPDATE = "performance_update"
    SPAWN_REQUEST = "spawn_request"
    SPAWN_RESULT = "spawn_result"
    VERIFICATION_REQUEST = "verification_request"
    VERIFICATION_RESULT = "verification_result"
    REGISTER_AGENT = "register_agent"


@dataclass
class Message:
    message_id: str
    message_type: MessageType
    from_agent: str
    to_agent: str
    project_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload: dict = field(default_factory=dict)
    requires_response: bool = False
    correlation_id: Optional[str] = None
    token_usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0
    })


@dataclass
class MessageResult:
    success: bool
    response: Optional[dict] = None
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success


class MessageBus(ABC):
    """Abstract message bus interface."""

    @abstractmethod
    def send(self, message: Message) -> MessageResult:
        """Send a message and optionally wait for response."""

    @abstractmethod
    def register_agent(self, agent_id: str, handler: Callable) -> None:
        """Register an agent's message handler."""

    @abstractmethod
    def get_message_log(self, project_id: str) -> list[Message]:
        """Get all messages for a project."""


class DirectCallBus(MessageBus):
    """
    V1: synchronous direct function calls.
    Messages are delivered immediately; responses returned synchronously.
    """

    def __init__(self, audit_logger=None):
        self._handlers: dict[str, Callable] = {}
        self._message_log: list[Message] = []
        self._audit_logger = audit_logger

    def register_agent(self, agent_id: str, handler: Callable) -> None:
        self._handlers[agent_id] = handler

    def unregister_agent(self, agent_id: str) -> None:
        self._handlers.pop(agent_id, None)

    def send(self, message: Message) -> MessageResult:
        # Estimate payload token cost if not already set by caller
        if message.token_usage.get("total_tokens", 0) == 0:
            payload_tokens = _token_counter.count_dict(message.payload)
            message.token_usage = {
                "prompt_tokens": payload_tokens,
                "completion_tokens": 0,
                "total_tokens": payload_tokens,
            }

        self._message_log.append(message)

        if self._audit_logger:
            self._audit_logger.log(
                "message_sent",
                from_agent=message.from_agent,
                to_agent=message.to_agent,
                message_type=message.message_type.value,
                project_id=message.project_id,
                message_id=message.message_id,
            )

        handler = self._handlers.get(message.to_agent)
        if handler is None:
            return MessageResult(
                success=False,
                error=f"No handler registered for agent: {message.to_agent}",
            )

        try:
            response = handler(message)
            return MessageResult(success=True, response=response)
        except Exception as e:
            return MessageResult(success=False, error=str(e))

    def get_message_log(self, project_id: str) -> list[Message]:
        return [m for m in self._message_log if m.project_id == project_id]

    def get_all_messages(self) -> list[Message]:
        return list(self._message_log)

    def registered_agents(self) -> list[str]:
        return list(self._handlers.keys())
