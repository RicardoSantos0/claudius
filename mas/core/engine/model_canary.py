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
) -> dict[str, Any]:
    """Run one opt-in bounded call without leaking request or credential data."""
    provider, model = _canary_target(catalog)
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
    try:
        response = invoke(
            provider=provider,
            model=model,
            credential=credential,
            prompt="Reply with OK.",
            max_output_tokens=8,
        )
        reported_model = str(response.get("reported_model") or "") or None
        status = "passed" if reported_model == model else "failed"
    except Exception:  # The audit/result must never serialize provider error text.
        status = "failed"

    audit = {
        "event": "provider_canary",
        "catalog": catalog_name,
        "provider": provider,
        "model": model,
        "status": status,
    }
    if reported_model:
        audit["reported_model"] = reported_model
    audit_sink(audit)
    result: dict[str, Any] = {
        "catalog": catalog_name,
        "model": model,
        "status": status,
    }
    if reported_model:
        result["reported_model"] = reported_model
    else:
        result["reason"] = "model_identity_unverified"
    return result
