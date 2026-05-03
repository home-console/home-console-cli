from __future__ import annotations

from hc.capabilities import Capabilities, probe
from hc.config import Config


def test_capabilities_probe_aggregates_flags(monkeypatch) -> None:
    class _Silent:
        async def health(self):  # noqa: ANN001
            return {"ok": True}

        async def auth_bootstrap(self):  # noqa: ANN001
            return None

        async def auth_me(self):  # noqa: ANN001
            return {"u": 1}

        async def admin_status(self):  # noqa: ANN001
            return None

        async def inspector_plugins(self):  # noqa: ANN001
            return {"x": 1}

        async def api_keys_list(self):  # noqa: ANN001
            return {"items": []}

    monkeypatch.setattr("hc.capabilities.client_from_config", lambda cfg, silent=True: _Silent())

    caps = probe(Config())
    assert caps == Capabilities(
        monitor_health=True,
        auth_bootstrap=False,
        auth_me=True,
        admin_status=False,
        inspector_plugins=True,
        api_keys=True,
    )
