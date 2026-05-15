from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hc.core_source import CoreSource


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_deploy_dev_up_local_profile_services(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], Path | None]] = []

    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr("hc.commands.deploy._resolve_source", lambda _console: CoreSource(path=tmp_path))
    monkeypatch.setattr(
        "hc.commands.deploy.compose_project_from_source",
        lambda _console, _src, mode="dev": type("P", (), {"compose_file": tmp_path / "deploy/dev/docker-compose.yml", "cwd": tmp_path})(),
    )
    monkeypatch.setattr("hc.commands.deploy._run", lambda cmd, cwd=None: calls.append((cmd, cwd)))

    from hc.main import app

    r = runner.invoke(app, ["deploy", "dev", "up", "--profile", "core+proxy+platform+cache"])

    assert r.exit_code == 0, r.output
    assert len(calls) == 1
    assert calls[0][0][-4:] == ["core-runtime", "caddy", "platform-web", "redis"]
    assert calls[0][1] == tmp_path


def test_deploy_dev_up_alias_profile_db(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], Path | None]] = []

    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr("hc.commands.deploy._resolve_source", lambda _console: CoreSource(path=tmp_path))
    monkeypatch.setattr(
        "hc.commands.deploy.compose_project_from_source",
        lambda _console, _src, mode="dev": type("P", (), {"compose_file": tmp_path / "deploy/dev/docker-compose.yml", "cwd": tmp_path})(),
    )
    monkeypatch.setattr("hc.commands.deploy._run", lambda cmd, cwd=None: calls.append((cmd, cwd)))

    from hc.main import app

    r = runner.invoke(app, ["deploy", "dev", "up", "--profile", "db"])

    assert r.exit_code == 0, r.output
    assert len(calls) == 1
    assert calls[0][0][-5:] == ["core-runtime", "caddy", "platform-web", "redis", "postgres"]


def test_deploy_dev_up_rejects_bad_profile(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr("hc.commands.deploy._resolve_source", lambda _console: CoreSource(path=tmp_path))

    from hc.main import app

    r = runner.invoke(app, ["deploy", "dev", "up", "--profile", "nope"])

    assert r.exit_code == 2, r.output
    assert "--profile 'nope' недопустим." in r.output
