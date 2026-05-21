from __future__ import annotations

from hc.commands.connect import connect_and_save


def test_connect_and_save_uses_health_fallback(monkeypatch, isolated_home) -> None:
    class _Client:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            pass

        async def admin_status(self):  # noqa: ANN001
            return None

        async def health(self):  # noqa: ANN001
            return {"status": "ok"}

        async def core_version(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.connect.HCClient", _Client)

    cfg = isolated_home.Config.load()
    cfg.core.verify_ssl = True
    cfg.save()

    health = connect_and_save(host="h", port=9, token="T", auth="bearer")
    assert isinstance(health, dict)

    cfg2 = isolated_home.Config.load()
    assert cfg2.core.host == "h"
    assert cfg2.core.port == 9
    assert cfg2.core.token == "T"
    assert cfg2.core.auth == "bearer"


def test_connect_and_save_returns_none_when_unreachable(monkeypatch, isolated_home) -> None:
    class _Client:
        def __init__(self, *a, **k) -> None:  # noqa: ANN001
            pass

        async def admin_status(self):  # noqa: ANN001
            return None

        async def health(self):  # noqa: ANN001
            return None

    monkeypatch.setattr("hc.commands.connect.HCClient", _Client)

    cfg = isolated_home.Config.load()
    cfg.core.host = "old"
    cfg.core.token = "old"
    cfg.save()

    assert connect_and_save(host="h", port=9, token="T", auth="auto") is None

    cfg2 = isolated_home.Config.load()
    assert cfg2.core.host == "old"
    assert cfg2.core.token == "old"
