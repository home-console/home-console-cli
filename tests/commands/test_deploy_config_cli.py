from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_deploy_config_show_defaults(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["deploy", "config", "show"])
    assert r.exit_code == 0
    assert "deploy.core_image" in r.output
    assert "dev-core-runtime" in r.output


def test_deploy_config_set_rejects_bad_core_mode(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["deploy", "config", "set", "--core-mode", "prod"])
    assert r.exit_code == 2
    assert "--core-mode" in r.output or "dev" in r.output


def test_deploy_config_set_roundtrip(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(
        app,
        [
            "deploy",
            "config",
            "set",
            "--core-image",
            "ghcr.io/x/core",
            "--core-mode",
            "dev",
            "--ssh",
            "u@h",
            "--path",
            "/srv/core",
        ],
    )
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["deploy", "config", "show"])
    assert r2.exit_code == 0
    assert "ghcr.io/x/core" in r2.output
    assert "/srv/core" in r2.output


def test_deploy_root_json_invalid_mode(runner: CliRunner, isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    from hc.main import app

    r = runner.invoke(
        app,
        [
            "deploy",
            "--json",
            "--mode",
            "prod",
            "--no-build",
            "--no-push",
            "--no-rollout",
        ],
    )
    assert r.exit_code == 2
    line = r.stdout.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["ok"] is False
    assert data["error"] == "InvalidModeError"
    assert data["exit_code"] == 2
