"""hc plugin validate — проверить структуру плагина перед публикацией."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hc.plugin_validator import validate_plugin


def register_validate(plugin_app: typer.Typer) -> None:
    @plugin_app.command("validate")
    def validate(
        path: Path = typer.Argument(..., help="Путь к папке плагина"),
        strict: bool = typer.Option(
            False, "--strict", help="Выход с ошибкой если есть предупреждения"
        ),
    ) -> None:
        """Проверить структуру плагина: plugin.json + AST-анализ plugin.py.

        Примеры:
          hc plugin validate ./my_plugin
          hc plugin validate /home/user/plugins/oauth_yandex --strict
        """
        console = Console()
        plugin_path = path.resolve()

        result = validate_plugin(plugin_path)

        if not result.errors and not result.warnings:
            console.print(f"[green]✓[/green] [bold]{plugin_path.name}[/bold] — всё в порядке")
            return

        table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
        table.add_column("Уровень", width=8)
        table.add_column("Сообщение")

        for msg in result.errors:
            table.add_row(Text("ОШИБКА", style="bold red"), msg)
        for msg in result.warnings:
            table.add_row(Text("WARN", style="yellow"), msg)

        console.print(f"\n[bold]{plugin_path.name}[/bold]")
        console.print(table)
        console.print()

        if result.errors:
            console.print(
                f"[red]✗ {len(result.errors)} ошибок"
                + (f", {len(result.warnings)} предупреждений" if result.warnings else "")
                + "[/red]"
            )
            raise typer.Exit(code=1)

        if result.warnings:
            console.print(f"[yellow]⚠ {len(result.warnings)} предупреждений[/yellow]")
            if strict:
                raise typer.Exit(code=1)
