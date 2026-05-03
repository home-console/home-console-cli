from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    @app.command("status")
    def status() -> None:
        console = Console()
        client = require_client(console)

        async def _run() -> tuple[dict | None, int | None, tuple[int, int] | None]:
            health = await client.admin_status()
            if not health:
                health = await client.health()
            plugins = await client.get_plugins()
            modules = await client.get_modules()
            active_plugins = None
            if isinstance(plugins, list):
                active_plugins = sum(1 for p in plugins if str(p.get("status", "")).lower() == "running")
            modules_stat = None
            if isinstance(modules, list):
                total = len(modules)
                ok = sum(1 for m in modules if str(m.get("status", "")).lower() in {"running", "ok"})
                modules_stat = (ok, total)
            return health, active_plugins, modules_stat

        health, active_plugins, modules_stat = anyio.run(_run)
        if not health:
            raise typer.Exit(code=1)

        version = str(health.get("version", "unknown"))
        status_value = str(health.get("status", "running"))
        uptime = str(health.get("uptime", "unknown"))

        status_text = Text(status_value)
        if status_value.lower() in {"running", "ok"}:
            status_text.stylize("green")
            status_text = Text("✓ ") + status_text
        else:
            status_text.stylize("red")

        lines: list[Text] = [
            Text.assemble(("Версия:   ", "bold"), (version, "")),
            Text.assemble(("Статус:   ", "bold"), status_text),
        ]
        if active_plugins is not None:
            lines.append(Text.assemble(("Плагинов: ", "bold"), (f"{active_plugins} активных", "")))
        if modules_stat is not None:
            ok, total = modules_stat
            lines.append(Text.assemble(("Модулей:  ", "bold"), (f"{ok} / {total}", "")))
        lines.append(Text.assemble(("Uptime:   ", "bold"), (uptime, "")))

        body = Text("\n").join(lines)
        console.print(Panel(body, title="HomeConsole", border_style="cyan"))

