from __future__ import annotations

import json
import time

from rich.console import Console

from hc.constants import STATE_DIR

_CACHE_FILE = STATE_DIR / "version_check.json"
_FETCH_TIMEOUT = 2.0
_PYPI_URL = "https://pypi.org/pypi/homeconsole-cli/json"
_PYPI_PACKAGE = "homeconsole-cli"


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


def _fetch_latest() -> str | None:
    try:
        import httpx

        with httpx.Client(timeout=_FETCH_TIMEOUT) as client:
            resp = client.get(_PYPI_URL)
        if resp.status_code == 200:
            return str(resp.json()["info"]["version"])
    except Exception:
        pass
    return None


def get_update_notification(current: str) -> str | None:
    """
    Returns latest version string if newer than current, else None.

    If cache already records a newer release, returns immediately.
    Otherwise queries PyPI synchronously (≤2s) so the banner appears
    in the same shell session right after a release.
    """
    try:
        data = _read_cache()
        cached_latest = str(data.get("latest", "") or "")

        if cached_latest and _is_newer(cached_latest, current):
            return cached_latest

        latest = _fetch_latest()
        if latest:
            _write_cache(latest)

        if latest and _is_newer(latest, current):
            return latest
    except Exception:
        pass
    return None


def upgrade_hint() -> str:
    return "pipx upgrade homeconsole-cli  |  pip install -U homeconsole-cli  |  hc upgrade"


def print_update_banner(console: Console, current: str) -> bool:
    """Print yellow banner if a newer release exists. Returns True if printed."""
    latest = get_update_notification(current)
    if not latest:
        return False
    console.print(
        f"[yellow]→ Доступна новая версия [bold]{latest}[/bold] "
        f"(текущая {current})[/yellow]"
    )
    console.print(f"[dim]  {upgrade_hint()}[/dim]")
    return True
