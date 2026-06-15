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
    from core.engine.shared_state_manager import SharedStateManager  # noqa: F401


def test_cli_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0, result.output


def test_cli_help_lists_core_commands(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    for cmd in ("init", "status", "prompt", "doctor"):
        assert cmd in result.output


def test_doctor_runs(runner):
    """`mas doctor` produces its report and exits cleanly or with actionable guidance."""
    result = runner.invoke(main, ["doctor"])
    assert "MAS Doctor" in result.output, result.output
    assert result.exit_code in (0, 1)


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

    prompt = runner.invoke(main, ["prompt", SMOKE_ID])
    assert prompt.exit_code == 0, prompt.output
    assert prompt.output.strip()
