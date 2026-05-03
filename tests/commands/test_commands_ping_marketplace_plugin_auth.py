from __future__ import annotations

import importlib

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_ping_uses_env_host_port(monkeypatch, runner: CliRunner) -> None:
    seen: dict[str, str] = {}

    class _C:
        def __init__(self, base_url: str, token: str, verify_ssl: bool = True, **kw):  # noqa: ANN001
            seen["base_url"] = base_url
            seen["token"] = token

        async def health(self):  # noqa: ANN001
            return {"status": "ok"}

    monkeypatch.setenv("HC_HOST", "example.org")
    monkeypatch.setenv("HC_PORT", "12345")
    monkeypatch.setattr("hc.commands.ping.HCClient", _C)

    from hc.main import app

    result = runner.invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "example.org:12345" in result.output
    assert seen["base_url"] == "http://example.org:12345"
    assert seen["token"] == ""


def test_ping_failure_exit_code_1(monkeypatch, runner: CliRunner) -> None:
    class _C:
        def __init__(self, *a, **kw):  # noqa: ANN001
            pass

        async def health(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.ping.HCClient", _C)
    from hc.main import app

    result = runner.invoke(app, ["ping", "--host", "h", "--port", "9"])
    assert result.exit_code == 1
    assert "Core недоступен" in result.output


def test_marketplace_updates_no_updates(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_updates(self):  # noqa: ANN001
            return []

    monkeypatch.setattr("hc.commands.marketplace.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["marketplace", "updates"])
    assert result.exit_code == 0
    assert "Все плагины актуальны" in result.output


def test_marketplace_updates_prints_table(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_updates(self):  # noqa: ANN001
            return [{"name": "p1", "current": "1.0", "latest": "2.0"}]

    monkeypatch.setattr("hc.commands.marketplace.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["marketplace", "updates"])
    assert result.exit_code == 0
    assert "p1" in result.output
    assert "1.0" in result.output
    assert "2.0" in result.output


def test_plugin_reload_happy_path(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def reload_plugin(self, name: str):  # noqa: ANN001
            return {"ok": True, "name": name}

    monkeypatch.setattr("hc.commands.plugin.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["plugin", "reload", "demo"])
    assert result.exit_code == 0
    assert "demo" in result.output


def test_auth_user_list(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def list_users(self):  # noqa: ANN001
            return {"users": [{"user_id": "u1", "username": "User", "is_admin": True, "scopes": []}]}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["auth", "user", "list"])
    assert result.exit_code == 0
    assert "u1" in result.output


def test_auth_sessions_revoke_all(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def revoke_all_sessions(self):  # noqa: ANN001
            return {"ok": True}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    result = runner.invoke(app, ["auth", "sessions", "revoke-all"])
    assert result.exit_code == 0
    assert "Все сессии отозваны" in result.output

