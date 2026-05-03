from __future__ import annotations

import os
import re

import anyio
import typer
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client

_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR)\s")


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


def _style_line(line: str) -> Text:
    m = _LEVEL_RE.search(line)
    if not m:
        return Text(line)
    level = m.group(1)
    t = Text(line)
    if level == "DEBUG":
        t.stylize("grey50")
    elif level == "INFO":
        t.stylize("white")
    elif level == "WARNING":
        t.stylize("yellow")
    elif level == "ERROR":
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
        client = require_client(console)

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
        client = require_client(console)
        data = anyio.run(client.start_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} запущен")

    @plugin_app.command("stop")
    def stop(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.stop_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} остановлен")

    @plugin_app.command("restart")
    def restart(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        client = require_client(console)
        stop_res = anyio.run(client.stop_plugin, name)
        if stop_res is None:
            raise typer.Exit(code=1)
        start_res = anyio.run(client.start_plugin, name)
        if start_res is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} перезапущен")

    @plugin_app.command("logs")
    def logs(
        name: str = typer.Argument(..., help="Имя плагина"),
        follow: bool = typer.Option(False, "--follow", help="Следить за логами (stream)"),
        level: str | None = typer.Option(
            None, "--level", help="debug|info|warning|error (локальная фильтрация)"
        ),
    ) -> None:
        console = Console()
        client = require_client(console)
        wanted = level.upper() if level else None

        async def _run() -> int:
            count = 0
            async for line in client.stream_logs(module=name, follow=follow):
                if wanted and wanted not in line.upper():
                    continue
                console.print(_style_line(line))
                count += 1
                if not follow and count >= 100:
                    break
            return 0

        anyio.run(_run)

    @plugin_app.command("info")
    def info(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.get_plugin_info, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(Panel.fit(Pretty(data, expand_all=True), title=f"Plugin: {name}"))

    app.add_typer(plugin_app, name="plugin")

