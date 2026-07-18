"""Provider-neutral semantic execution-profile routing for MAS dispatch.

Routing is deterministic and intentionally keeps provider/model identifiers at
the configuration boundary. Callers select a semantic profile (reasoning,
standard, economy); this module resolves the configured provider and model.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

from core.utils.config import load_config

ExecutionProfile = Literal["reasoning", "standard", "economy"]
VALID_PROFILES: tuple[ExecutionProfile, ...] = ("reasoning", "standard", "economy")


class RouteConfigurationError(ValueError):
    """Raised when an explicitly selected route cannot be resolved safely."""


class RouteAuditPersistenceError(RuntimeError):
    """Raised when required route-audit persistence cannot be guaranteed."""


@dataclass(frozen=True)
class RouteSelection:
    agent_id: str
    phase: str
    profile: ExecutionProfile
    provider: str
    model: str
    source: str
    reason: str
    risk_level: str | None
    retry_count: int
    enforcement_capability: str
    enforced: bool
    surface: str
    surface_adapter: str
    reasoning_effort: str | None
    launch_args: list[str]
    provider_catalog: str | None
    legacy_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionProfileRouter:
    """Resolve one dispatch using the canonical precedence contract.

    Precedence (highest first): explicit override, risk/retry/critical-agent
    escalation, agent override, phase mapping, legacy master/default model.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.llm = self.config.get("llm", {}) or {}
        self.routing = self.llm.get("routing", {}) or {}

    @property
    def manual_enforcement_capability(self) -> str:
        return str(self.routing.get("manual_enforcement_capability", "advisory"))

    def enforcement_for_surface(self, surface: str | None) -> str:
        if surface:
            surface_cfg = (self.llm.get("surfaces", {}) or {}).get(surface, {}) or {}
            if surface_cfg.get("enforcement_capability"):
                return str(surface_cfg["enforcement_capability"])
        return self.manual_enforcement_capability

    def resolve(
        self,
        agent_id: str,
        phase: str = "",
        *,
        explicit_profile: str | None = None,
        explicit_model: str | None = None,
        explicit_provider: str | None = None,
        risk_level: str | None = None,
        retry_count: int = 0,
        enforcement_capability: str | None = None,
        surface: str | None = None,
        provider_catalog: str | None = None,
    ) -> RouteSelection:
        self._audit_persistence_policy()
        if bool(explicit_provider) != bool(explicit_model):
            missing = "model" if explicit_provider else "provider"
            raise RouteConfigurationError(
                "partial explicit provider/model override is unsafe: "
                f"missing explicit {missing}; provide both --provider and --model, "
                "or select a configured profile/catalog"
            )

        profile: ExecutionProfile
        provider_override: str | None = explicit_provider
        model_override: str | None = explicit_model
        source: str
        reason: str
        legacy_fallback = False

        if explicit_profile or explicit_model or explicit_provider:
            profile = self._normalize_profile(
                explicit_profile or self._profile_for_model(explicit_model) or "standard"
            )
            source = (
                "explicit_model"
                if explicit_model
                else "explicit_profile"
                if explicit_profile
                else "explicit_provider"
            )
            reason = f"{source.replace('_', ' ')} override"
        else:
            escalation = self._escalation(agent_id, risk_level, retry_count)
            if escalation:
                profile, source, reason = escalation
            else:
                agent_override = (self.llm.get("agent_overrides", {}) or {}).get(agent_id)
                if agent_override not in (None, ""):
                    profile, provider_override, model_override = self._agent_override(
                        agent_override
                    )
                    source = "agent_override"
                    reason = f"configured override for {agent_id}"
                else:
                    phase_profile = (self.llm.get("phase_profiles", {}) or {}).get(phase)
                    if phase_profile:
                        profile = self._normalize_profile(str(phase_profile))
                        source = "phase"
                        reason = f"configured mapping for phase {phase}"
                    else:
                        profile = "reasoning" if agent_id == "master_orchestrator" else "standard"
                        source = "legacy_default"
                        reason = (
                            "legacy master_model fallback"
                            if agent_id == "master_orchestrator"
                            else "legacy default_model fallback"
                        )
                        model_override = self._legacy_model(agent_id)
                        legacy_fallback = True

        surface_name = (surface or "generic").strip().lower()
        if not (provider_override and model_override):
            self._validate_environment_override(profile, surface_name)
        surface_cfg = (self.llm.get("surfaces", {}) or {}).get(surface_name, {}) or {}
        catalogs = self.llm.get("provider_catalogs", {}) or {}
        surface_catalog_env = (
            os.getenv(f"MAS_{surface_name.upper()}_CATALOG") if surface else None
        )
        configured_surface_catalog = surface_cfg.get("catalog")
        if configured_surface_catalog == "inherit":
            configured_surface_catalog = None
        env_provider_catalog = os.getenv("MAS_PROVIDER")
        if env_provider_catalog not in catalogs:
            env_provider_catalog = None
        catalog_name = str(
            provider_catalog
            or surface_catalog_env
            or os.getenv("MAS_MODEL_CATALOG")
            or configured_surface_catalog
            or env_provider_catalog
            or (self.llm.get("provider") if self.llm.get("provider") in catalogs else "")
            or self.llm.get("default_provider_catalog")
            or ""
        )
        if catalog_name and catalog_name not in catalogs:
            available = ", ".join(sorted(catalogs)) or "<none configured>"
            raise RouteConfigurationError(
                f"unknown provider catalog {catalog_name!r}; "
                f"available catalogs: {available}"
            )
        catalog_cfg = catalogs.get(catalog_name, {}) or {}
        if catalog_name:
            self._validate_catalog_lifecycle(catalog_name, catalog_cfg)
        catalog_target = (catalog_cfg.get("profiles", {}) or {}).get(profile)
        if catalog_name and catalog_target is None:
            available_profiles = ", ".join(
                sorted((catalog_cfg.get("profiles", {}) or {}).keys())
            ) or "<none configured>"
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} is incomplete: "
                f"missing profile {profile!r}; available profiles: {available_profiles}"
            )
        if isinstance(catalog_target, str):
            catalog_target = {"model": catalog_target}
        elif catalog_target is not None and not isinstance(catalog_target, dict):
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} profile {profile!r} must be a mapping"
            )
        if isinstance(catalog_target, dict):
            catalog_target = {
                "provider": catalog_cfg.get("provider"),
                **catalog_target,
            }
            missing_fields = [
                field for field in ("provider", "model") if not catalog_target.get(field)
            ]
            if missing_fields:
                raise RouteConfigurationError(
                    f"provider catalog {catalog_name!r} profile {profile!r} is incomplete: "
                    f"missing {', '.join(missing_fields)}"
                )
        surface_target = (surface_cfg.get("profiles", {}) or {}).get(profile)
        if isinstance(surface_target, str):
            surface_target = {"model": surface_target}
        catalog_explicit = bool(provider_catalog or surface_catalog_env or os.getenv("MAS_MODEL_CATALOG"))
        profile_target = (
            catalog_target
            if catalog_explicit and catalog_target
            else surface_target or catalog_target or self._configured_profile_target(profile)
        )
        if profile_target is None:
            provider = str(os.getenv("MAS_PROVIDER") or self.llm.get("provider", "anthropic"))
            model = model_override or self._legacy_model(agent_id)
            if source != "explicit_model":
                source = "legacy_default"
                reason = f"profile {profile!r} is not configured; legacy model fallback"
                legacy_fallback = True
        else:
            provider, model = self._target_values(profile, profile_target, surface_name)
        provider = provider_override or provider
        model = model_override or model
        env_prefix = profile.upper()
        if surface_name == "generic" and (
            os.getenv(f"MAS_{env_prefix}_PROVIDER")
            or os.getenv(f"MAS_{env_prefix}_MODEL")
        ):
            reason = f"{reason}; profile target overridden by environment"
        if enforcement_capability:
            capability = str(enforcement_capability)
        elif surface is None:
            # Autonomous dispatch applies the selected provider/model directly.
            # Do not let the generic manual-surface default downgrade this fact.
            capability = "engine_enforced"
        else:
            capability = str(
                surface_cfg.get("enforcement_capability")
                or self.manual_enforcement_capability
            )
        enforced = capability in {"enforced", "engine_enforced", "client_enforced"}
        adapter = str(
            surface_cfg.get("adapter")
            or catalog_cfg.get("adapter")
            or ("manual" if surface_name == "generic" else surface_name)
        )
        reasoning_effort = profile_target.get("reasoning_effort") if profile_target else None
        launch_args = [
            str(value).format(provider=provider, model=model, profile=profile)
            for value in (profile_target.get("launch_args", []) if profile_target else [])
        ]
        if surface_name == "opencode":
            surface_provider = (
                profile_target.get("opencode_provider")
                or profile_target.get("surface_provider")
                or catalog_cfg.get("opencode_provider")
                if profile_target
                else catalog_cfg.get("opencode_provider")
            )
            surface_model = (
                profile_target.get("opencode_model")
                or profile_target.get("surface_model")
                if profile_target
                else None
            )
            if surface_provider:
                provider = str(surface_provider)
            if surface_model:
                model = str(surface_model)
        if surface_name == "opencode" and not launch_args:
            selector = model if "/" in model else f"{provider}/{model}"
            launch_args = ["-m", selector]
        return RouteSelection(
            agent_id=agent_id,
            phase=phase,
            profile=profile,
            provider=provider,
            model=model,
            source=source,
            reason=reason,
            risk_level=risk_level,
            retry_count=max(0, int(retry_count)),
            enforcement_capability=capability,
            enforced=enforced,
            surface=surface_name,
            surface_adapter=adapter,
            reasoning_effort=(str(reasoning_effort) if reasoning_effort else None),
            launch_args=launch_args,
            provider_catalog=(catalog_name or None),
            legacy_fallback=legacy_fallback,
        )

    def build_manual_envelope(
        self,
        *,
        project_id: str,
        agent_id: str,
        phase: str,
        prompt: str,
        selection: RouteSelection,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "kind": "mas_manual_execution",
            "project_id": project_id,
            "agent_id": agent_id,
            "phase": phase,
            "routing": selection.to_dict(),
            "enforcement": {
                "capability": selection.enforcement_capability,
                "enforced": selection.enforced,
                "requested_provider": selection.provider,
                "requested_model": selection.model,
                "surface": selection.surface,
                "adapter": selection.surface_adapter,
                "reasoning_effort": selection.reasoning_effort,
                "launch_args": selection.launch_args,
                "instruction": (
                    "Use the requested provider/model for this execution."
                    if selection.enforced
                    else "Advisory only: this manual surface cannot enforce provider/model selection."
                ),
            },
            "prompt": prompt,
        }

    def record_route_selection(self, project_id: str, selection: RouteSelection) -> str:
        """Record routing provenance with the existing decision event convention."""
        if not project_id:
            return ""
        policy = self._audit_persistence_policy()
        try:
            from core.engine.event_recorder import EventRecorder

            action_id = EventRecorder().record_simple(
                project_id=project_id,
                actor=selection.agent_id,
                action_type="decision_recorded",
                intent=f"Execution route selected: {selection.profile}",
                phase=selection.phase or None,
                payload={
                    "decision_type": "execution_route_selection",
                    "route_selection": selection.to_dict(),
                },
            )
            if action_id:
                return action_id
            if policy == "required":
                raise RouteAuditPersistenceError(
                    "required route audit persistence failed: event was not persisted"
                )
            warnings.warn(
                "best-effort route audit was not persisted",
                RuntimeWarning,
                stacklevel=2,
            )
        except RouteAuditPersistenceError:
            raise
        except Exception as exc:
            if policy == "required":
                raise RouteAuditPersistenceError(
                    f"required route audit persistence failed: {type(exc).__name__}"
                ) from exc
            warnings.warn(
                f"best-effort route audit was not persisted: {type(exc).__name__}",
                RuntimeWarning,
                stacklevel=2,
            )
            return ""
        return ""

    def _audit_persistence_policy(self) -> str:
        policy = str(self.routing.get("audit_persistence", "best_effort"))
        if policy not in {"best_effort", "required"}:
            raise RouteConfigurationError(
                "route audit persistence must be 'best_effort' or 'required'"
            )
        return policy

    def _validate_catalog_lifecycle(
        self, catalog_name: str, catalog: dict[str, Any]
    ) -> None:
        policy = self.routing.get("catalog_lifecycle", {}) or {}
        if not policy.get("enabled", False):
            return
        source = str(catalog.get("source_url") or "")
        if not source.startswith("https://"):
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} lifecycle source must be HTTPS"
            )
        lifecycle = str(catalog.get("lifecycle_state") or "")
        if lifecycle != "active":
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} lifecycle is not active"
            )
        raw_validated = str(catalog.get("validated_at") or "")
        try:
            validated_at = date.fromisoformat(raw_validated)
        except ValueError as exc:
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} lifecycle validation date is invalid"
            ) from exc
        raw_as_of = policy.get("as_of_date")
        try:
            as_of = date.fromisoformat(str(raw_as_of)) if raw_as_of else date.today()
        except ValueError as exc:
            raise RouteConfigurationError(
                "catalog lifecycle as_of_date must use YYYY-MM-DD"
            ) from exc
        try:
            max_age_days = int(policy.get("max_age_days", 92))
        except (TypeError, ValueError) as exc:
            raise RouteConfigurationError(
                "catalog lifecycle max_age_days must be an integer"
            ) from exc
        age_days = (as_of - validated_at).days
        if age_days < 0:
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} lifecycle validation is future-dated"
            )
        if age_days > max_age_days:
            raise RouteConfigurationError(
                f"provider catalog {catalog_name!r} lifecycle metadata is stale"
            )

    def _profile_target(self, profile: ExecutionProfile) -> tuple[str, str]:
        raw = self._configured_profile_target(profile) or {}
        return self._target_values(profile, raw, "generic")

    @staticmethod
    def _profile_environment_values(profile: ExecutionProfile) -> tuple[str | None, str | None]:
        prefix = profile.upper()
        return os.getenv(f"MAS_{prefix}_PROVIDER"), os.getenv(f"MAS_{prefix}_MODEL")

    def _validate_environment_override(
        self, profile: ExecutionProfile, surface: str
    ) -> None:
        if surface != "generic":
            return
        provider, model = self._profile_environment_values(profile)
        if bool(provider) != bool(model):
            missing = f"MAS_{profile.upper()}_{'MODEL' if provider else 'PROVIDER'}"
            raise RouteConfigurationError(
                "partial profile environment override is unsafe: "
                f"{missing} is missing; set provider and model together"
            )

    def _target_values(
        self,
        profile: ExecutionProfile,
        raw: dict[str, Any],
        surface: str,
    ) -> tuple[str, str]:
        if isinstance(raw, str):
            raw = {"model": raw}
        env_provider, env_model = (
            self._profile_environment_values(profile)
            if surface == "generic"
            else (None, None)
        )
        provider = env_provider or raw.get("provider")
        model = env_model or raw.get("model")
        provider = str(provider or os.getenv("MAS_PROVIDER") or self.llm.get("provider", "anthropic"))
        if not model:
            model = self._legacy_model(
                "master_orchestrator" if profile == "reasoning" else "default"
            )
        return provider, str(model)

    def _configured_profile_target(self, profile: ExecutionProfile) -> dict[str, Any] | None:
        raw = (self.llm.get("execution_profiles", {}) or {}).get(profile)
        if raw is None:
            return None
        if isinstance(raw, str):
            return {"model": raw}
        if isinstance(raw, dict):
            return raw
        return None

    def _legacy_model(self, agent_id: str) -> str:
        if agent_id == "master_orchestrator":
            return str(os.getenv("MAS_MASTER_MODEL") or self.llm.get("master_model", "claude-fable-5"))
        return str(os.getenv("MAS_DEFAULT_MODEL") or self.llm.get("default_model", "claude-sonnet-5"))

    def _profile_for_model(self, model: str | None) -> ExecutionProfile | None:
        if not model:
            return None
        for profile in VALID_PROFILES:
            if self._profile_target(profile)[1] == model:
                return profile
        return None

    def _normalize_profile(self, profile: str) -> ExecutionProfile:
        normalized = profile.strip().lower()
        if normalized not in VALID_PROFILES:
            raise ValueError(
                f"execution profile must be one of {VALID_PROFILES}, got {profile!r}"
            )
        return normalized  # type: ignore[return-value]

    def _agent_override(
        self, override: Any
    ) -> tuple[ExecutionProfile, str | None, str | None]:
        if isinstance(override, str):
            if override.strip().lower() in VALID_PROFILES:
                return self._normalize_profile(override), None, None
            return self._profile_for_model(override) or "standard", None, override
        if isinstance(override, dict):
            model = override.get("model")
            provider = override.get("provider")
            if bool(provider) != bool(model):
                raise RouteConfigurationError(
                    "partial agent provider/model override is unsafe; "
                    "configure both provider and model, or use only a semantic profile"
                )
            profile = self._normalize_profile(
                str(override.get("profile") or self._profile_for_model(model) or "standard")
            )
            return profile, provider, model
        raise ValueError(f"invalid agent override for routing: {override!r}")

    def _escalation(
        self, agent_id: str, risk_level: str | None, retry_count: int
    ) -> tuple[ExecutionProfile, str, str] | None:
        critical_agents = set(self.routing.get("critical_agents", []) or [])
        if agent_id in critical_agents:
            profile = self._normalize_profile(
                str(self.routing.get("critical_agent_profile", "reasoning"))
            )
            return profile, "critical_agent", f"critical agent escalation for {agent_id}"
        levels = {
            str(level).strip().lower()
            for level in self.routing.get("risk_escalation_levels", ["high", "critical"])
        }
        if risk_level and risk_level.strip().lower() in levels:
            profile = self._normalize_profile(
                str(self.routing.get("risk_escalation_profile", "reasoning"))
            )
            return profile, "risk_escalation", (
                f"risk escalation for {risk_level.strip().lower()} risk"
            )
        threshold = int(self.routing.get("retry_escalation_threshold", 1))
        if threshold >= 0 and retry_count >= threshold and retry_count > 0:
            profile = self._normalize_profile(
                str(self.routing.get("retry_escalation_profile", "reasoning"))
            )
            return profile, "retry_escalation", f"retry escalation at attempt {retry_count}"
        return None


def build_manual_envelope(
    project_id: str,
    agent_id: str,
    phase: str,
    prompt: str,
    selection: RouteSelection,
) -> dict[str, Any]:
    """Build the shared CLI/MCP manual execution envelope contract."""
    return ExecutionProfileRouter().build_manual_envelope(
        project_id=project_id,
        agent_id=agent_id,
        phase=phase,
        prompt=prompt,
        selection=selection,
    )
