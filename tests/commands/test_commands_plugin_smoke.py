from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_plugin_list(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def inspector_plugins(self):  # noqa: ANN001
            return {
                "ok": True,
                "result": [
                    {"name": "p1", "version": "1", "status": "running", "mode": "x", "uptime": "1s"}
                ],
            }

    monkeypatch.setattr("hc.commands.plugin.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["plugin", "list"])
    assert result.exit_code == 0
    assert "p1" in result.output


def test_plugin_start_stop_restart(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        def __init__(self) -> None:
            self.stops = 0
            self.starts = 0

        async def stop_plugin(self, name: str):  # noqa: ANN001
            self.stops += 1
            return {"ok": True, "name": name}

        async def start_plugin(self, name: str):  # noqa: ANN001
            self.starts += 1
            return {"ok": True, "name": name}

    c = _Client()
    monkeypatch.setattr("hc.commands.plugin.require_client", lambda console: c)
    from hc.main import app

    assert runner.invoke(app, ["plugin", "stop", "demo"]).exit_code == 0
    assert runner.invoke(app, ["plugin", "start", "demo"]).exit_code == 0
    assert runner.invoke(app, ["plugin", "restart", "demo"]).exit_code == 0
    assert c.stops == 2
    assert c.starts == 2


def test_plugin_info(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_plugin_info(self, name: str):  # noqa: ANN001
            return {
                "name": name,
                "version": "1",
                "status": "running",
                "mode": "x",
                "description": "d",
            }

    monkeypatch.setattr("hc.commands.plugin.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["plugin", "info", "demo"])
    assert result.exit_code == 0
    assert "demo" in result.output
