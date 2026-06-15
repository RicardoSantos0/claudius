#!/usr/bin/env python3
"""
check_archive_clean.py — Verify an archive contains no private/generated paths.

Usage:
    python scripts/check_archive_clean.py <archive.zip|archive.tar>

Exit codes:
    0  — archive contains no blocked private/generated paths
    1  — archive contains one or more blocked paths (error details printed)
    2  — usage error or archive cannot be opened

Blocked path patterns (any archive member matching these is a violation):
    .env
    .env.*
    .claude/settings.local.json
    .git/
    .venv/
    __pycache__/
    *.pyc
    *.pyo
    mas/data/
    mas/projects/
    mas/logs/
    mas/working_state/
    skills/notebooklm/data/browser_state/
    skills/notebooklm/data/auth_info.json
    skills/notebooklm/.venv/
    *.sqlite
    *.sqlite3
    *.db
    *.log
    *.tmp
    *.tmp.*
    *_payload.json
    *.payload.json
    .scribe_prompt.tmp.txt
    *AppDataLocalTemp*
    *UsersricarAppDataLocalTemp*
    secrets/
    logs/
"""

import sys
import tarfile
import zipfile
import fnmatch
from pathlib import Path


BLOCKED_PATTERNS = [
    ".env",
    ".env.*",
    ".git",
    ".git/*",
    ".claude/settings.local.json",
    ".venv",
    ".venv/*",
    "__pycache__/*",
    "*.pyc",
    "*.pyo",
    "mas/data/*",
    "mas/projects/*",
    "mas/logs/*",
    "mas/working_state/*",
    "skills/notebooklm/data/browser_state/*",
    "skills/notebooklm/data/auth_info.json",
    "skills/notebooklm/.venv",
    "skills/notebooklm/.venv/*",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.log",
    "*.tmp",
    "*.tmp.*",
    "*_payload.json",
    "*.payload.json",
    ".scribe_prompt.tmp.txt",
    "*AppDataLocalTemp*",
    "*UsersricarAppDataLocalTemp*",
    "secrets/*",
    "logs/*",
]


def _normalise_member_name(name: str) -> str:
    """Normalise archive member names for cross-platform matching."""
    name = name.replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _candidate_names(name: str) -> list[str]:
    """Return the raw member path plus suffixes without archive root prefixes."""
    normalised = _normalise_member_name(name)
    parts = [part for part in normalised.split("/") if part and part != "."]
    candidates = [normalised]
    for i in range(1, len(parts)):
        candidates.append("/".join(parts[i:]))
    return list(dict.fromkeys(candidates))


def _matches_pattern(name: str, pattern: str) -> bool:
    if fnmatch.fnmatch(name, pattern):
        return True
    # Also match if the name starts with a blocked prefix directory.
    prefix = pattern.rstrip("*").rstrip("/")
    return bool(prefix and (name == prefix or name.startswith(prefix + "/")))


def is_blocked(name: str) -> bool:
    """Return True if the archive member name matches any blocked pattern."""
    for candidate in _candidate_names(name):
        for pattern in BLOCKED_PATTERNS:
            if _matches_pattern(candidate, pattern):
                return True
    return False


def _archive_names(path: str) -> list[str]:
    archive_path = Path(path)
    if zipfile.is_zipfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                return zf.namelist()
        except zipfile.BadZipFile as exc:
            print(f"ERROR: cannot open ZIP archive: {exc}", file=sys.stderr)
            sys.exit(2)

    if tarfile.is_tarfile(archive_path):
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                return tf.getnames()
        except tarfile.TarError as exc:
            print(f"ERROR: cannot open TAR archive: {exc}", file=sys.stderr)
            sys.exit(2)

    print(f"ERROR: unsupported archive format: {path}", file=sys.stderr)
    sys.exit(2)


def check_archive(path: str) -> list[str]:
    """Return list of blocked paths found in the archive."""
    names = _archive_names(path)
    return [n for n in names if is_blocked(n)]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/check_archive_clean.py <archive.zip|archive.tar>", file=sys.stderr)
        sys.exit(2)

    archive_path = sys.argv[1]
    if not Path(archive_path).exists():
        print(f"ERROR: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(2)

    violations = check_archive(archive_path)

    if violations:
        print("ERROR: archive contains blocked paths:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    else:
        print("OK: archive contains no blocked private/generated paths.")
        sys.exit(0)


if __name__ == "__main__":
    main()
