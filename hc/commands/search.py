from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.client import HCClient
from hc.config import Config


def register(app: typer.Typer) -> None:
    @app.command("search")
    def search(query: str = typer.Argument(..., help="Строка поиска в marketplace")) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)

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

