from __future__ import annotations

from hc.client import HCClient


def test_headers_empty_without_token() -> None:
    c = HCClient(base_url="http://x", token="")
    assert c._headers() == {}


def test_headers_bearer_mode() -> None:
    c = HCClient(base_url="http://x", token="abc", auth="bearer")
    assert c._headers() == {"Authorization": "Bearer abc"}


def test_headers_api_key_mode() -> None:
    c = HCClient(base_url="http://x", token="k", auth="api-key")
    assert c._headers() == {"X-API-Key": "k"}


def test_headers_auto_detects_jwt_by_dots() -> None:
    c = HCClient(base_url="http://x", token="a.b.c", auth="auto")
    assert c._headers() == {"Authorization": "Bearer a.b.c"}


def test_headers_auto_falls_back_to_api_key() -> None:
    c = HCClient(base_url="http://x", token="no-dots", auth="auto")
    assert c._headers() == {"X-API-Key": "no-dots"}

