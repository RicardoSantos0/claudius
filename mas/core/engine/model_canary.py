"""Pure, injected provider canary used by CLI and release verification."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def _canary_target(catalog: Mapping[str, Any]) -> tuple[str, str]:
    profiles = catalog.get("profiles", {}) or {}
    for profile in ("standard", "economy", "reasoning"):
        target = profiles.get(profile)
        if isinstance(target, str):
            return str(catalog.get("provider", "")), target
        if isinstance(target, Mapping) and target.get("model"):
            return str(target.get("provider") or catalog.get("provider") or ""), str(
                target["model"]
            )
    raise ValueError("provider catalog has no canary-capable model profile")


def run_provider_canary(
    catalog_name: str,
    catalog: Mapping[str, Any],
    credential_lookup: Callable[[str], str | None],
    invoke: Callable[..., Mapping[str, Any]],
    audit_sink: Callable[[dict[str, Any]], None],
    *,
    provider: str | None = None,
    model: str | None = None,
    approved_candidates: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one opt-in bounded call without leaking request or credential data."""
    catalog_provider, catalog_model = _canary_target(catalog)
    provider = provider or catalog_provider
    model = model or catalog_model
    candidates = approved_candidates or [{"provider": provider, "model": model}]
    approved_routes = {
        (
            str(candidate.get("provider") or "").lower(),
            str(candidate.get("model") or "").lower(),
        )
        for candidate in candidates
        if isinstance(candidate, Mapping)
    }
    credential_env = str(catalog.get("credential_env") or "")
    credential = credential_lookup(credential_env) if credential_env else None
    if not credential:
        return {
            "catalog": catalog_name,
            "model": model,
            "status": "skipped",
            "reason": "missing_credentials",
        }

    status = "failed"
    reported_model: str | None = None
    reported_provider: str | None = None
    reason = "model_identity_unverified"
    try:
        response = invoke(
            provider=provider,
            model=model,
            credential=credential,
            prompt="Reply with OK.",
            max_output_tokens=8,
        )
        reported_model = str(response.get("reported_model") or "") or None
        reported_provider = (
            str(response.get("reported_provider") or provider) or None
        )
        reported_route = (
            str(reported_provider or "").lower(),
            str(reported_model or "").lower(),
        )
        if reported_model and reported_route in approved_routes:
            status = "passed"
            reason = ""
        elif reported_model:
            reason = "model_outside_approved_candidates"
    except Exception:  # The audit/result must never serialize provider error text.
        status = "failed"
        reason = "provider_call_failed"

    audit = {
        "event": "provider_canary",
        "catalog": catalog_name,
        "provider": provider,
        "model": model,
        "status": status,
    }
    if reported_model:
        audit["reported_model"] = reported_model
    if reported_provider:
        audit["reported_provider"] = reported_provider
    audit_sink(audit)
    result: dict[str, Any] = {
        "catalog": catalog_name,
        "provider": provider,
        "model": model,
        "status": status,
    }
    if reported_model:
        result["reported_model"] = reported_model
    if reported_provider:
        result["reported_provider"] = reported_provider
    if reason:
        result["reason"] = reason
    return result
