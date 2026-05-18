from __future__ import annotations

import anyio
import httpx

from hc.client import HCClient


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:  # noqa: ANN001
        self.lines.append(" ".join(str(a) for a in args))


def test_request_json_403_calls_auth_hint(monkeypatch) -> None:
    rec = _Recorder()

    class _Console:
        def print(self, *args, **kwargs) -> None:  # noqa: ANN001
            rec.print(*args, **kwargs)

    monkeypatch.setattr("hc.client.Console", _Console)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/health"):
            return httpx.Response(403, json={"detail": "forbidden"})
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t", api_prefix="/api/v1")
    data = anyio.run(c._request_json, "GET", "/health")
    assert data is None
    assert any("Не хватает прав" in line for line in rec.lines)


def test_request_json_all_prefixes_404_prints_hint(monkeypatch) -> None:
    rec = _Recorder()

    class _Console:
        def print(self, *args, **kwargs) -> None:  # noqa: ANN001
            rec.print(*args, **kwargs)

    monkeypatch.setattr("hc.client.Console", _Console)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    data = anyio.run(c._request_json, "GET", "/nope")
    assert data is None
    assert any("endpoint не найден" in line.lower() for line in rec.lines)


def test_request_json_http_error_prints_and_returns_none(monkeypatch) -> None:
    rec = _Recorder()

    class _Console:
        def print(self, *args, **kwargs) -> None:  # noqa: ANN001
            rec.print(*args, **kwargs)

    monkeypatch.setattr("hc.client.Console", _Console)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/health"):
            return httpx.Response(500, text="boom")
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t", api_prefix="/api/v1")
    data = anyio.run(c._request_json, "GET", "/health")
    assert data is None
    assert any("HTTP 500" in line for line in rec.lines)


def test_request_json_locks_prefix_after_first_success(monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/api/x"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):  # noqa: ANN001
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _Client)

    c = HCClient(base_url="http://x", token="t")
    first = anyio.run(c._request_json, "GET", "/x")
    assert first == {"ok": True}
    assert c.api_prefix == "/api"

    second = anyio.run(c._request_json, "GET", "/x")
    assert second == {"ok": True}
    assert calls[0].startswith("/api/v1/x")
    assert calls[1].startswith("/api/x")
