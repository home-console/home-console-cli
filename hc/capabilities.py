from __future__ import annotations

from dataclasses import dataclass

import anyio

from hc.config import Config
from hc.commands._client_helpers import client_from_config


@dataclass(slots=True)
class Capabilities:
    monitor_health: bool
    auth_bootstrap: bool
    auth_me: bool
    admin_status: bool
    inspector_plugins: bool
    api_keys: bool


def probe(cfg: Config) -> Capabilities:
    silent = client_from_config(cfg, silent=True)

    async def _run() -> Capabilities:
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

