from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hc.client import HCClient
from hc.config import Config
from hc.commands._client_helpers import require_client


def _status_cell(status: str) -> Text:
    s = status.lower()
    t = Text(status)
    if s in {"running", "ok"}:
        t.stylize("green")
    elif s in {"stopped"}:
        t.stylize("yellow")
    else:
        t.stylize("red")
    return t


def register(app: typer.Typer) -> None:
    module_app = typer.Typer(
        help="Системные модули",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    def _client(console: Console) -> HCClient:
        return require_client(console)

    @module_app.command("list")
    def list_modules() -> None:
        console = Console()
        client = _client(console)
        modules = anyio.run(client.get_modules)
        if modules is None:
            raise typer.Exit(code=1)

        table = Table(title="Modules")
        table.add_column("Модуль", style="bold")
        table.add_column("Статус")
        table.add_column("Required")
        table.add_column("Uptime")

        for m in modules:
            table.add_row(
                str(m.get("name", "")),
                _status_cell(str(m.get("status", ""))),
                "required" if bool(m.get("required", False)) else "optional",
                str(m.get("uptime", "")),
            )
        console.print(table)

    @module_app.command("status")
    def status() -> None:
        console = Console()
        client = _client(console)
        modules = anyio.run(client.get_modules)
        if modules is None:
            raise typer.Exit(code=1)
        total = len(modules)
        ok = sum(1 for m in modules if str(m.get("status", "")).lower() in {"running", "ok"})
        console.print(f"[green]✓[/green] Модулей: {ok} / {total}")

    def _require_supported(console: Console, res: object | None) -> None:
        # Если endpoint не существует, `_request_json_optional` вернёт None без ошибок.
        if res is None:
            console.print("[red]Ошибка:[/red] текущая версия Core не поддерживает управление модулями через API.")
            console.print("[dim]Подсказка:[/dim] обнови Core или используй локальный docker-режим для рестартов.")
            raise typer.Exit(code=1)

    @module_app.command("start")
    def start(name: str = typer.Argument(..., help="Имя модуля")) -> None:
        console = Console()
        client = _client(console)
        res = anyio.run(client.start_module, name)
        _require_supported(console, res)
        console.print(f"[green]✓[/green] {name} запущен")

    @module_app.command("stop")
    def stop(name: str = typer.Argument(..., help="Имя модуля")) -> None:
        console = Console()
        client = _client(console)
        res = anyio.run(client.stop_module, name)
        _require_supported(console, res)
        console.print(f"[green]✓[/green] {name} остановлен")

    @module_app.command("restart")
    def restart(name: str = typer.Argument(..., help="Имя модуля")) -> None:
        console = Console()
        client = _client(console)
        res = anyio.run(client.restart_module, name)
        _require_supported(console, res)
        console.print(f"[green]✓[/green] {name} перезапущен")

    app.add_typer(module_app, name="module")

