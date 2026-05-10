from __future__ import annotations

from pathlib import Path

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client
from hc.marketplace_operation import parse_marketplace_operation_view


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

    @marketplace_app.command("install-archive")
    def install_archive(
        archive: Path | None = typer.Argument(
            None,
            metavar="ARCHIVE",
            exists=False,
            file_okay=True,
            dir_okay=False,
            readable=True,
            path_type=Path,
            help="Локальный .zip или .tar.gz — загрузка на Core (по умолчанию)",
        ),
        sha256_opt: str | None = typer.Option(None, "--sha256", help="Ожидаемый SHA256 архива"),
        server_path: str | None = typer.Option(
            None,
            "--server-path",
            help="Если архив уже на стороне Core — путь в ФС процесса (без multipart)",
        ),
    ) -> None:
        """Установка из архива: по умолчанию multipart upload, иначе устаревший JSON с ``archive_path``.

        Если Core в Docker, обычно достаточно передать локальный ARCHIVE — файл уйдёт в ``install-upload``.
        """
        console = Console()

        if archive is None and not server_path:
            console.print(
                "[yellow]Укажите локальный файл ARCHIVE или --server-path /path/on/core[/yellow]"
            )
            raise typer.Exit(code=2)
        if archive is not None and server_path:
            console.print("[red]Выберите либо ARCHIVE (загрузка), либо --server-path, не оба[/red]")
            raise typer.Exit(code=2)
        if archive is not None and not archive.is_file():
            console.print(f"[red]Не файл или не найден: {archive}[/red]")
            raise typer.Exit(code=2)

        client = require_client(console)

        async def _call() -> None:
            if archive is not None:
                raw = await client.admin_marketplace_install_upload_archive(
                    archive, sha256=sha256_opt
                )
            else:
                raw = await client.admin_marketplace_install_archive(
                    server_path or "",
                    sha256=sha256_opt,
                )
            if raw is None:
                console.print(
                    "[red]Нет ответа от Core (сеть, 403 или версия без endpoint).[/red]"
                )
                raise typer.Exit(code=1)
            view = parse_marketplace_operation_view(raw)
            if view.ok:
                console.print("[green]✓[/green] Установка прошла успешно.")
                if view.data:
                    console.print(f"  [bold]Имя:[/bold] {view.data.get('name', '—')}")
                    console.print(f"  [bold]Версия:[/bold] {view.data.get('version', '—')}")
                    if view.data.get("path"):
                        console.print(f"  [bold]Каталог:[/bold] {view.data['path']}")
                raise typer.Exit(code=0)
            if view.user_message:
                console.print(f"[red]{view.user_message}[/red]")
            else:
                console.print(f"[red]{view.error or 'Неизвестная ошибка'}[/red]")
            if view.error_stage:
                console.print(f"[dim]Стадия: {view.error_stage}[/dim]")
            if view.user_message and view.error and view.user_message.strip() != view.error.strip():
                console.print(f"[dim]Технич.: {view.error}[/dim]")
            raise typer.Exit(code=1)

        anyio.run(_call)

    app.add_typer(marketplace_app, name="marketplace")
