"""Naming-convention check (prop-TP-044 / proj-YYYYMMDD-NNN).

Given a *proposed* file path, inspect its sibling directory for prevailing
naming patterns (specifically: shared underscore-separated suffix tokens
before the extension) and flag mismatches.

Use cases:
- Planning-phase pre-flight by ``product_manager_agent`` /
  ``project_manager_agent`` when proposing new artifact paths.
- Reliability-engineer paired-test check.

Library API
-----------
    from core.tools.naming_convention_check import check_path
    result = check_path("docs/providers/opencode.md", repo_root=Path("."))
    # -> {
    #     'ok': False,
    #     'proposed': 'opencode.md',
    #     'suggestion': 'opencode_cli.md',
    #     'reason': "Sibling files share suffix '_cli'; proposed name omits it.",
    #     'sibling_examples': ['codex_cli.md', 'claude_code_cli.md'],
    # }

CLI
---
    uv run python -m mas.core.tools.naming_convention_check <path>
    # exit 0 — matches convention (or no convention to enforce)
    # exit 1 — mismatch (suggestion printed to stdout)
    # exit 2 — usage error or parent directory missing
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


# Minimum sibling support for a token to count as a "convention".
_MIN_SIBLING_SUPPORT = 2


def _split_tokens(stem: str) -> list[str]:
    """Split a filename stem into underscore-separated tokens."""
    return [t for t in stem.split("_") if t]


def _dominant_suffix(sibling_stems: Iterable[str]) -> str | None:
    """Find the most common trailing token across siblings, if any.

    A "dominant suffix" is a single token that appears as the *last*
    underscore-separated token in at least ``_MIN_SIBLING_SUPPORT`` siblings.
    """
    counter: Counter[str] = Counter()
    for stem in sibling_stems:
        tokens = _split_tokens(stem)
        if not tokens:
            continue
        counter[tokens[-1]] += 1
    if not counter:
        return None
    token, count = counter.most_common(1)[0]
    if count >= _MIN_SIBLING_SUPPORT:
        return token
    return None


def check_path(proposed: str | Path, repo_root: str | Path | None = None) -> dict:
    """Check whether ``proposed`` aligns with sibling-directory naming.

    Returns a dict with keys:
    - ``ok`` (bool): True iff no mismatch was detected.
    - ``proposed`` (str): basename of the proposed path.
    - ``suggestion`` (str | None): aligned name, or None if no change needed.
    - ``reason`` (str): human-readable explanation.
    - ``sibling_examples`` (list[str]): up to 5 sibling basenames sampled.
    """
    p = Path(proposed)
    parent = p.parent
    if repo_root is not None:
        if not parent.is_absolute():
            parent = (Path(repo_root) / parent).resolve()
    name = p.name
    stem = p.stem

    if not parent.exists() or not parent.is_dir():
        return {
            "ok": True,
            "proposed": name,
            "suggestion": None,
            "reason": (
                f"Parent directory '{parent}' does not exist — "
                "no siblings to compare against."
            ),
            "sibling_examples": [],
        }

    # Collect siblings with the same extension.
    sibling_paths = [
        f for f in parent.iterdir()
        if f.is_file() and f.suffix == p.suffix and f.name != name
    ]
    sibling_stems = [f.stem for f in sibling_paths]

    if not sibling_stems:
        return {
            "ok": True,
            "proposed": name,
            "suggestion": None,
            "reason": "No siblings with matching extension — no convention to enforce.",
            "sibling_examples": [],
        }

    dominant = _dominant_suffix(sibling_stems)
    examples = sorted(f.name for f in sibling_paths)[:5]

    if dominant is None:
        return {
            "ok": True,
            "proposed": name,
            "suggestion": None,
            "reason": "Siblings have no dominant suffix token.",
            "sibling_examples": examples,
        }

    tokens = _split_tokens(stem)
    if tokens and tokens[-1] == dominant:
        return {
            "ok": True,
            "proposed": name,
            "suggestion": None,
            "reason": f"Proposed name matches dominant suffix '_{dominant}{p.suffix}'.",
            "sibling_examples": examples,
        }

    # Build a suggestion that appends the dominant suffix.
    suggested_stem = f"{stem}_{dominant}" if stem else dominant
    suggestion = f"{suggested_stem}{p.suffix}"

    return {
        "ok": False,
        "proposed": name,
        "suggestion": suggestion,
        "reason": (
            f"Sibling files share suffix '_{dominant}{p.suffix}' "
            f"(seen in {', '.join(examples)}); proposed name omits it."
        ),
        "sibling_examples": examples,
    }


def _format_human(result: dict) -> str:
    lines = [f"proposed: {result['proposed']}"]
    if result["ok"]:
        lines.append(f"status: OK — {result['reason']}")
    else:
        lines.append(f"status: MISMATCH — {result['reason']}")
        lines.append(f"suggestion: {result['suggestion']}")
    if result["sibling_examples"]:
        lines.append(f"siblings: {', '.join(result['sibling_examples'])}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naming_convention_check",
        description="Check a proposed file path against sibling naming conventions.",
    )
    parser.add_argument("path", help="Proposed file path to check.")
    parser.add_argument(
        "--repo-root", default=None,
        help="Optional repo root (defaults to current working directory).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    proposed = Path(args.path)
    parent = proposed.parent
    if args.repo_root and not parent.is_absolute():
        parent = (Path(args.repo_root) / parent).resolve()
    if not parent.exists():
        print(
            f"ERROR: parent directory does not exist: {parent}",
            file=sys.stderr,
        )
        return 2

    result = check_path(proposed, repo_root=args.repo_root)
    if args.json:
        import json
        print(json.dumps(result, indent=2))
    else:
        print(_format_human(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
