"""
Token Counter (utils copy)

Copied into `core.utils` as part of the incremental refactor. No
internal core imports; behavior is identical to the original module.
"""

from __future__ import annotations

import sys
import json
import argparse
import math
from typing import Any

# Characters per token heuristic. Empirically ~4 for English prose,
# closer to 3 for code. We use 3.8 as a conservative middle ground.
_CHARS_PER_TOKEN: float = 3.8

# Overhead per message in the messages API (role + delimiters).
_TOKENS_PER_MESSAGE: int = 4


class TokenCounter:
    """
    Estimates token counts for strings and chat message lists.

    Backends:
      - "heuristic"  (default): char / 3.8, no dependencies
      - "tiktoken": exact count via tiktoken library (optional install)

    If tiktoken backend is requested but not installed, falls back
    to heuristic silently.
    """

    def __init__(self, backend: str = "heuristic", model: str = "cl100k_base"):
        self.backend = backend
        self.model = model
        self._encoder = None

        if backend == "tiktoken":
            self._encoder = self._load_tiktoken(model)
            if self._encoder is None:
                self.backend = "heuristic"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count(self, text: str) -> int:
        """Estimate token count for a string."""
        if not text:
            return 0
        if self.backend == "tiktoken" and self._encoder:
            return len(self._encoder.encode(text))
        return self._heuristic(text)

    def count_messages(self, messages: list[dict]) -> int:
        """
        Estimate token count for a list of chat messages.
        Each message: {"role": str, "content": str}
        """
        total = 0
        for msg in messages:
            total += _TOKENS_PER_MESSAGE
            total += self.count(str(msg.get("role", "")))
            total += self.count(str(msg.get("content", "")))
        total += 3  # reply priming overhead
        return total

    def count_dict(self, data: dict | list) -> int:
        """Estimate token count for a dict or list (serialized as JSON)."""
        try:
            text = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(data)
        return self.count(text)

    @property
    def backend_name(self) -> str:
        return self.backend

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic(text: str) -> int:
        """char / 3.8, rounded up."""
        return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))

    @staticmethod
    def _load_tiktoken(model: str):
        """Load tiktoken encoder. Returns None if not installed."""
        try:
            import tiktoken  # type: ignore
            try:
                return tiktoken.encoding_for_model(model)
            except KeyError:
                return tiktoken.get_encoding("cl100k_base")
        except ImportError:
            return None


# ---------------------------------------------------------------------------
# Module-level convenience instance
# ---------------------------------------------------------------------------

_default = TokenCounter()


def count(text: str) -> int:
    """Count tokens in a string using the default (heuristic) counter."""
    return _default.count(text)


def count_messages(messages: list[dict]) -> int:
    """Count tokens in a messages list using the default counter."""
    return _default.count_messages(messages)


def count_dict(data: Any) -> int:
    """Count tokens in a dict/list using the default counter."""
    return _default.count_dict(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate token count for a string",
        epilog='uv run python mas/core/token_counter.py "hello world"',
    )
    parser.add_argument("text", nargs="?", help="Text to count (or pipe via stdin)")
    parser.add_argument("--backend", choices=["heuristic", "tiktoken"],
                        default="heuristic")
    parser.add_argument("--model", default="cl100k_base",
                        help="Model name for tiktoken backend")
    ns = parser.parse_args()

    text = ns.text
    if text is None:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            parser.print_help()
            return 1

    tc = TokenCounter(backend=ns.backend, model=ns.model)
    n = tc.count(text)
    print(f"{n} tokens  (backend={tc.backend_name}, chars={len(text)})")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
