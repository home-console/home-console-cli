from __future__ import annotations

from dataclasses import dataclass

import anyio

from hc.client import HCClient
from hc.config import Config


@dataclass(slots=True)
class Capabilities:
    monitor_health: bool
    auth_bootstrap: bool
    auth_me: bool
    admin_status: bool
    inspector_plugins: bool
    api_keys: bool


def probe(cfg: Config) -> Capabilities:
    base_url = f"http://{cfg.core.host}:{cfg.core.port}"
    client = HCClient(
        base_url=base_url, token=cfg.core.token, verify_ssl=cfg.core.verify_ssl, auth=cfg.core.auth
    )

    async def _run() -> Capabilities:
        # Capabilities probe должен быть "тихим": не спамим подсказками при 401/403.
        silent = HCClient(
            base_url=client.base_url,
            token=client.token,
            verify_ssl=client.verify_ssl,
            auth=client.auth,
        )

        def _mute_hint() -> None:
            return None

        silent._auth_hint = lambda *_args, **_kwargs: _mute_hint()  # type: ignore[attr-defined]

        mh = await silent.health()
        ab = await silent.auth_bootstrap()
        am = await silent.auth_me()
        st = await silent.admin_status()
        ip = await silent.inspector_plugins()
        ak = await silent.api_keys_list()
        return Capabilities(
            monitor_health=bool(mh),
            auth_bootstrap=bool(ab),
            auth_me=bool(am),
            admin_status=bool(st),
            inspector_plugins=bool(ip),
            api_keys=bool(ak),
        )

    return anyio.run(_run)

