#!/usr/bin/env python3
"""
link_skill_surfaces.py - Point every installed client at the canonical skills.

``skills/`` in this repo is the only copy of each skill. Every client discovers
skills in its own place, so each one gets a pointer to that copy, never a copy
of its own:

    claude       ~/.claude/skills/<name>        one link per skill; opencode and VS Code
                                                Copilot read this folder too
    copilot      ~/.copilot/skills/<name>       one link per skill, for the Copilot CLI
    codex        ~/.codex/skills/<name>         one link per skill
    antigravity  ~/.gemini/config/skills.json   one manifest entry naming skills/

The discovery paths were read from the installed clients' code (Copilot CLI
1.0.45, VS Code 1.140, opencode 1.17.11, Codex 26.930, agy), not from vendor
docs, because two of them contradict their own docs: the Copilot CLI's help
lists ~/.claude/skills but its loader never reads it, and agy documents a "~/"
manifest path that it then rejects. VS Code Copilot reads both ~/.claude/skills
and ~/.copilot/skills and keeps the first skill of each name, so the overlap
lists nothing twice.

Links are junctions on Windows, which need no admin rights, and symlinks
elsewhere. A real directory carrying a canonical skill's name is a stale copy
that shadows the canonical skill, so --write moves it under
~/.skill-copies-backup/<timestamp>/<client>/ before linking. Entries the repo
does not own (account-synced skills, personal skills) are never touched, and a
link that points somewhere else is reported rather than replaced.

Usage:
    python scripts/link_skill_surfaces.py              # link every available client
    python scripts/link_skill_surfaces.py --check      # report drift, write nothing
    python scripts/link_skill_surfaces.py --only codex

Exit codes:
    0 - every client points at every skill (or was linked)
    1 - drift found under --check, a conflict needs a decision, or a write failed
    2 - usage error, or the repo has no skills/ folder
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

CLIENTS = ("claude", "copilot", "codex", "antigravity")

# Client -> (home marker that proves the client is installed, per-skill link folder).
LINK_DIRS = {
    "claude": (".claude", (".claude", "skills")),
    "copilot": (".copilot", (".copilot", "skills")),
    "codex": (".codex", (".codex", "skills")),
}
ANTIGRAVITY_CONFIG = (".gemini", "config")


def canonical_skills(repo: Path) -> dict[str, Path]:
    """Top-level skill folders, the ones every client scans one level deep."""
    root = repo / "skills"
    return {d.name: d for d in sorted(root.iterdir())
            if d.is_dir() and (d / "SKILL.md").is_file()}


def _is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(p)
    # Python < 3.12 has no isjunction and is_symlink() is False for a junction,
    # so read the reparse-point attribute directly.
    try:
        attrs = getattr(os.lstat(p), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _inside(p: Path, root: Path) -> bool:
    real = os.path.normcase(os.path.realpath(p))
    base = os.path.normcase(os.path.realpath(root))
    return real == base or real.startswith(base + os.sep)


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target.resolve()), str(link))
    else:
        os.symlink(target.resolve(), link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    # rmdir removes a junction or directory symlink on Windows without touching
    # its target; on POSIX a symlink is unlinked.
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


class Report:
    def __init__(self) -> None:
        self.drift = 0
        self.conflicts = 0
        self.failures = 0

    def line(self, tag: str, client: str, text: str) -> None:
        print(f"[{tag}] {client}: {text}")


def link_folder(client: str, folder: Path, skills: dict[str, Path], repo: Path,
                write: bool, backup_root: Path, rep: Report) -> None:
    if _is_link(folder) and _same(folder, repo / "skills"):
        rep.line("ok", client, f"{folder} is itself a link to skills/, so it holds all {len(skills)}")
        return
    if write:
        folder.mkdir(parents=True, exist_ok=True)
    linked = 0
    missing: list[str] = []
    for name, src in skills.items():
        p = folder / name
        if _is_link(p):
            if _same(p, src):
                linked += 1
                continue
            if os.path.exists(p):
                rep.conflicts += 1
                rep.line("conflict", client, f"{p} links to {os.path.realpath(p)}, not skills/{name}; left alone")
                continue
            # A dangling link with a canonical name: repoint it.
            rep.drift += 1
            if not write:
                rep.line("dangling", client, f"{p}")
                continue
            _remove_link(p)
        elif p.is_dir():
            rep.drift += 1
            if not write:
                rep.line("copy", client, f"{p} is a copy that shadows skills/{name}")
                continue
            dest = backup_root / client / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            rep.line("moved", client, f"copy {p} -> {dest}")
        elif p.exists():
            rep.conflicts += 1
            rep.line("conflict", client, f"{p} is a file; left alone")
            continue
        else:
            rep.drift += 1
            if not write:
                missing.append(name)
                continue
        try:
            _make_link(p, src)
            linked += 1
        except OSError as exc:
            rep.failures += 1
            rep.line("failed", client, f"{p} -> {src}: {exc}")

    # A link into this repo whose skill no longer exists would surface a dead skill.
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.name in skills or not _is_link(p):
                continue
            target = Path(os.path.realpath(p))
            if not (_inside(target, repo / "skills") or _inside(target, repo / ".agents" / "skills")):
                continue
            if (target / "SKILL.md").is_file():
                # Points at a live skill under a different name; leave it.
                continue
            rep.drift += 1
            if write:
                _remove_link(p)
                rep.line("pruned", client, f"{p} pointed at a skill the repo no longer has")
            else:
                rep.line("orphan", client, f"{p} points at a skill the repo no longer has")
    if missing:
        rep.line("missing", client, f"{len(missing)} unlinked: {', '.join(missing)}")
    rep.line("ok" if linked == len(skills) else "partial", client,
             f"{linked}/{len(skills)} skills linked in {folder}")


def manifest_path_value(repo: Path) -> str:
    # Absolute, because the installed agy rejects the "~/" form its own docs
    # describe ("must be an absolute path") and then loads none of the skills.
    return (repo / "skills").resolve().as_posix()


def _expand(value: str, home: Path) -> Path:
    return home / value[2:] if value.startswith("~/") else Path(value)


def link_antigravity(config_dir: Path, repo: Path, home: Path, write: bool, rep: Report) -> None:
    manifest = config_dir / "skills.json"
    value = manifest_path_value(repo)
    data: dict = {}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            rep.conflicts += 1
            rep.line("conflict", "antigravity", f"{manifest} is not valid JSON ({exc}); left alone")
            return
    entries = data.setdefault("entries", [])
    ours = [e for e in entries if isinstance(e, dict)
            and _same(_expand(str(e.get("path", "")), home), repo / "skills")]
    if any(e.get("path") == value for e in ours):
        rep.line("ok", "antigravity", f"{manifest} names {value}")
        return
    rep.drift += 1
    if not write:
        what = "names skills/ in a form agy rejects" if ours else "has no entry for"
        rep.line("missing", "antigravity", f"{manifest} {what} {value}")
        return
    if ours:
        for e in ours:
            e["path"] = value
    else:
        entries.append({"path": value})
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rep.line("linked", "antigravity", f"{manifest} now names {value}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="report drift, write nothing")
    ap.add_argument("--only", action="append", choices=CLIENTS, help="limit to these clients")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--home", type=Path, default=Path.home(),
                    help="home folder whose client folders get the links (default: yours)")
    ns = ap.parse_args(argv)

    repo, home, write = ns.repo_root, ns.home, not ns.check
    if not (repo / "skills").is_dir():
        print(f"[error] no skills/ folder under {repo}", file=sys.stderr)
        return 2
    skills = canonical_skills(repo)
    backup_root = home / ".skill-copies-backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    rep = Report()

    for client in ns.only or CLIENTS:
        if client in LINK_DIRS:
            marker, parts = LINK_DIRS[client]
            if not (home / marker).is_dir():
                rep.line("skip", client, f"{home / marker} not found, client not installed")
                continue
            link_folder(client, home.joinpath(*parts), skills, repo, write, backup_root, rep)
        else:
            config_dir = home.joinpath(*ANTIGRAVITY_CONFIG)
            if not config_dir.is_dir():
                rep.line("skip", client, f"{config_dir} not found, client not installed")
                continue
            link_antigravity(config_dir, repo, home, write, rep)

    if rep.conflicts or rep.failures:
        return 1
    return 1 if (rep.drift and not write) else 0


if __name__ == "__main__":
    sys.exit(main())
