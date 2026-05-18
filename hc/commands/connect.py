from __future__ import annotations

import getpass

import typer
from rich.console import Console

from hc.client import HCClient
from hc.config import Config


def connect_and_save(host: str, port: int, token: str, auth: str = "auto") -> dict | None:
    """Общая логика для `hc connect` и `hc setup`."""
    console = Console()
    cfg = Config.load()
    base_url = f"http://{host}:{port}"
    client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl, auth=auth)

    import anyio

    # Для connect нам важно только понять, что Core отвечает и токен принят.
    health = anyio.run(client.admin_status)
    if not health:
        health = anyio.run(client.health)
    if not health:
        return None

    ver = anyio.run(client.core_version)
    if ver:
        v = str(ver.get("version", "")).strip()
        if v:
            console.print(f"[dim]Core version:[/dim] {v}")

    cfg.core.host = host
    cfg.core.port = port
    cfg.core.token = token
    cfg.core.auth = auth
    cfg.save()
    return health


def register(app: typer.Typer) -> None:
    @app.command("connect")
    def connect(
        host: str = typer.Argument(..., help="Хост CoreRuntime (например localhost)"),
        port: int = typer.Option(8080, "--port", help="Порт CoreRuntime"),
        token: str | None = typer.Option(None, "--token", help="JWT токен"),
        auth: str = typer.Option("auto", "--auth", help="auto|bearer|api-key"),
    ) -> None:
        console = Console()
        if token is None:
            token = getpass.getpass("Token: ").strip()
        if not token:
            console.print("[red]Ошибка: токен не задан[/red]")
            raise typer.Exit(code=1)

        health = connect_and_save(host=host, port=port, token=token, auth=auth)
        if not health:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] Подключено к HomeConsole на {host}:{port}")

