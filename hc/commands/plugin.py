from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from hc.client import HCClient
from hc.config import Config


def _status_cell(status: str) -> Text:
    s = status.lower()
    t = Text(status)
    if s in {"running", "ok"}:
        t.stylize("green")
    elif s in {"stopped", "paused"}:
        t.stylize("yellow")
    else:
        t.stylize("red")
    return t


def register(app: typer.Typer) -> None:
    plugin_app = typer.Typer(
        help="Управление плагинами",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @plugin_app.command("list")
    def list_plugins() -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)

        plugins = anyio.run(client.get_plugins)
        if plugins is None:
            raise typer.Exit(code=1)

        table = Table(title="Plugins")
        table.add_column("Плагин", style="bold")
        table.add_column("Версия")
        table.add_column("Статус")
        table.add_column("Режим")
        table.add_column("Uptime")

        for p in plugins:
            table.add_row(
                str(p.get("name", "")),
                str(p.get("version", "")),
                _status_cell(str(p.get("status", ""))),
                str(p.get("mode", "")),
                str(p.get("uptime", "")),
            )
        console.print(table)

    @plugin_app.command("start")
    def start(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)
        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)
        data = anyio.run(client.start_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} запущен")

    @plugin_app.command("stop")
    def stop(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)
        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)
        data = anyio.run(client.stop_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} остановлен")

    @plugin_app.command("info")
    def info(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)
        data = anyio.run(client.get_plugin_info, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(Panel.fit(Pretty(data, expand_all=True), title=f"Plugin: {name}"))

    app.add_typer(plugin_app, name="plugin")

