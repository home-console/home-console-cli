from __future__ import annotations

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    marketplace_app = typer.Typer(
        help="Маркетплейс плагинов",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @marketplace_app.command("updates")
    def updates() -> None:
        """Плагины с доступными обновлениями."""
        console = Console()
        client = require_client(console)
        available = anyio.run(client.get_marketplace_updates)
        if not available:
            console.print("[green]✓[/green] Все плагины актуальны.")
            return
        table = Table(title="Доступные обновления")
        table.add_column("Плагин", style="bold")
        table.add_column("Текущая")
        table.add_column("Новая", style="green")
        for item in available:
            table.add_row(item["name"], item["current"], item["latest"])
        console.print(table)
        console.print("\n[dim]Обновить:[/dim] hc install <имя>")

    app.add_typer(marketplace_app, name="marketplace")
