from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_status_happy_path(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def admin_status(self):  # noqa: ANN001
            return None

        async def health(self):  # noqa: ANN001
            return {"status": "ok", "version": "9.9.9", "uptime": "1s"}

        async def get_plugins(self):  # noqa: ANN001
            return [{"name": "p", "status": "running"}]

        async def get_modules(self):  # noqa: ANN001
            return [{"name": "m", "status": "running"}]

    monkeypatch.setattr("hc.commands.status.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "9.9.9" in result.output


def test_search_happy_path(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def search_marketplace(self, query: str):  # noqa: ANN001
            assert query == "foo"
            return [{"name": "p", "version": "1", "description": "d", "author": "a", "downloads": 1}]

    monkeypatch.setattr("hc.commands.search.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["search", "foo"])
    assert result.exit_code == 0
    assert "p" in result.output


def test_module_list_happy_path(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_modules(self):  # noqa: ANN001
            return [{"name": "m1", "status": "running", "required": True, "uptime": "1s"}]

    monkeypatch.setattr("hc.commands.module.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["module", "list"])
    assert result.exit_code == 0
    assert "m1" in result.output


def test_logs_smoke(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def stream_logs(self, module: str | None, follow: bool):  # noqa: ANN001
            assert module is None
            assert follow is False
            yield "INFO hello"

    monkeypatch.setattr("hc.commands.logs.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    assert "hello" in result.output
