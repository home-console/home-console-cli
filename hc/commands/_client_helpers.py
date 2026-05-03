from __future__ import annotations

import os

import typer
from rich.console import Console

from hc.client import HCClient
from hc.config import Config


def _mute_auth_hints(client: HCClient) -> HCClient:
    # Capabilities probe / фоновые операции не должны спамить подсказками при 401/403.
    client._auth_hint = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    return client


def client_from_config(cfg: Config, *, token: str | None = None, silent: bool = False) -> HCClient:
    """
    Собрать HCClient из уже загруженного `Config`.
    Полезно для утилитных модулей (capabilities, REPL), где конфиг уже есть.
    """

    t = token if token is not None else (os.getenv("HC_TOKEN") or cfg.core.token)
    base_url = f"http://{cfg.core.host}:{cfg.core.port}"
    client = HCClient(
        base_url=base_url,
        token=t,
        verify_ssl=cfg.core.verify_ssl,
        auth=cfg.core.auth,
    )
    return _mute_auth_hints(client) if silent else client


def require_client(console: Console, *, silent: bool = False) -> HCClient:
    """
    Единая точка сборки API-клиента:
    - берём host/port из config
    - токен: HC_TOKEN env > config
    - выставляем verify_ssl/auth
    """

    cfg = Config.load()
    token = os.getenv("HC_TOKEN") or cfg.core.token
    if not cfg.core.host.strip() or not (token or "").strip():
        console.print("[red]Ошибка:[/red] Сначала подключись: `hc connect <host>`")
        console.print("[dim]Подсказка:[/dim] токен можно передать через `--token`, переменную `HC_TOKEN`, или ввести интерактивно.")
        raise typer.Exit(code=1)

    return client_from_config(cfg, token=token, silent=silent)

