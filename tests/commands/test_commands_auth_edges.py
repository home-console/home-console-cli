from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_auth_bootstrap_fails_when_no_data(monkeypatch, runner: CliRunner) -> None:
    monkeypatch.setattr("hc.commands.auth.anyio.run", lambda fn, *a, **k: None)
    from hc.main import app

    res = runner.invoke(app, ["auth", "bootstrap"])
    assert res.exit_code == 1


def test_auth_logout_fails_without_host(monkeypatch, runner: CliRunner, isolated_home) -> None:
    cfg = isolated_home.Config.load()
    cfg.core.host = ""
    cfg.core.token = "T"
    cfg.save()

    from hc.main import app

    res = runner.invoke(app, ["auth", "logout"])
    assert res.exit_code == 1


def test_auth_init_password_mismatch(monkeypatch, runner: CliRunner) -> None:
    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        if fn.__name__ == "auth_bootstrap":
            return {"initialized": False}
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    monkeypatch.setattr("hc.commands.auth.getpass.getpass", lambda prompt: "a" if "New" in prompt else "b")
    from hc.main import app

    res = runner.invoke(app, ["auth", "init"])
    assert res.exit_code == 1


def test_auth_init_happy_path(monkeypatch, runner: CliRunner) -> None:
    calls: list[str] = []

    def fake_run(fn, *args, **kwargs):  # noqa: ANN001
        calls.append(fn.__name__)
        if fn.__name__ == "auth_bootstrap":
            return {"initialized": False}
        if fn.__name__ == "auth_initialize":
            return {"ok": True}
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr("hc.commands.auth.anyio.run", fake_run)
    monkeypatch.setattr(
        "hc.commands.auth.getpass.getpass",
        lambda prompt: "secret" if "New" in prompt else "secret",
    )
    from hc.main import app

    res = runner.invoke(app, ["auth", "init"])
    assert res.exit_code == 0
    assert "auth_bootstrap" in calls
    assert "auth_initialize" in calls


def test_auth_user_list_renders_table(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def list_users(self):  # noqa: ANN001
            return {
                "users": [
                    {
                        "user_id": "u1",
                        "username": "U1",
                        "is_admin": False,
                        "scopes": ["a", "b"],
                        "created_at": "t",
                    }
                ]
            }

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "user", "list"])
    assert res.exit_code == 0
    assert "u1" in res.output


def test_auth_sessions_list_supports_top_level_list(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def list_sessions(self):  # noqa: ANN001
            return [{"session_id": "s1", "user_id": "u1", "created_at": "c", "expires_at": "e"}]

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "sessions", "list"])
    assert res.exit_code == 0
    assert "s1" in res.output


def test_auth_api_key_rotate_without_key_in_payload(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def api_keys_rotate(self, key_id: str):  # noqa: ANN001
            assert key_id == "k1"
            return {"detail": "rotated"}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["auth", "api-key", "rotate", "k1"])
    assert res.exit_code == 0
    assert "обновлён" in res.output.lower()
