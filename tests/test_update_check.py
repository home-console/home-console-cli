from __future__ import annotations

import importlib

import pytest

import hc.constants as constants


@pytest.fixture()
def update_check(isolated_home):
    import hc.update_check as uc

    importlib.reload(constants)
    importlib.reload(uc)
    return uc


def test_notification_from_cache_when_update_known(update_check, monkeypatch) -> None:
    uc = update_check
    def _no_fetch() -> str:
        raise AssertionError("no fetch")

    monkeypatch.setattr(uc, "_fetch_latest", _no_fetch)
    uc._write_cache("0.0.9")
    assert uc.get_update_notification("0.0.8") == "0.0.9"


def test_notification_after_sync_fetch_when_cache_stale(update_check, monkeypatch) -> None:
    uc = update_check
    uc._write_cache("0.0.7")
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "0.0.9")
    assert uc.get_update_notification("0.0.8") == "0.0.9"


def test_no_notification_when_up_to_date(update_check, monkeypatch) -> None:
    uc = update_check
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "0.0.8")
    assert uc.get_update_notification("0.0.8") is None


def test_fetch_when_cache_empty(update_check, monkeypatch) -> None:
    uc = update_check
    monkeypatch.setattr(uc, "_fetch_latest", lambda: "1.0.0")
    assert uc.get_update_notification("0.0.8") == "1.0.0"
