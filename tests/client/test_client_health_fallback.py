from __future__ import annotations

import anyio
import httpx

from hc.client import HCClient


def test_health_falls_back_to_prefixed_health_when_monitor_not_dict(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/api/v1/monitor/health"):
            return httpx.Response(200, json=["not-a-dict"])
        if request.url.path.startswith("/monitor/health"):
            return httpx.Response(404, text="no")
        if request.url.path.startswith("/api/health"):
            return httpx.Response(404, text="no")
        if request.url.path.startswith("/api/v1/health"):
            return httpx.Response(200, json={"status": "ok", "via": "prefixed"})
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    data = anyio.run(c.health)
    assert isinstance(data, dict)
    assert data.get("via") == "prefixed"
    assert c.api_prefix == "/api/v1"
    assert any(p.startswith("/api/v1/monitor/health") for p in calls)
    assert any(p.startswith("/api/v1/health") for p in calls)
