from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_connect_saves_config_via_connect_and_save(monkeypatch, runner: CliRunner) -> None:
    class _Core:
        def __init__(self) -> None:
            self.verify_ssl = True
            self.host = ""
            self.port = 0
            self.token = ""
            self.auth = ""

    class _Cfg:
        def __init__(self) -> None:
            self.core = _Core()
            self.saved = 0

        def save(self) -> None:
            self.saved += 1

    cfg = _Cfg()

    class _Client:
        def __init__(self, base_url: str, token: str, verify_ssl: bool = True, auth: str = "auto") -> None:
            assert base_url == "http://h:9"
            assert token == "T"
            assert verify_ssl is True
            assert auth == "api-key"

        async def admin_status(self):  # noqa: ANN001
            return {"ok": True}

        async def health(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.connect.Config.load", lambda: cfg)
    monkeypatch.setattr("hc.commands.connect.HCClient", _Client)

    from hc.main import app

    res = runner.invoke(app, ["connect", "h", "--port", "9", "--token", "T", "--auth", "api-key"])
    assert res.exit_code == 0
    assert "Подключено" in res.output
    assert cfg.core.host == "h"
    assert cfg.core.port == 9
    assert cfg.core.token == "T"
    assert cfg.core.auth == "api-key"
    assert cfg.saved == 1


def test_connect_fails_without_token(monkeypatch, runner: CliRunner) -> None:
    # avoid interactive getpass in test environment
    monkeypatch.setattr("hc.commands.connect.getpass.getpass", lambda prompt: "   ")
    from hc.main import app

    res = runner.invoke(app, ["connect", "h", "--port", "9"])
    assert res.exit_code == 1
    assert "токен не задан" in res.output.lower()


def test_install_cancel_does_not_call_install_plugin(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_marketplace_index(self):  # noqa: ANN001
            return [{"name": "p1", "version": "1.0", "description": "d", "dependencies": []}]

        async def install_plugin(self, name: str) -> AsyncIterator[str]:  # noqa: ANN001
            raise AssertionError("should not be called when confirm=False")
            if False:  # pragma: no cover
                yield ""

    monkeypatch.setattr("hc.commands.install.require_client", lambda console: _Client())
    monkeypatch.setattr("hc.commands.install.typer.confirm", lambda *a, **kw: False)
    from hc.main import app

    res = runner.invoke(app, ["install", "p1"])
    assert res.exit_code == 0


def test_remove_blocks_when_has_dependents_and_no_force(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_plugins(self):  # noqa: ANN001
            return [
                {"name": "a", "dependencies": ["p1"]},
                {"name": "p1", "dependencies": []},
            ]

    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _Client())
    from hc.main import app

    res = runner.invoke(app, ["remove", "p1"])
    assert res.exit_code == 1
    # rich может переносить слова, поэтому проверяем по частям
    out = res.output.lower()
    assert "нельзя" in out
    assert "удалить" in out


def test_remove_force_confirms_and_calls_remove_plugin(monkeypatch, runner: CliRunner) -> None:
    class _Client:
        async def get_plugins(self):  # noqa: ANN001
            return [{"name": "a", "dependencies": ["p1"]}]

        async def remove_plugin(self, name: str):  # noqa: ANN001
            assert name == "p1"
            return {"ok": True}

    monkeypatch.setattr("hc.commands.remove.require_client", lambda console: _Client())
    monkeypatch.setattr("hc.commands.remove.typer.confirm", lambda *a, **kw: True)
    from hc.main import app

    res = runner.invoke(app, ["remove", "p1", "--force"])
    assert res.exit_code == 0
    assert "удалён" in res.output.lower()


def test_auth_api_key_create_save_writes_config(monkeypatch, runner: CliRunner) -> None:
    class _Core:
        def __init__(self) -> None:
            self.token = ""
            self.auth = "bearer"

    class _Cfg:
        def __init__(self) -> None:
            self.core = _Core()
            self.saved = 0

        def save(self) -> None:
            self.saved += 1

    cfg = _Cfg()

    class _Client:
        async def api_keys_create(self, name: str | None = None):  # noqa: ANN001
            assert name == "my"
            return {"api_key": "K"}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    monkeypatch.setattr("hc.commands.auth.Config.load", lambda: cfg)
    from hc.main import app

    res = runner.invoke(app, ["auth", "api-key", "create", "--name", "my", "--save"])
    assert res.exit_code == 0
    assert cfg.core.token == "K"
    assert cfg.core.auth == "api-key"
    assert cfg.saved == 1


def test_auth_api_key_rotate_save_writes_config(monkeypatch, runner: CliRunner) -> None:
    class _Core:
        def __init__(self) -> None:
            self.token = ""
            self.auth = "bearer"

    class _Cfg:
        def __init__(self) -> None:
            self.core = _Core()
            self.saved = 0

        def save(self) -> None:
            self.saved += 1

    cfg = _Cfg()

    class _Client:
        async def api_keys_rotate(self, key_id: str):  # noqa: ANN001
            assert key_id == "id1"
            return {"key": "NEWKEY"}

    monkeypatch.setattr("hc.commands.auth.require_client", lambda console: _Client())
    monkeypatch.setattr("hc.commands.auth.Config.load", lambda: cfg)
    from hc.main import app

    res = runner.invoke(app, ["auth", "api-key", "rotate", "id1", "--save"])
    assert res.exit_code == 0
    assert cfg.core.token == "NEWKEY"
    assert cfg.core.auth == "api-key"
    assert cfg.saved == 1

