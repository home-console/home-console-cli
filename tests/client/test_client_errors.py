from __future__ import annotations

import httpx

from hc.client import HCClient


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:  # noqa: ANN001
        self.lines.append(" ".join(str(a) for a in args))


def test_client_401_prints_token_expired(monkeypatch) -> None:
    rec = _Recorder()

    class _Console:
        def print(self, *args, **kwargs) -> None:  # noqa: ANN001
            rec.print(*args, **kwargs)

    monkeypatch.setattr("hc.client.Console", _Console)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    import anyio

    health = anyio.run(c.health)
    assert health is None
    assert any("сессия истекла" in line.lower() for line in rec.lines)


def test_client_autodetects_api_prefix(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.startswith("/api/health"):
            return httpx.Response(404, text="no")
        if request.url.path.startswith("/api/v1/health"):
            return httpx.Response(200, json={"version": "1"})
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    import anyio

    health = anyio.run(c.health)
    assert health and health["version"] == "1"
    assert c.api_prefix == "/api/v1"
    assert any("/api/v1/health" in u for u in calls)

