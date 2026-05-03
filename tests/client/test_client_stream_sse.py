from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx

from hc.client import HCClient


def test_stream_sse_yields_data_lines(monkeypatch) -> None:
    class _FakeAsyncClient:
        def __init__(self, *a, **kw):  # noqa: ANN001
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def stream(self, method: str, url: str, headers=None, **kwargs):  # noqa: ANN001
            assert method == "GET"
            assert url.endswith("/api/logs")

            class _Resp:
                status_code = 200
                is_error = False

                async def __aenter__(self_inner):
                    return self_inner

                async def __aexit__(self_inner, exc_type, exc, tb):  # noqa: ANN001
                    return False

                async def aiter_lines(self_inner) -> AsyncIterator[str]:
                    yield ""
                    yield "data: hello"
                    yield "data: world"

            return _Resp()

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _FakeAsyncClient)

    c = HCClient(base_url="http://x", token="t", api_prefix="/api")

    async def _run() -> list[str]:
        out: list[str] = []
        async for msg in c.stream_logs(module=None, follow=False):
            out.append(msg)
        return out

    msgs = anyio.run(_run)
    assert msgs == ["hello", "world"]


def test_stream_sse_401_refreshes_once(monkeypatch) -> None:
    attempts = {"n": 0}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):  # noqa: ANN001
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def post(self, path: str, cookies=None, **kwargs):  # noqa: ANN001
            assert cookies == {"session_id": "sess"}
            return httpx.Response(200, json={"result": {"access_token": "NEW"}})

        def stream(self, method: str, url: str, headers=None, **kwargs):  # noqa: ANN001
            attempts["n"] += 1
            status = 401 if attempts["n"] == 1 else 200

            class _Resp:
                def __init__(self) -> None:
                    self.status_code = status
                    self.is_error = status >= 400

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
                    return False

                async def aiter_lines(self) -> AsyncIterator[str]:
                    if self.status_code != 200:
                        if False:  # pragma: no cover
                            yield ""
                        return
                    yield "data: ok"

            return _Resp()

    monkeypatch.setattr("hc.client.httpx.AsyncClient", _FakeAsyncClient)

    refreshed: list[str] = []
    c = HCClient(
        base_url="http://x",
        token="OLD",
        api_prefix="/api",
        refresh_token="sess",
        on_token_refreshed=refreshed.append,
    )

    async def _run() -> list[Any]:
        out: list[Any] = []
        async for msg in c.stream_logs(module=None, follow=False):
            out.append(msg)
        return out

    msgs = anyio.run(_run)
    assert msgs == ["ok"]
    assert refreshed == ["NEW"]
    assert c.token == "NEW"
    assert attempts["n"] == 2
