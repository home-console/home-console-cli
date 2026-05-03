from __future__ import annotations

import os
from collections.abc import Callable

import typer
from rich.console import Console

from hc.client import HCClient
from hc.config import Config


def _mute_auth_hints(client: HCClient) -> HCClient:
    # Capabilities probe / фоновые операции не должны спамить подсказками при 403.
    client._auth_hint = lambda *a, **kw: None  # type: ignore[attr-defined]
    client._expired_session_hint = lambda: None  # type: ignore[attr-defined]
    return client


def _make_refresh_callback(cfg: Config) -> Callable[[str], None]:
    def _on_refresh(new_token: str) -> None:
        cfg.core.token = new_token
        cfg.save()
    return _on_refresh


def client_from_config(cfg: Config, *, token: str | None = None, silent: bool = False) -> HCClient:
    """
    Собрать HCClient из уже загруженного `Config`.
    Полезно для утилитных модулей (capabilities, REPL), где конфиг уже есть.

    Env overrides: HC_HOST, HC_PORT, HC_AUTH, HC_TOKEN (через effective_token).
    """
    env_token = os.getenv("HC_TOKEN")
    effective_token = token if token is not None else (env_token or cfg.core.token)
    # Only wire up refresh when using the persisted config token (not HC_TOKEN override).
    using_config_token = token is None and not env_token

    host = os.getenv("HC_HOST") or cfg.core.host
    port_raw = (os.getenv("HC_PORT") or "").strip()
    port = int(port_raw) if port_raw else int(cfg.core.port)
    auth = os.getenv("HC_AUTH") or cfg.core.auth

    base_url = f"http://{host}:{port}"
    client = HCClient(
        base_url=base_url,
        token=effective_token,
        verify_ssl=cfg.core.verify_ssl,
        auth=auth,
        refresh_token=cfg.core.refresh_token if using_config_token else "",
        on_token_refreshed=_make_refresh_callback(cfg) if using_config_token else None,
    )
    return _mute_auth_hints(client) if silent else client


def require_client(console: Console, *, silent: bool = False) -> HCClient:
    """
    Единая точка сборки API-клиента:
    - host/port: HC_HOST / HC_PORT или config
    - токен: HC_TOKEN env > config
    - auth: HC_AUTH или config
    """
    cfg = Config.load()
    env_token = os.getenv("HC_TOKEN")
    token = env_token or cfg.core.token
    host = os.getenv("HC_HOST") or cfg.core.host
    if not host.strip() or not (token or "").strip():
        console.print("[red]Ошибка:[/red] Не настроено подключение к Core.")
        console.print("  [bold]hc setup[/bold]        — мастер первого запуска [dim](рекомендуется)[/dim]")
        console.print("  [bold]hc connect ...[/bold]  — подключиться вручную")
        console.print("  [dim]Или задай HC_HOST / HC_TOKEN через env.[/dim]")
        raise typer.Exit(code=1)

    return client_from_config(cfg, token=token, silent=silent)
