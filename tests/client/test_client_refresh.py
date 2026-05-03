from __future__ import annotations

import anyio
import httpx

from hc.client import HCClient
from hc import api as endpoints


def test_request_json_retries_after_refresh_and_persists_token(monkeypatch) -> None:
    # 1) main request returns 401
    # 2) refresh endpoint returns new access_token
    # 3) main request retries and returns 200 with json
    calls: list[tuple[str, str]] = []
    saved_tokens: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):  # noqa: ANN001
            self.base_url = kw.get("base_url", "")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def request(self, method: str, path: str, headers=None, **kwargs):  # noqa: ANN001
            calls.append((method, str(path)))
            # first request: 401, second: 200
            if calls.count((method, str(path))) == 1:
                return httpx.Response(401, json={"detail": "unauthorized"})
            return httpx.Response(200, json={"ok": True})

        async def post(self, path: str, cookies=None, **kwargs):  # noqa: ANN001
            calls.append(("POST", str(path)))
            assert str(path) == endpoints.AUTH_REFRESH
            assert cookies == {"session_id": "refresh-cookie"}
            return httpx.Response(200, json={"result": {"access_token": "NEW"}})

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _FakeAsyncClient)

    c = HCClient(
        base_url="http://x",
        token="OLD",
        refresh_token="refresh-cookie",
        on_token_refreshed=saved_tokens.append,
    )

    data = anyio.run(c._request_json_absolute, "GET", "/admin/v1/status")
    assert data == {"ok": True}
    assert c.token == "NEW"
    assert saved_tokens == ["NEW"]


def test_try_refresh_returns_false_without_refresh_token() -> None:
    c = HCClient(base_url="http://x", token="t", refresh_token="")
    assert anyio.run(c._try_refresh) is False

