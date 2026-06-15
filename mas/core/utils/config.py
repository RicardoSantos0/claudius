"""
System configuration loader (utils copy)

Copied into `core.utils`. Adjusted path calculations so `ROOT`
resolves correctly when the module lives under `mas/core/utils/`.
"""

import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

# mas/ root when this file is located at mas/core/utils/config.py
ROOT = Path(__file__).parents[2]
REPO_ROOT = ROOT.parent


def _find_root() -> Path:
    """Find system root by locating system_config.yaml."""
    candidate = Path(__file__).parents[2]
    if (candidate / "system_config.yaml").exists():
        return candidate
    raise FileNotFoundError(f"system_config.yaml not found under {candidate}")


def load_config() -> dict:
    """Load and return the full system configuration."""
    load_dotenv(REPO_ROOT / ".env")
    config_path = ROOT / "system_config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_master_model() -> str:
    config = load_config()
    return os.getenv("MAS_MASTER_MODEL", config["llm"]["master_model"])


def get_default_model() -> str:
    config = load_config()
    return os.getenv("MAS_DEFAULT_MODEL", config["llm"]["default_model"])


def get_model_for_agent(agent_id: str) -> str:
    """Return the appropriate model for a given agent."""
    if agent_id == "master_orchestrator":
        return get_master_model()
    return get_default_model()


def get_api_key() -> str:
    load_dotenv(REPO_ROOT / ".env")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return key


def get_projects_dir() -> Path:
    config = load_config()
    return ROOT / config["paths"]["projects"]


# Project IDs look like proj-YYYYMMDD-NNN-slug (or short test ids like proj-gov-001).
_PROJECT_ID_PREFIX = "proj-"


def resolve_project_dir(project_id: str, projects_root: Path | None = None) -> Path:
    """Resolve a project_id to its on-disk directory, layout-agnostic.

    Supports both the legacy flat layout (`projects/<id>/`) and the family-nested
    layout (`projects/<family>/<id>/`). Resolution order:
      1. flat: projects_root/<id>            (back-compat; also what `init` creates)
      2. nested: projects_root/<family>/<id> (one level deep)
    If the same project_id exists in more than one location (a split-brain — e.g. an
    artifact written before `mas init` created an empty sibling), prefer the location
    that actually holds a `shared_state.yaml` and warn about the duplicate, instead of
    silently returning the first (possibly empty) match (ip-par-001). Falls back to the
    flat path (which may not yet exist) so callers that create new projects still work.
    """
    root = projects_root or get_projects_dir()

    # collect every on-disk location for this project_id (flat + one level deep)
    matches: list[Path] = []
    flat = root / project_id
    if flat.is_dir():
        matches.append(flat)
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith(_PROJECT_ID_PREFIX):
                candidate = child / project_id
                if candidate.is_dir():
                    matches.append(candidate)

    if not matches:
        return flat  # not-yet-created project → flat path

    if len(matches) == 1:
        return matches[0]

    # duplicate: prefer the one with a shared_state.yaml; warn on the split-brain.
    with_state = [m for m in matches if (m / "shared_state.yaml").exists()]
    chosen = with_state[0] if with_state else matches[0]
    import warnings
    warnings.warn(
        f"Project '{project_id}' exists in {len(matches)} locations "
        f"{[str(m) for m in matches]}; using {chosen}. "
        f"Remove the stale duplicate(s) (likely artifacts written before `mas init`).",
        RuntimeWarning, stacklevel=2,
    )
    return chosen


def iter_project_dirs(projects_root: Path | None = None):
    """Yield every project directory, whether flat or nested in a family folder.

    A directory is a project iff its name starts with 'proj-'. Family subfolders
    (names without the proj- prefix) are descended into one level.
    """
    root = projects_root or get_projects_dir()
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(_PROJECT_ID_PREFIX):
            yield child                      # flat project
        else:
            for sub in sorted(child.iterdir()):  # family folder → nested projects
                if sub.is_dir() and sub.name.startswith(_PROJECT_ID_PREFIX):
                    yield sub


def get_governance_mode() -> str:
    config = load_config()
    return os.getenv("MAS_GOVERNANCE_MODE", config["system"]["governance_mode"])


def get_defaults() -> dict:
    return load_config()["defaults"]
