from __future__ import annotations

import json
import threading
import time

from hc.constants import STATE_DIR

_CACHE_FILE = STATE_DIR / "version_check.json"
_TTL = 86400  # 24 hours
_PYPI_URL = "https://pypi.org/pypi/homeconsole-cli/json"


def _parse_ver(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    except Exception:
        return (0,)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_ver(latest) > _parse_ver(current)


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _write_cache(latest: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "latest": latest}))
    except Exception:
        pass


def _fetch_and_cache() -> None:
    """Background thread: fetch latest version from PyPI and cache it."""
    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(_PYPI_URL)
        if resp.status_code == 200:
            latest = resp.json()["info"]["version"]
            _write_cache(latest)
    except Exception:
        pass


def get_update_notification(current: str) -> str | None:
    """
    Returns a notification string if a newer version is available, else None.
    Uses a 24-hour cache — never blocks startup.
    If cache is missing or expired, starts a background refresh for next session.
    """
    try:
        data = _read_cache()
        ts = data.get("ts", 0)
        latest = data.get("latest", "")

        if not latest or time.time() - ts > _TTL:
            # Refresh in background — result visible next session
            threading.Thread(target=_fetch_and_cache, daemon=True).start()

        if latest and _is_newer(latest, current):
            return latest
    except Exception:
        pass
    return None
