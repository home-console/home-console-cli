from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_install_fails_when_marketplace_index_empty(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    from hc.main import app

    assert runner.invoke(app, ["install", "p1"]).exit_code == 1


def test_install_fails_when_plugin_missing(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return [{"name": "other", "version": "1"}]

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["install", "p1"])
    assert res.exit_code == 1
    assert "не найден" in res.output.lower()


def test_install_fails_when_version_missing(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return [{"name": "p1", "version": "1.0"}]

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["install", "p1", "--version", "9.9"])
    assert res.exit_code == 1
    assert "версия" in res.output.lower()
