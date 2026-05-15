from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_deploy_stack_accepts_env_argument_dev(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    compose_file = tmp_path / "deploy" / "dev" / "docker-compose.image.yml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr(
        "hc.commands.deploy._resolve_source",
        lambda _console: type("S", (), {"path": tmp_path})(),
    )
    monkeypatch.setattr("hc.commands.deploy._run_env", lambda *args, **kwargs: None)
    monkeypatch.setattr("hc.commands.deploy._step_start", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr("hc.commands.deploy._step_ok", lambda *_args, **_kwargs: 0.0)

    from hc.main import app

    r = runner.invoke(app, ["deploy", "stack", "dev", "--no-pull", "--no-wait", "--quiet"])
    assert r.exit_code == 0, r.output


def test_deploy_stack_rejects_bad_env(
    runner: CliRunner, isolated_home, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("hc.commands.deploy.require_docker", lambda _console: None)
    monkeypatch.setattr(
        "hc.commands.deploy._resolve_source",
        lambda _console: type("S", (), {"path": tmp_path})(),
    )

    from hc.main import app

    r = runner.invoke(app, ["deploy", "stack", "stage"])
    assert r.exit_code == 2, r.output
    assert "допустимые: dev | prod" in r.output.lower()
