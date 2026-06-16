"""Runtime path resolution for both source-tree and installed (pip) usage.

The framework anchors every runtime file to one of two roots:

  * ``repo_root()`` — holds ``agents/`` and ``skills/`` (and ``.env`` in a clone).
  * ``mas_root()``  — the ``mas/`` tree: ``system_config.yaml``, ``policies/``,
    ``templates/``, ``roster/``, ``foundation/``, ``domains/`` and the writable
    ``projects/``, ``data/``, ``logs/``, ``working_state/``.

Historically every module computed these with ``Path(__file__).parent.parent...``.
That only works when the code is run from a git clone, because the data files are
siblings of the package directory. This module centralises the calculation so the
same code also works when installed as a wheel.

Two modes
---------
**Source-tree mode** — running from a clone (or an editable ``pip install -e .``).
``mas/core/`` sits inside the repo, so ``mas/system_config.yaml`` is a sibling of
the package dir and ``agents/`` lives at the repo root. Both roots resolve to the
real repo, exactly as before — behaviour-preserving, no regression.

**Installed mode** — a non-editable wheel in ``site-packages``. The data files are
not siblings of the package, so the framework uses a writable *workspace*
(``$MAS_HOME``, default ``~/.mas``) that mirrors the source-tree layout. The wheel
ships the read-only framework files as bundled package data under
``core/_bundled/``; ``mas init-workspace`` copies them into the workspace so every
downstream ``mas_root()/x`` / ``repo_root()/agents`` path resolves normally.
"""

from __future__ import annotations

import os
from pathlib import Path

# Package directory (.../core) and its parent. In a clone the parent is mas/;
# in an installed wheel it is site-packages/ (or wherever the wheel landed).
_PKG_DIR = Path(__file__).resolve().parent
_PKG_PARENT = _PKG_DIR.parent
_REPO_ROOT_CANDIDATE = _PKG_PARENT.parent

#: Name of the bundled-assets directory shipped inside the wheel.
BUNDLED_DIRNAME = "_bundled"

#: Default workspace location when installed and ``$MAS_HOME`` is unset.
DEFAULT_WORKSPACE = Path.home() / ".mas"


def _is_source_tree() -> bool:
    """True when running from a clone / editable install.

    In that layout the package lives at ``<repo>/mas/core``, so ``mas/`` holds
    ``system_config.yaml`` and the repo root holds ``agents/``. Both must be
    present to avoid mistaking an unrelated ``site-packages`` parent for a repo.
    """
    return (_PKG_PARENT / "system_config.yaml").exists() and (
        _REPO_ROOT_CANDIDATE / "agents"
    ).is_dir()


def bundled_dir() -> Path:
    """Read-only framework assets shipped inside the wheel.

    Only ``mas init-workspace`` reads these — the running engine always sees a
    real on-disk layout (the clone, or the initialised workspace).
    """
    return _PKG_DIR / BUNDLED_DIRNAME


def workspace_root() -> Path:
    """The writable workspace root used in installed mode."""
    env = os.environ.get("MAS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_WORKSPACE


def repo_root() -> Path:
    """Directory that holds ``agents/`` and ``skills/`` (and ``.env``)."""
    if _is_source_tree():
        return _REPO_ROOT_CANDIDATE
    return workspace_root()


def mas_root() -> Path:
    """The ``mas/`` tree (config, policies, templates, roster, projects, data...)."""
    if _is_source_tree():
        return _PKG_PARENT
    return workspace_root() / "mas"


def is_installed() -> bool:
    """True when running from an installed wheel rather than a clone."""
    return not _is_source_tree()


def is_workspace_initialized() -> bool:
    """True if the framework can find its read-only data where it expects it."""
    return (mas_root() / "system_config.yaml").exists()
