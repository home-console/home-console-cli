from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    @app.command("install")
    def install(
        name: str = typer.Argument(..., help="Имя плагина"),
        version: str | None = typer.Option(None, "--version", help="Желаемая версия"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план установки без реального выполнения"),
    ) -> None:
        console = Console()
        client = require_client(console)

        async def _precheck() -> dict | None:
            idx = await client.get_marketplace_index()
            if not idx:
                return None
            cand = [p for p in idx if str(p.get("name", "")) == name]
            if not cand:
                return {"error": f"Плагин {name} не найден в marketplace"}
            if version:
                for p in cand:
                    if str(p.get("version", "")) == version:
                        return p
                return {"error": f"Версия {version} для {name} не найдена в marketplace"}
            return cand[0]

        info = anyio.run(_precheck)
        if not info:
            raise typer.Exit(code=1)
        if "error" in info:
            console.print(f"[red]Ошибка: {info['error']}[/red]")
            raise typer.Exit(code=1)

        deps = info.get("dependencies") or []
        if not isinstance(deps, list):
            deps = []

        table = Table(title="Будет установлено")
        table.add_column("Поле", style="bold")
        table.add_column("Значение", overflow="fold")
        table.add_row("Имя", str(info.get("name", name)))
        table.add_row("Версия", str(info.get("version", version or "")))
        table.add_row("Описание", str(info.get("description", "")))
        table.add_row("Зависимости", ", ".join(map(str, deps)) if deps else "-")
        console.print(table)

        if dry_run:
            console.print("[yellow]Dry run:[/yellow] установка не выполнена")
            raise typer.Exit(code=0)

        if not typer.confirm("Install?", default=False):
            raise typer.Exit(code=0)

        async def _run_install() -> None:
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Установка...", total=None)
                async for msg in client.install_plugin(name):
                    progress.update(task, description=msg or "Установка...")

        anyio.run(_run_install)
        console.print(f"[green]✓[/green] {name} {info.get('version', '')} установлен и запущен")

        # Обновить кеш CLI-команд плагинов
        try:
            from hc.plugin_cli_loader import refresh_cache
            refresh_cache(client)
        except Exception:
            pass

