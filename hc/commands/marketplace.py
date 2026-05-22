from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import anyio
import httpx
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client
from hc.marketplace_operation import parse_marketplace_operation_view


def _print_marketplace_op(
    console: Console, raw: dict | None, *, ok_title: str
) -> None:
    if raw is None:
        console.print("[red]Нет ответа от Core.[/red]")
        raise typer.Exit(code=1)
    view = parse_marketplace_operation_view(raw)
    if view.ok:
        console.print(f"[green]✓[/green] {ok_title}")
        if view.data:
            console.print(f"  [bold]Имя:[/bold] {view.data.get('name', '—')}")
            ver = view.data.get("resolved_version") or view.data.get("version")
            if ver:
                console.print(f"  [bold]Версия:[/bold] {ver}")
            if view.data.get("path"):
                console.print(f"  [bold]Каталог:[/bold] {view.data['path']}")
        raise typer.Exit(code=0)
    if view.user_message:
        console.print(f"[red]{view.user_message}[/red]")
    else:
        console.print(f"[red]{view.error or 'Неизвестная ошибка'}[/red]")
    if view.error_stage:
        console.print(f"[dim]Стадия: {view.error_stage}[/dim]")
    raise typer.Exit(code=1)


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

    @marketplace_app.command("install-from-registry")
    def install_from_registry(
        plugin_name: str = typer.Argument(..., help="Имя плагина в реестре"),
        version: str | None = typer.Option(
            None, "--version", "-v", help="Версия или constraint (например 0.1.0)"
        ),
        channel: str = typer.Option("stable", "--channel", help="Канал реестра"),
        force: bool = typer.Option(
            False,
            "--force",
            help="Переустановить, если плагин уже установлен",
        ),
    ) -> None:
        """Установить плагин из маркетплейса (registry index → Core)."""
        console = Console()
        client = require_client(console)

        async def _call() -> None:
            raw = await client.admin_marketplace_install_from_registry(
                plugin_name,
                version=version,
                channel=channel,
                force_update=force,
            )
            _print_marketplace_op(console, raw, ok_title="Установка из реестра")

        anyio.run(_call)

    @marketplace_app.command("update-from-registry")
    def update_from_registry(
        plugin_name: str = typer.Argument(..., help="Имя установленного плагина"),
        version: str | None = typer.Option(
            None,
            "--version",
            "-v",
            help="Версия в реестре (та же semver OK — новый sha256)",
        ),
        channel: str = typer.Option("stable", "--channel", help="Канал реестра"),
    ) -> None:
        """Обновить установленный плагин из реестра (в т.ч. перезалитый 0.1.0)."""
        console = Console()
        client = require_client(console)

        async def _call() -> None:
            raw = await client.admin_marketplace_update_from_registry(
                plugin_name,
                version=version,
                channel=channel,
            )
            _print_marketplace_op(console, raw, ok_title="Обновление из реестра")

        anyio.run(_call)

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
            _print_marketplace_op(console, raw, ok_title="Установка прошла успешно")

        anyio.run(_call)

    @marketplace_app.command("publish-from-git")
    def publish_from_git(
        plugin: str = typer.Argument(..., help="Slug плагина в реестре"),
        ref: str = typer.Option("main", "--ref", help="Git ref: ветка, тег или sha"),
        channel: str = typer.Option("stable", "--channel", help="Канал релиза"),
        force: bool = typer.Option(
            False,
            "--force",
            help="Перезаписать существующую версию (PUT replace)",
        ),
        source_repo: str | None = typer.Option(
            None,
            "--source-repo",
            help="URL github-репо (по умолчанию берётся plugin.homepage_url)",
        ),
        changelog: str | None = typer.Option(None, "--changelog", help="Текст changelog"),
        api: str | None = typer.Option(
            None,
            "--api",
            envvar="MARKETPLACE_API_URL",
            help="URL marketplace-api (по умолчанию ENV MARKETPLACE_API_URL)",
        ),
        token: str | None = typer.Option(
            None,
            "--token",
            envvar="MARKETPLACE_PUBLISHER_TOKEN",
            help="Publisher Bearer token (по умолчанию ENV MARKETPLACE_PUBLISHER_TOKEN)",
        ),
    ) -> None:
        """Собрать и опубликовать релиз из публичного GitHub-репо.

        Никаких локальных zip/подписей — реестр сам качает архив, считает sha256
        и подписывает своим ключом.
        """
        console = Console()
        api_url = (api or "").rstrip("/")
        if not api_url:
            console.print(
                "[red]Не задан URL marketplace-api: --api или MARKETPLACE_API_URL[/red]"
            )
            raise typer.Exit(code=2)
        if not token:
            console.print(
                "[red]Не задан publisher token: --token или MARKETPLACE_PUBLISHER_TOKEN[/red]"
            )
            raise typer.Exit(code=2)

        body = {
            "ref": ref,
            "channel": channel,
            "force_replace": force,
        }
        if source_repo:
            body["source_repo"] = source_repo
        if changelog:
            body["changelog"] = changelog

        url = f"{api_url}/api/plugins/{plugin}/releases/from-git"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=120.0)
        except httpx.HTTPError as exc:
            console.print(f"[red]Сетевая ошибка: {exc}[/red]")
            raise typer.Exit(code=1)

        try:
            payload = resp.json()
        except Exception:
            payload = {"detail": resp.text[:500]}

        if resp.is_success:
            replaced = bool(payload.get("replaced"))
            action = "перезалит" if replaced else "опубликован"
            console.print(f"[green]✓[/green] {plugin} {action}")
            console.print(f"  [bold]Версия:[/bold] {payload.get('version', '—')}")
            console.print(f"  [bold]Канал:[/bold] {payload.get('channel', '—')}")
            console.print(f"  [bold]SHA256:[/bold] {payload.get('sha256', '—')}")
            if payload.get("git_sha"):
                console.print(f"  [bold]Commit:[/bold] {payload['git_sha']}")
            if payload.get("url"):
                console.print(f"  [bold]URL:[/bold] {payload['url']}")
            raise typer.Exit(code=0)

        detail = payload.get("detail") or payload
        console.print(f"[red]✗ {resp.status_code}: {detail}[/red]")
        raise typer.Exit(code=1)

    app.add_typer(marketplace_app, name="marketplace")
