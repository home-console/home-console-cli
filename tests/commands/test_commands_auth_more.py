from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_auth_login_saves_config(monkeypatch, runner: CliRunner, isolated_home) -> None:
    monkeypatch.setattr("hc.commands.auth.getpass.getpass", lambda prompt: "pw")

    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        assert fn.__name__ == "auth_login_full"
        return ({"result": {"access_token": "JWT"}}, "sess")

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    from hc.main import app

    res = runner.invoke(app, ["auth", "login", "-u", "admin", "--host", "h", "--port", "1234"])
    assert res.exit_code == 0

    cfg = isolated_home.Config.load()
    assert cfg.core.host == "h"
    assert cfg.core.port == 1234
    assert cfg.core.token == "JWT"
    assert cfg.core.refresh_token == "sess"
    assert cfg.core.auth == "bearer"


def test_auth_login_fails_on_bad_payload(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.auth.getpass.getpass", lambda prompt: "pw")

    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        return ({"nope": True}, "")

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    from hc.main import app

    res = runner.invoke(app, ["auth", "login", "-u", "admin"])
    assert res.exit_code == 1


def test_auth_bootstrap_prints_initialized(monkeypatch, runner: CliRunner) -> None:
    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        return {"result": {"initialized": True}}

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    from hc.main import app

    res = runner.invoke(app, ["auth", "bootstrap"])
    assert res.exit_code == 0
    assert "initialized: True" in res.output


def test_auth_init_exits_when_already_initialized(monkeypatch, runner: CliRunner) -> None:
    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        return {"initialized": True}

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    from hc.main import app

    res = runner.invoke(app, ["auth", "init"])
    assert res.exit_code == 0
    assert "уже инициализирована" in res.output.lower()


def test_auth_whoami_and_check(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def auth_me(self):  # noqa: ANN001
            return {"user_id": "u1"}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    assert runner.invoke(app, ["auth", "whoami"]).exit_code == 0
    assert runner.invoke(app, ["auth", "check"]).exit_code == 0


def test_auth_check_fails_when_me_is_none(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def auth_me(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "check"])
    assert res.exit_code == 1


def test_auth_logout_clears_tokens(monkeypatch, runner: CliRunner, isolated_home) -> None:
    class _Client:
        async def auth_logout(self):  # noqa: ANN001
            return {"ok": True}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())

    cfg = isolated_home.Config.load()
    cfg.core.host = "h"
    cfg.core.port = 1
    cfg.core.token = "T"
    cfg.core.refresh_token = "R"
    cfg.save()

    from hc.main import app

    res = runner.invoke(app, ["auth", "logout"])
    assert res.exit_code == 0

    cfg2 = isolated_home.Config.load()
    assert cfg2.core.token == ""
    assert cfg2.core.refresh_token == ""


def test_auth_user_create(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.auth.getpass.getpass", lambda prompt: "pw")

    class _Client:
        async def create_user(self, user_id: str, username: str, password: str, is_admin: bool):  # noqa: ANN001
            assert user_id == "u2"
            assert username == "U2"
            assert password == "pw"
            assert is_admin is True
            return {"ok": True}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(
        app,
        ["auth", "user", "create", "--user-id", "u2", "--username", "U2", "--admin"],
    )
    assert res.exit_code == 0


def test_auth_sessions_revoke(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def revoke_session(self, session_id: str):  # noqa: ANN001
            assert session_id == "s1"
            return {"ok": True}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "sessions", "revoke", "s1"])
    assert res.exit_code == 0


def test_auth_api_key_list_and_revoke_errors(monkeypatch, runner: CliRunner) -> None:
    class _Empty:
        async def api_keys_list(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Empty())
    from hc.main import app

    assert runner.invoke(app, ["auth", "api-key", "list"]).exit_code == 1

    class _RevokeNone:
        async def api_keys_revoke(self, key_id: str):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _RevokeNone())
    assert runner.invoke(app, ["auth", "api-key", "revoke", "k1"]).exit_code == 1


def test_auth_api_key_create_without_key_field_exits_zero(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def api_keys_create(self, name: str | None = None):  # noqa: ANN001
            return {"detail": "created"}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "api-key", "create"])
    assert res.exit_code == 0
    assert "создано" in res.output.lower()
