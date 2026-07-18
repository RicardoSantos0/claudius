"""Fresh-install smoke tests — the bare minimum that proves a clean install works.

This is the ONLY test module shipped in the public `claudius` repo (the full
internal suite stays in the private working repo). It must be self-contained:
no fixtures from the rest of the suite, no network, no pre-existing project or
DB state. It exercises the release-criteria CLI flow (init -> status -> prompt)
plus package import and `mas doctor`.
"""

import pytest
from click.testing import CliRunner

from core.cli import main

SMOKE_ID = "proj-29990101-001-smoke"


@pytest.fixture()
def runner():
    return CliRunner()


def test_package_imports():
    """The installed package imports cleanly via its runtime root (`core.*`)."""
    import core.cli  # noqa: F401
    import core.config  # noqa: F401
    from core.engine.execution_profile_router import ExecutionProfileRouter  # noqa: F401
    from core.engine.model_canary import run_provider_canary  # noqa: F401
    from core.engine.route_telemetry import RouteTelemetryStore  # noqa: F401
    from core.engine.shared_state_manager import SharedStateManager  # noqa: F401


def test_cli_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0, result.output


def test_cli_help_lists_core_commands(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "init",
        "status",
        "prompt",
        "doctor",
        "model-catalogs",
        "model-canary",
        "route-metrics",
    ):
        assert cmd in result.output


def test_public_routing_config_and_agent_metadata_are_provider_neutral():
    """The curated public release has live-date catalogs and unpinned agents."""
    from pathlib import Path

    import tomllib

    import yaml

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "mas" / "system_config.yaml").read_text("utf-8"))
    catalogs = config["llm"]["provider_catalogs"]
    assert {"anthropic", "openai", "gemini"} <= set(catalogs)
    assert "as_of_date" not in config["llm"]["routing"]["catalog_lifecycle"]

    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    extras = project["optional-dependencies"]
    assert {"openai", "litellm", "providers"} <= set(extras)

    agent_files = [path for path in (root / "agents").glob("*.md") if path.name != "_utilities.md"]
    assert len(agent_files) == 16
    for path in agent_files:
        text = path.read_text("utf-8")
        assert "model: inherit" in text, path
        assert "model_profile: auto" in text, path


def test_doctor_runs():
    """`mas doctor` runs via the real CLI entrypoint (subprocess — how users run it)
    and reports, exiting cleanly or with actionable guidance."""
    import sys
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "core.cli", "doctor"],
        capture_output=True, text=True, timeout=120,
    )
    combined = result.stdout + result.stderr
    assert "MAS Doctor" in combined, f"rc={result.returncode}\n{combined}"
    assert result.returncode in (0, 1)


def test_init_workspace_scaffolds_usable_layout(runner, tmp_path):
    """`mas init-workspace --path` builds a workspace mirroring the source layout.

    This is the install-path command: a pip-installed wheel copies the bundled
    framework files into a workspace. Here (source tree / editable) it copies from
    the repo, but the resulting layout + runtime dirs must be identical.
    """
    ws = tmp_path / "ws"
    result = runner.invoke(main, ["init-workspace", "--path", str(ws)])
    assert result.exit_code == 0, result.output
    assert (ws / "mas" / "system_config.yaml").exists()
    assert (ws / "agents").is_dir()
    assert (ws / "mas" / "roster").is_dir()
    for d in ("projects", "data", "logs", "working_state"):
        assert (ws / "mas" / d).is_dir(), f"runtime dir missing: {d}"


def test_init_status_prompt_roundtrip(runner, tmp_path, monkeypatch):
    """A fresh `mas init` -> `status` -> `prompt` lifecycle works in an isolated dir."""
    import core.engine.shared_state_manager as ssm

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    # Point BOTH path layers at the tmp dir so `init` and the read-back commands agree:
    #   - core.config.get_projects_dir() — used by the CLI to place new projects
    #   - SharedStateManager.ROOT        — used to load existing project state
    monkeypatch.setattr("core.config.get_projects_dir", lambda: projects_dir)
    monkeypatch.setattr(ssm, "ROOT", tmp_path)
    # Isolate from the local episodic.db — event recording is non-essential here.
    monkeypatch.setattr(
        "core.engine.event_recorder.EventRecorder.record_simple",
        lambda *a, **k: None,
    )

    init = runner.invoke(main, ["init", SMOKE_ID])
    assert init.exit_code == 0, init.output
    assert (projects_dir / SMOKE_ID / "shared_state.yaml").exists()

    status = runner.invoke(main, ["status", SMOKE_ID])
    assert status.exit_code == 0, status.output
    assert SMOKE_ID in status.output

    with pytest.warns(RuntimeWarning, match="route audit was not persisted"):
        prompt = runner.invoke(main, ["prompt", SMOKE_ID])
    assert prompt.exit_code == 0, prompt.output
    assert prompt.output.strip()
