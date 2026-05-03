from __future__ import annotations

import anyio

from hc.client import HCClient


def test_get_marketplace_updates_detects_newer_versions(monkeypatch) -> None:
    c = HCClient(base_url="http://x", token="t")

    async def fake_get_plugins(self):  # noqa: ANN001
        return [{"name": "a", "version": "1.0"}, {"name": "b", "version": "2.0"}]

    async def fake_get_marketplace_index(self):  # noqa: ANN001
        return [{"name": "a", "version": "1.1"}, {"name": "b", "version": "2.0"}]

    monkeypatch.setattr(HCClient, "get_plugins", fake_get_plugins)
    monkeypatch.setattr(HCClient, "get_marketplace_index", fake_get_marketplace_index)

    updates = anyio.run(c.get_marketplace_updates)
    assert updates == [{"name": "a", "current": "1.0", "latest": "1.1"}]
