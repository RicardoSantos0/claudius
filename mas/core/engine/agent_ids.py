"""
Agent ID normalization utilities.

Keeps orchestration robust when the model emits shorthand IDs such as
"hr", "experts", or hyphenated names.
"""

from __future__ import annotations


# Canonical MAS agent IDs.
_ALIASES: dict[str, str] = {
    # Core
    "master": "master_orchestrator",
    "master_orchestrator": "master_orchestrator",
    "master-orchestrator": "master_orchestrator",
    "orchestrator": "master_orchestrator",
    "scribe": "scribe_agent",
    "scribe_agent": "scribe_agent",
    "scribe-agent": "scribe_agent",
    "hr": "hr_agent",
    "hr_agent": "hr_agent",
    "hr-agent": "hr_agent",
    # Delivery
    "inquirer": "inquirer_agent",
    "inquirer_agent": "inquirer_agent",
    "inquirer-agent": "inquirer_agent",
    "product_manager": "product_manager_agent",
    "product-manager": "product_manager_agent",
    "product_manager_agent": "product_manager_agent",
    "project_manager": "project_manager_agent",
    "project-manager": "project_manager_agent",
    "project_manager_agent": "project_manager_agent",
    "evaluator": "evaluator_agent",
    "evaluator_agent": "evaluator_agent",
    "evaluator-agent": "evaluator_agent",
    "trainer": "trainer_agent",
    "trainer_agent": "trainer_agent",
    "trainer-agent": "trainer_agent",
    "spawner": "spawner_agent",
    "spawner_agent": "spawner_agent",
    "spawner-agent": "spawner_agent",
    "librarian": "librarian_agent",
    "librarian_agent": "librarian_agent",
    "librarian-agent": "librarian_agent",
    # Consultant panel members
    "risk": "risk_advisor",
    "risk_advisor": "risk_advisor",
    "risk-advisor": "risk_advisor",
    "quality": "quality_advisor",
    "quality_advisor": "quality_advisor",
    "quality-advisor": "quality_advisor",
    "devils_advocate": "devils_advocate",
    "devils-advocate": "devils_advocate",
    "devil_s_advocate": "devils_advocate",
    "domain_expert": "domain_expert",
    "domain-expert": "domain_expert",
    "expert": "domain_expert",
    "efficiency_advisor": "efficiency_advisor",
    "efficiency-advisor": "efficiency_advisor",
    "efficiency": "efficiency_advisor",
    "session_scheduler": "session_scheduler",
    "session-scheduler": "session_scheduler",
}


# Group aliases that mean "consult the panel", not a single agent.
_CONSULTANT_PANEL_ALIASES: set[str] = {
    "consultants",
    "consultant_panel",
    "consultant-panel",
    "panel",
    "experts",
    "expert_panel",
    "expert-panel",
    "wxperts",  # observed typo
}


def _clean(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_")


def normalize_agent_id(agent_id: str | None) -> str | None:
    """
    Return canonical agent ID when an alias is known.
    Unknown IDs pass through unchanged (after light normalization).
    """
    if agent_id is None:
        return None
    cleaned = _clean(agent_id)
    return _ALIASES.get(cleaned, cleaned)


def is_consultant_panel_alias(value: str | None) -> bool:
    """True when value refers to the consultant panel as a group."""
    if value is None:
        return False
    return _clean(value) in _CONSULTANT_PANEL_ALIASES

