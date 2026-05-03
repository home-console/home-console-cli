from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    @app.command("search")
    def search(query: str = typer.Argument(..., help="Строка поиска в marketplace")) -> None:
        console = Console()
        client = require_client(console)

        async def _run() -> list[dict] | None:
            return await client.search_marketplace(query)

        items = anyio.run(_run)
        if items is None:
            raise typer.Exit(code=1)

        table = Table(title="Marketplace search", show_lines=False)
        table.add_column("Имя", style="bold")
        table.add_column("Версия")
        table.add_column("Описание", overflow="fold")
        table.add_column("Автор")
        table.add_column("Скачиваний", justify="right")

        for it in items:
            table.add_row(
                str(it.get("name", "")),
                str(it.get("version", "")),
                str(it.get("description", "")),
                str(it.get("author", "")),
                str(it.get("downloads", "")),
            )
        console.print(table)

