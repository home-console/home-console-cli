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

    @module_app.command("inspect")
    def inspect(name: str = typer.Argument(..., help="Имя модуля")) -> None:
        """Показать детали модуля: статус, uptime, зависимости."""
        from rich.panel import Panel
        from rich.pretty import Pretty
        console = Console()
        client = _client(console)
        modules = anyio.run(client.get_modules)
        if modules is None:
            raise typer.Exit(code=1)

        target = next((m for m in modules if m.get("name", "") == name), None)
        if target is None:
            known = [m.get("name", "") for m in modules]
            console.print(f"[red]Модуль {name!r} не найден.[/red]")
            console.print(f"Доступные: {', '.join(known)}")
            raise typer.Exit(code=1)

        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Поле", style="bold dim", width=16)
        table.add_column("Значение", overflow="fold")

        priority_keys = ["name", "status", "required", "uptime", "description"]
        for key in priority_keys:
            val = target.get(key)
            if val is None:
                continue
            if key == "status":
                color = "green" if str(val).lower() in {"running", "ok"} else "yellow"
                table.add_row(key, f"[{color}]{val}[/{color}]")
            elif key == "required":
                table.add_row(key, "required" if bool(val) else "optional")
            else:
                table.add_row(key, str(val))

        extra = {k: v for k, v in target.items() if k not in priority_keys and v is not None}
        console.print(Panel(table, title=f"[bold]Module: {name}[/bold]", expand=False))
        if extra:
            console.print(Panel(Pretty(extra, expand_all=True), title="metadata", expand=False))

    app.add_typer(module_app, name="module")

