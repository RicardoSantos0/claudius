"""
Agent Runner
Provider-agnostic wrapper for MAS agent calls.

Live execution only. If the configured provider is unavailable (no API key /
SDK / base_url), the caller receives an explicit non-retryable error.

Every successful call is logged to the SQLite event store (mas/data/episodic.db).

Provider selection (d-009 provider seam — M-b):
  - default provider is "anthropic" (preserves the historical daily-driver behavior);
    override with env MAS_PROVIDER or AgentRunner(provider=...).
  - "anthropic" : Anthropic SDK (ANTHROPIC_API_KEY)
  - "openai"/"azure" : OpenAI-compatible API (OPENAI_API_KEY). Set MAS_OPENAI_BASE_URL
    to reach a local OpenAI-compatible endpoint (Ollama/LM Studio/Opencode/vLLM),
    keyless (a placeholder key is supplied).
  - "litellm" : unified gateway to ~100 providers (provider/model ids, e.g.
    "gemini/gemini-1.5-pro", "ollama/llama3"). Requires `pip install litellm`.
  - any other : add a ProviderAdapter subclass + register_adapter() — no AgentRunner edit.

AgentRunner owns governance (availability checks, retryable classification, event
logging); a ProviderAdapter owns one provider's SDK/transport.

Default model: claude-haiku-4-5-20251001 (fast + cheap — right for scaffolding).
Override with AgentRunner(model="claude-sonnet-4-6").

Usage:
    from core.engine.agent_runner import AgentRunner
    runner = AgentRunner()
    result = runner.run(
        agent_id="inquirer_agent",
        prompt="You are an intake agent. Summarize this brief: ...",
        project_id="proj-YYYYMMDD-NNN-true-mas-integration",
    )
    print(result["text"])
    print(result["tokens_used"])
"""

from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

# Load .env from repo root so ANTHROPIC_API_KEY is available in all entry points
try:
    from dotenv import load_dotenv as _load_dotenv
    from core.paths import repo_root
    _load_dotenv(repo_root() / ".env")
except Exception:
    pass  # optional dependency / no .env present — proceed without it

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_PROVIDER = "anthropic"
_MAX_TOKENS_DEFAULT = 1024
_LIVE_RUN_REQUIRED = (
    "Live execution is mandatory. Configure ANTHROPIC_API_KEY and the anthropic "
    "package before running MAS agents."
)

# Errors that will not resolve on retry — fail fast
_NON_RETRYABLE = (
    "credit balance is too low",
    "authentication_error",
    "permission_error",
    "your account has been",
)


def _resolve_provider() -> str:
    """Provider from env (MAS_PROVIDER), else the historical default 'anthropic'."""
    return (os.getenv("MAS_PROVIDER") or DEFAULT_PROVIDER).strip().lower()


def _resolve_base_url() -> str:
    """OpenAI-compatible base URL (env MAS_OPENAI_BASE_URL).

    Set this to target a local/open endpoint (Ollama http://localhost:11434/v1,
    LM Studio, Opencode, vLLM) through the 'openai' provider."""
    return (os.getenv("MAS_OPENAI_BASE_URL") or "").strip()


# --- Provider adapter seam ----------------------------------------------------
# Every provider is a registered adapter; AgentRunner dispatches uniformly and
# keeps governance (availability, retryable classification, logging). Adapters
# MUST NOT log — AgentRunner records the event on success.

_ADAPTERS: dict[str, "ProviderAdapter"] = {}


def register_adapter(adapter: "ProviderAdapter") -> "ProviderAdapter":
    _ADAPTERS[adapter.name] = adapter
    return adapter


class ProviderAdapter(ABC):
    name: str = ""

    @abstractmethod
    def init_client(self):
        """Return a live client object, or None when the provider is unavailable."""

    @abstractmethod
    def call(self, client, *, agent_id, prompt, system_prompt, model, max_tokens) -> dict:
        """Run one call; return {text, tokens_used, model, provider[, tokens_prompt,
        tokens_completion][, error]}."""


class _AnthropicAdapter(ProviderAdapter):
    """Anthropic SDK — the default provider (preserves historical behavior)."""

    name = "anthropic"

    def init_client(self):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return None
        try:
            import anthropic
            return anthropic.Anthropic(api_key=key)
        except ImportError:
            return None

    def call(self, client, *, agent_id, prompt, system_prompt, model, max_tokens) -> dict:
        _ = agent_id
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        try:
            msg = client.messages.create(**kwargs)
            text = msg.content[0].text if msg.content else ""
            tp = msg.usage.input_tokens or 0
            tc = msg.usage.output_tokens or 0
            return {
                "text": text, "tokens_used": tp + tc,
                "tokens_prompt": tp, "tokens_completion": tc,
                "model": model, "provider": "anthropic",
            }
        except Exception as exc:
            return {"text": "", "tokens_used": 0, "model": model,
                    "provider": "anthropic", "error": str(exc)}


class _OpenAIAdapter(ProviderAdapter):
    """OpenAI-compatible API ("openai"/"azure"). Set MAS_OPENAI_BASE_URL to reach a
    local OpenAI-compatible endpoint (Ollama/LM Studio/Opencode/vLLM) keyless."""

    def __init__(self, name: str = "openai"):
        self.name = name

    def init_client(self):
        base_url = _resolve_base_url()
        key = os.getenv("OPENAI_API_KEY")
        if not key and base_url:
            key = "sk-local"  # local servers don't need a real key
        if not key:
            return None
        try:
            import openai
            kwargs = {"api_key": key}
            if base_url:
                kwargs["base_url"] = base_url
            return openai.OpenAI(**kwargs)
        except ImportError:
            return None

    def call(self, client, *, agent_id, prompt, system_prompt, model, max_tokens) -> dict:
        _ = agent_id
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = client.chat.completions.create(model=model, max_tokens=max_tokens, messages=messages)
            text = resp.choices[0].message.content or ""
            tokens = resp.usage.total_tokens if resp.usage else 0
            return {"text": text, "tokens_used": tokens, "model": model, "provider": self.name}
        except Exception as exc:
            return {"text": "", "tokens_used": 0, "model": model,
                    "provider": self.name, "error": str(exc)}


