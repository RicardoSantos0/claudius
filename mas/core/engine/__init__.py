"""Engine subpackage — governed delivery engine modules.

All modules are physically located in this package.
Import directly from submodules, e.g.:

    from core.engine.handoff_engine import HandoffEngine
    from core.engine.shared_state_manager import SharedStateManager
"""

from . import access_control
from . import audit_logger
from . import capability_registry
from . import checkpoint_writer
from . import consultation_engine
from . import context_compressor
from . import handoff_engine
from . import handoff_helpers
from . import intake_checker
from . import message_bus
from . import metrics_engine
from . import prompt_assembler
from . import shared_state_manager
from . import skill_bridge
from . import spawn_policy
from . import task_board
from . import training_engine

__all__ = [
    "access_control", "audit_logger", "capability_registry", "checkpoint_writer",
    "consultation_engine", "context_compressor", "handoff_engine",
    "handoff_helpers", "intake_checker", "message_bus", "metrics_engine",
    "prompt_assembler", "shared_state_manager", "skill_bridge", "spawn_policy",
    "task_board", "training_engine",
]
