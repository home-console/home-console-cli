"""hc cloud — управление облачными хранилищами (cloud_sync plugin)."""
from __future__ import annotations

from pathlib import Path

import anyio
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    cloud_app = typer.Typer(
        help="Работа с облачными хранилищами через плагин cloud_sync",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    # ------------------------------------------------------------------ providers

    @cloud_app.command("providers")
    def providers(
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Список подключённых облачных провайдеров и их статус."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "cloud_sync.providers", None)
        if data is None:
            console.print("[red]Ошибка: cloud_sync недоступен.[/red]")
            raise typer.Exit(code=1)

        items = data.get("providers", []) if isinstance(data, dict) else []

        if json_out:
            from hc.json_output import print_json
            print_json({"ok": True, "providers": items})
            return

        if not items:
            console.print("[dim]Нет настроенных провайдеров.[/dim]")
            return

        table = Table(title="Cloud провайдеры")
        table.add_column("Провайдер", style="bold")
        table.add_column("Статус")
        for p in items:
            ok = p.get("connected", False)
            table.add_row(
                str(p.get("provider", "?")),
                Text("connected", style="green") if ok else Text("error", style="red"),
            )
        console.print(table)

    # ------------------------------------------------------------------ list

    @cloud_app.command("list")
    def list_files(
        provider: str = typer.Argument(..., help="Провайдер: yandex_disk | google_drive | dropbox"),
        remote_path: str = typer.Argument("", help="Путь в облаке (по умолчанию корень)"),
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Список файлов в облачном хранилище.

        Примеры:
          hc cloud list yandex_disk
          hc cloud list yandex_disk /backups
        """
        console = Console()
        client = require_client(console)
        kwargs = {"provider": provider, "remote_path": remote_path}
        data = anyio.run(client.call_service, "cloud_sync.list", kwargs)

        if data is None:
            console.print(f"[red]Ошибка: не удалось получить список файлов.[/red]")
            raise typer.Exit(code=1)

        files = data.get("files", []) if isinstance(data, dict) else []
        path  = data.get("path", remote_path) if isinstance(data, dict) else remote_path

        if json_out:
            from hc.json_output import print_json
            print_json({"ok": True, "provider": provider, "path": path, "files": files, "count": len(files)})
            return

        if not files:
            console.print(f"[dim]{provider}:{path or '/'} — пусто[/dim]")
            return

        table = Table(title=f"{provider}:{path or '/'} ({len(files)} файлов)")
        table.add_column("Имя", style="bold")
        table.add_column("Тип",  style="dim")
        table.add_column("Размер", justify="right")
        table.add_column("Изменён", style="dim")

        for f in sorted(files, key=lambda x: (not x.get("is_dir"), x.get("name", ""))):
            size = f.get("size", 0) or 0
            size_str = _fmt_size(size) if not f.get("is_dir") else "—"
            icon = "📁" if f.get("is_dir") else "📄"
            table.add_row(
                f"{icon} {f.get('name', '?')}",
                "dir" if f.get("is_dir") else (f.get("mime_type") or "file"),
                size_str,
                str(f.get("modified", ""))[:16],
            )
        console.print(table)

    # ------------------------------------------------------------------ upload

    @cloud_app.command("upload")
    def upload(
        provider: str    = typer.Argument(..., help="Провайдер: yandex_disk | google_drive | dropbox"),
        local_path: Path = typer.Argument(..., help="Локальный файл"),
        remote_path: str = typer.Argument(..., help="Путь в облаке (напр. /backups/file.txt)"),
    ) -> None:
        """Выгрузить файл в облачное хранилище.

        Примеры:
          hc cloud upload yandex_disk ./dump.sql /backups/dump.sql
          hc cloud upload google_drive report.pdf /reports/2026/report.pdf
        """
        console = Console()

        if not local_path.is_file():
            console.print(f"[red]Файл не найден: {local_path}[/red]")
            raise typer.Exit(code=2)

        size = local_path.stat().st_size
        console.print(f"[dim]Загрузка {local_path.name} ({_fmt_size(size)}) → {provider}:{remote_path}…[/dim]")

        data_bytes = local_path.read_bytes()
        client = require_client(console)

        kwargs = {"provider": provider, "data": data_bytes.decode("latin-1"), "remote_path": remote_path}
        result = anyio.run(client.call_service, "cloud_sync.upload", kwargs)

        if result is None:
            console.print("[red]Ошибка: нет ответа от cloud_sync.[/red]")
            raise typer.Exit(code=1)

        if result.get("success") or result.get("ok"):
            uploaded_size = result.get("size", size)
            console.print(
                f"[green]✓[/green] Загружено: [bold]{provider}:{remote_path}[/bold] "
                f"({_fmt_size(uploaded_size)})"
            )
        else:
            err = result.get("error", "unknown")
            console.print(f"[red]Ошибка загрузки: {err}[/red]")
            raise typer.Exit(code=1)

    # ------------------------------------------------------------------ download

    @cloud_app.command("download")
    def download(
        provider: str    = typer.Argument(..., help="Провайдер: yandex_disk | google_drive | dropbox"),
        remote_path: str = typer.Argument(..., help="Путь в облаке"),
        local_path: Path = typer.Argument(..., help="Куда сохранить"),
        force: bool      = typer.Option(False, "--force", "-f", help="Перезаписать если файл существует"),
    ) -> None:
        """Скачать файл из облачного хранилища.

        Примеры:
          hc cloud download yandex_disk /backups/dump.sql ./dump.sql
          hc cloud download google_drive /reports/q1.pdf ./q1.pdf --force
        """
        console = Console()

        if local_path.exists() and not force:
            console.print(f"[yellow]{local_path} уже существует.[/yellow] Используй --force для перезаписи.")
            raise typer.Exit(code=1)

        console.print(f"[dim]Скачивание {provider}:{remote_path} → {local_path}…[/dim]")

        client = require_client(console)
        kwargs = {"provider": provider, "remote_path": remote_path}
        result = anyio.run(client.call_service, "cloud_sync.download", kwargs)

        if result is None:
            console.print("[red]Ошибка: нет ответа от cloud_sync.[/red]")
            raise typer.Exit(code=1)

        raw = result.get("data") if isinstance(result, dict) else None
        if raw is None:
            console.print(f"[red]Ошибка: пустые данные в ответе.[/red]")
            raise typer.Exit(code=1)

        # data передаётся как latin-1 строка (round-trip с upload)
        data_bytes = raw.encode("latin-1") if isinstance(raw, str) else bytes(raw)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data_bytes)

        console.print(
            f"[green]✓[/green] Сохранено: [bold]{local_path}[/bold] "
            f"({_fmt_size(len(data_bytes))})"
        )

    # ------------------------------------------------------------------ delete

    @cloud_app.command("delete")
    def delete(
        provider: str    = typer.Argument(..., help="Провайдер: yandex_disk | google_drive | dropbox"),
        remote_path: str = typer.Argument(..., help="Путь к файлу в облаке"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
    ) -> None:
        """Удалить файл из облачного хранилища.

        Примеры:
          hc cloud delete yandex_disk /backups/old_dump.sql
        """
        console = Console()
        if not yes:
            confirmed = typer.confirm(f"Удалить {provider}:{remote_path}?", default=False)
            if not confirmed:
                console.print("[dim]Отменено.[/dim]")
                raise typer.Exit(code=0)

        client = require_client(console)
        kwargs = {"provider": provider, "remote_path": remote_path}
        result = anyio.run(client.call_service, "cloud_sync.delete", kwargs)

        if result is None:
            console.print("[red]Ошибка: нет ответа от cloud_sync.[/red]")
            raise typer.Exit(code=1)

        if result.get("success"):
            console.print(f"[green]✓[/green] Удалено: [bold]{provider}:{remote_path}[/bold]")
        else:
            console.print(f"[red]Ошибка удаления: {result.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

    app.add_typer(cloud_app, name="cloud")


# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TB"
