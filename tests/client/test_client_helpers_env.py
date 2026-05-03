from __future__ import annotations

import importlib


def test_client_from_config_applies_env_overrides_and_disables_refresh_for_env_token(
    isolated_home, monkeypatch
) -> None:
    cfg = isolated_home.Config.load()
    cfg.core.host = "cfg-host"
    cfg.core.port = 1111
    cfg.core.token = "CFG_TOKEN"
    cfg.core.refresh_token = "CFG_REFRESH"
    cfg.core.auth = "bearer"

    monkeypatch.setenv("HC_HOST", "env-host")
    monkeypatch.setenv("HC_PORT", "2222")
    monkeypatch.setenv("HC_TOKEN", "ENV_TOKEN")
    monkeypatch.setenv("HC_AUTH", "api-key")

    import hc.commands._client_helpers as h

    importlib.reload(h)

    c = h.client_from_config(cfg)
    assert c.base_url == "http://env-host:2222"
    assert c.token == "ENV_TOKEN"
    assert c.auth == "api-key"
    assert c.refresh_token == ""
    assert c.on_token_refreshed is None


def test_client_from_config_uses_config_refresh_and_wires_callback() -> None:
    class _Core:
        def __init__(self) -> None:
            self.host = "cfg-host"
            self.port = 1111
            self.token = "CFG_TOKEN"
            self.refresh_token = "CFG_REFRESH"
            self.auth = "bearer"
            self.verify_ssl = True

    class _Cfg:
        def __init__(self) -> None:
            self.core = _Core()
            self.saved: list[str] = []

        def save(self) -> None:
            self.saved.append(self.core.token)

    cfg = _Cfg()

    import hc.commands._client_helpers as h

    importlib.reload(h)

    c = h.client_from_config(cfg)
    assert c.refresh_token == "CFG_REFRESH"
    assert c.on_token_refreshed is not None
    c.on_token_refreshed("NEW_TOKEN")
    assert cfg.core.token == "NEW_TOKEN"
    assert cfg.saved == ["NEW_TOKEN"]