class _LiteLLMAdapter(ProviderAdapter):
    """Unified gateway to ~100 providers (OpenAI, Anthropic, Gemini, Ollama, LM Studio,
    Opencode, vLLM ...) via LiteLLM. Use provider/model ids, e.g. 'gemini/gemini-1.5-pro',
    'ollama/llama3'. For local OpenAI-compatible endpoints set MAS_OPENAI_BASE_URL
    (passed through as api_base). Requires `pip install litellm`."""

    name = "litellm"

    def init_client(self):
        try:
            import litellm
            return litellm
        except ImportError:
            return None

    def call(self, client, *, agent_id, prompt, system_prompt, model, max_tokens) -> dict:
        _ = agent_id
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens}
        base_url = _resolve_base_url()
        if base_url:
            kwargs["api_base"] = base_url
        try:
            resp = client.completion(**kwargs)
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            tokens = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
            return {"text": text, "tokens_used": tokens, "model": model, "provider": "litellm"}
        except Exception as exc:
            return {"text": "", "tokens_used": 0, "model": model,
                    "provider": "litellm", "error": str(exc)}


register_adapter(_AnthropicAdapter())
register_adapter(_OpenAIAdapter("openai"))
register_adapter(_OpenAIAdapter("azure"))
register_adapter(_LiteLLMAdapter())


class AgentRunner:
    """
    Provider-agnostic wrapper for MAS agent calls.
    Thread-safe: one client instance, stateless per call.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        db_path: Optional[Path] = None,
        provider: Optional[str] = None,
    ):
        self.model = model
        self.provider = (provider or _resolve_provider()).strip().lower()
        self._db_path = db_path
        self._adapter = _ADAPTERS.get(self.provider)
        self._client = self._adapter.init_client() if self._adapter else None

    @property
    def available(self) -> bool:
        """True if a live provider client is ready (key/SDK/base_url present)."""
        return self._client is not None

    def _unavailable_msg(self) -> str:
        if self.provider == "anthropic":
            return _LIVE_RUN_REQUIRED
        return (
            f"Live execution is mandatory. Provider '{self.provider}' is unavailable "
            "(missing SDK, API key, or base_url)."
        )

    # ------------------------------------------------------------------
    # Core call
    # ------------------------------------------------------------------

    def run(
        self,
        agent_id: str,
        prompt: str,
        project_id: str = "",
        max_tokens: int = _MAX_TOKENS_DEFAULT,
        system_prompt: str = "",
    ) -> dict:
        """
        Call an agent via the configured provider.

        Returns:
            {
                "text":        str  — response text (empty on error)
                "tokens_used": int  — total tokens consumed
                "model":       str  — model that was called
                "error":       str  — set on error (absent on success)
                "retryable":   bool — set on error; False for auth/credit errors
            }
        """
        if self._adapter is None:
            return {
                "text": "", "tokens_used": 0, "model": self.model,
                "error": f"unsupported_provider: {self.provider}", "retryable": False,
            }

        if not self.available:
            return {
                "text": "", "tokens_used": 0, "model": self.model,
                "error": self._unavailable_msg(), "retryable": False,
            }

        result = self._adapter.call(
            self._client, agent_id=agent_id, prompt=prompt,
            system_prompt=system_prompt, model=self.model, max_tokens=max_tokens,
        )

        if result.get("error"):
            error_str = result["error"]
            retryable = not any(m in error_str.lower() for m in _NON_RETRYABLE)
            return {
                "text": "", "tokens_used": 0, "model": self.model,
                "error": error_str, "retryable": retryable,
            }

        tp = int(result.get("tokens_prompt", 0) or 0)
        tc = int(result.get("tokens_completion", 0) or 0)
        if not tp and not tc:  # providers that report only a total
            tc = int(result.get("tokens_used", 0) or 0)
        self._log_event(project_id, agent_id, prompt,
                        tokens_prompt=tp, tokens_completion=tc)

        return {
            "text": result.get("text", ""),
            "tokens_used": result.get("tokens_used", 0),
            "model": self.model,
        }

    # ------------------------------------------------------------------
    # SQLite logging
    # ------------------------------------------------------------------

    def _log_event(
        self,
        project_id: str,
        agent_id: str,
        prompt: str,
        tokens_prompt: int = 0,
        tokens_completion: int = 0,
    ) -> None:
        """Write an agent_call event to SQLite. Non-fatal."""
        if not project_id:
            return
        try:
            from core.db import append_event
            kwargs: dict = {}
            if self._db_path:
                kwargs["db_path"] = self._db_path
            tokens_total = tokens_prompt + tokens_completion
            append_event(
                project_id=project_id,
                agent_id=agent_id,
                action_type="agent_call",
                intent=prompt[:120],
                result_shape=f"tokens={tokens_total}",
                payload={
                    "model":             self.model,
                    "provider":          self.provider,
                    "tokens_prompt":     tokens_prompt,
                    "tokens_completion": tokens_completion,
                    "tokens_total":      tokens_total,
                },
                **kwargs,
            )
        except Exception as exc:
            logger.debug("agent-runner telemetry failed (non-blocking): %s", exc)
