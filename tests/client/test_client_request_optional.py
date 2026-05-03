from __future__ import annotations

import anyio
import httpx

from hc.client import HCClient


def test_request_json_optional_skips_first_prefix_on_404(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.startswith("/api/marketplace/index"):
            return httpx.Response(404, text="no")
        if request.url.path.startswith("/api/v1/marketplace/index"):
            return httpx.Response(200, json=[{"name": "p", "version": "1"}])
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    data = anyio.run(c.get_marketplace_index)
    assert data == [{"name": "p", "version": "1"}]
    assert c.api_prefix == "/api/v1"
    assert any("/api/marketplace/index" in u for u in calls)
    assert any("/api/v1/marketplace/index" in u for u in calls)


def test_request_json_optional_returns_none_on_404_after_prefix_lock(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/marketplace/index"):
            return httpx.Response(404, text="no")
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t", api_prefix="/api/v1")
    data = anyio.run(c.get_marketplace_index)
    assert data is None
