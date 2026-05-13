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
    assert "ghcr.io/home-console/core-runtime" in r.output


def test_deploy_config_set_rejects_bad_core_mode(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["deploy", "config", "set", "--core-mode", "legacy-image-alias"])
    assert r.exit_code == 2
    assert "--core-mode" in r.output or "недопустим" in r.output.lower() or "допустим" in r.output.lower()


def test_deploy_config_set_accepts_prod_core_mode(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["deploy", "config", "set", "--core-mode", "prod"])
    assert r.exit_code == 0


def test_deploy_config_set_core_mode_image_alias(runner: CliRunner, isolated_home) -> None:
    from hc.main import app

    r = runner.invoke(app, ["deploy", "config", "set", "--core-mode", "image"])
    assert r.exit_code == 0
    r2 = runner.invoke(app, ["deploy", "config", "show"])
    assert r2.exit_code == 0
    assert "dev-image" in r2.output


def test_deploy_config_edit_opens_editor(runner: CliRunner, isolated_home, monkeypatch) -> None:
    import subprocess

    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw):  # noqa: ANN003
        recorded.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr("hc.commands.deploy.subprocess.run", fake_run)
    from hc.main import app
    import hc.constants as constants

    r = runner.invoke(app, ["deploy", "config", "edit"])
    assert r.exit_code == 0
    assert recorded
    assert recorded[0][0] == "true"
    assert str(constants.CONFIG_PATH) in recorded[0]


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
            "not-a-valid-mode",
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
