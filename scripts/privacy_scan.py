#!/usr/bin/env python3
"""privacy_scan.py — scan a source tree's file CONTENTS for personal/private data.

Companion to ``check_archive_clean.py`` (which checks archive *path names*). This
tool scans the *contents* of text files for material that must not reach the
public ``claudius`` repo. It is the content-level gate for the M4 cut-over and is
also runnable against any candidate tree.

Detected categories:
    home_path     user-home paths (C:\\Users\\<user>, /Users/<user>, /home/<user>)
    email         email addresses (placeholders are allow-listed)
    api_key       Anthropic / AWS / generic secret-looking key material
    project_id    MAS private project IDs (proj-YYYYMMDD-NNN-...)
    private_ref   private-context keywords (NOVA, PhD, OneDrive, Notion, Zotero)
    owner_ident   known owner identifiers (configurable)

Note: run this against the *curated public tree*, not the raw private repo — the
private repo legitimately contains excluded material (mas/projects, research-*
skills, changelogs) that is removed during curation.

Usage:
    python scripts/privacy_scan.py [PATH] [--allow FILE] [--quiet]

Exit codes:
    0 — no findings
    1 — one or more findings
    2 — usage / IO error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Known owner identifiers — high-confidence private strings. Adjust as needed.
OWNER_IDENTIFIERS = [
    r"ricardomcsantos0",
    r"RicardoSantos0",
    r"Users[\\/]ricar\b",
    r"\bricar\b",
]

PATTERNS: dict[str, re.Pattern[str]] = {
    "home_path": re.compile(r"""[A-Za-z]:[\\/]Users[\\/][^\\/\s"']+|/(?:Users|home)/[^/\s"']+"""),
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "api_key": re.compile(
        r"sk-ant-[A-Za-z0-9_\-]{8,}"          # Anthropic keys
        r"|sk-[A-Za-z0-9]{20,}"               # generic OpenAI-style
        r"|AKIA[0-9A-Z]{16}"                  # AWS access key id
        r"|ANTHROPIC_API_KEY\s*[=:]\s*['\"]?sk-"  # assigned anthropic key value
    ),
    "project_id": re.compile(r"proj-\d{8}-\d{3}[a-z0-9\-]*"),
    "private_ref": re.compile(r"\b(NOVA|OneDrive|Zotero|PhD|Notion)\b", re.IGNORECASE),
    "owner_ident": re.compile("|".join(OWNER_IDENTIFIERS)),
}

# Email/value substrings that are legitimate placeholders, not leaks.
EMAIL_ALLOW = re.compile(r"@(example\.(com|org|net)|test|localhost)|noreply@|you@|user@")

# Directories never scanned (runtime/build/vendored/private state).
IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".stfolder", ".mypy_cache", ".ruff_cache",
}
# Path fragments (posix-style) whose subtree is skipped.
IGNORE_SUBTREES = (
    "mas/data", "mas/projects", "mas/logs", "mas/working_state",
)
# Binary / non-text extensions skipped.
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".whl", ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".lock",
    ".woff", ".woff2", ".ttf", ".mp4", ".mov",
}


def _load_allow(path: str | None) -> list[re.Pattern[str]]:
    if not path:
        return []
    pats = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append(re.compile(line))
    return pats


def _should_skip(rel: str) -> bool:
    posix = rel.replace("\\", "/")
    parts = set(posix.split("/"))
    if parts & IGNORE_DIRS:
        return True
    if any(posix == s or posix.startswith(s + "/") for s in IGNORE_SUBTREES):
        return True
    return False


def scan_tree(root: Path, allow: list[re.Pattern[str]], self_path: Path) -> list[tuple]:
    """Return findings as (relpath, lineno, category, snippet)."""
    findings: list[tuple] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if _should_skip(rel) or path.suffix.lower() in BINARY_EXT:
            continue
        # Never flag the scanner or its allowlist on their own pattern literals.
        if path.resolve() == self_path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for category, pat in PATTERNS.items():
                m = pat.search(line)
                if not m:
                    continue
                hit = m.group(0)
                if category == "email" and EMAIL_ALLOW.search(line):
                    continue
                if any(a.search(line) for a in allow):
                    continue
                findings.append((rel, lineno, category, hit))
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan file contents for private data.")
    ap.add_argument("path", nargs="?", default=".", help="tree to scan (default: .)")
    ap.add_argument("--allow", default=None, help="file of regexes to allow-list")
    ap.add_argument("--quiet", action="store_true", help="only print the summary")
    args = ap.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"ERROR: path not found: {root}", file=sys.stderr)
        sys.exit(2)

    allow = _load_allow(args.allow)
    self_path = Path(__file__).resolve()
    findings = scan_tree(root, allow, self_path)

    if findings:
        if not args.quiet:
            print("PRIVACY SCAN: findings (must be resolved before public release):")
            for rel, lineno, category, hit in findings:
                print(f"  [{category}] {rel}:{lineno}: {hit}")
        print(f"FAIL: {len(findings)} finding(s) across the scanned tree.")
        sys.exit(1)
    print(f"OK: no private data found in {root}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
