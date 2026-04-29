from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hc.client import HCClient
from hc.config import Config


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
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)
        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        return HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)

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

    app.add_typer(module_app, name="module")

