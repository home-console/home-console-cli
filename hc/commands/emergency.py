"""
hc emergency — прямой доступ к ядру без API.

Работает когда Core не запущен или API недоступен.
Читает DB напрямую через SQLite (bcrypt для паролей).

Аналог: войти как root в Linux TTY когда всё остальное упало.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hc.core_source import (
    get_core_source_from_repo,
    get_core_source_local,
    resolve_workspace_root,
)
from hc.emergency_db import (
    inspect_storage,
    list_api_keys,
    list_sessions,
    list_users,
    reset_password,
    resolve_db_path,
    revoke_all_user_sessions,
)


def _resolve_core_root(console: Console) -> Path:
    repo = resolve_workspace_root()
    if repo:
        src = get_core_source_from_repo(repo)
        if src:
            return Path(src.path)
    src = get_core_source_local()
    if src:
        return Path(src.path)
    console.print(
        "[red]Ошибка:[/red] не найден core-runtime-service.\n"
        "Укажи путь явно: [bold]hc emergency --core-path /path/to/core-runtime-service[/bold]"
    )
    raise typer.Exit(code=1)


def _get_db(console: Console, core_path: Path | None) -> tuple[Path, Path]:
    """Вернуть (core_root, db_path). Выйти с ошибкой если не найдено."""
    core_root = core_path.resolve() if core_path else _resolve_core_root(console)
    try:
        db_path = resolve_db_path(core_root)
    except Exception as e:
        console.print(f"[red]Ошибка определения пути к БД: {e}[/red]")
        raise typer.Exit(code=1)
    return core_root, db_path


def register(app: typer.Typer) -> None:
    emergency_app = typer.Typer(
        help=(
            "Emergency-доступ к ядру без API.\n\n"
            "Работает когда Core не запущен. Читает SQLite напрямую.\n"
            "Аналог: войти как root в TTY когда API мёртв."
        ),
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    core_path_opt = typer.Option(
        None,
        "--core-path",
        help="Путь к core-runtime-service (если не в монорепо)",
        show_default=False,
    )

    @emergency_app.command("inspect")
    def inspect(
        core_path: Path | None = core_path_opt,
    ) -> None:
        """Показать состояние БД: пользователи, сессии, API-ключи, namespace."""
        console = Console()
        _, db_path = _get_db(console, core_path)

        console.print(f"\n[dim]БД:[/dim] {db_path}\n")

        # Namespace overview
        try:
            ns_counts = inspect_storage(db_path)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)

        ns_table = Table(title="Storage namespaces", show_header=True)
        ns_table.add_column("Namespace", style="bold")
        ns_table.add_column("Записей", justify="right")
        for ns, cnt in ns_counts.items():
            ns_table.add_row(ns, str(cnt))
        console.print(ns_table)

        # Users
        users = list_users(db_path)
        if users:
            u_table = Table(title="Users (auth_users)", show_header=True)
            u_table.add_column("user_id", style="bold")
            u_table.add_column("username")
            u_table.add_column("admin")
            u_table.add_column("password")
            for u in users:
                u_table.add_row(
                    u["user_id"],
                    u["username"] or "—",
                    "[green]yes[/green]" if u["is_admin"] else "no",
                    "[green]set[/green]" if u["has_password"] else "[red]not set[/red]",
                )
            console.print(u_table)

        # Sessions
        sessions = list_sessions(db_path)
        if sessions:
            s_table = Table(title=f"Sessions ({len(sessions)} total)", show_header=True)
            s_table.add_column("session_id")
            s_table.add_column("user_id")
            s_table.add_column("expires_at")
            for s in sessions[:20]:
                s_table.add_row(
                    s["session_id"],
                    s["user_id"],
                    str(s.get("expires_at", "—")),
                )
            if len(sessions) > 20:
                console.print(f"[dim]…и ещё {len(sessions) - 20} сессий[/dim]")
            console.print(s_table)

        # API keys
        api_keys = list_api_keys(db_path)
        if api_keys:
            k_table = Table(title="API Keys", show_header=True)
            k_table.add_column("key_id")
            k_table.add_column("name")
            k_table.add_column("user_id")
            k_table.add_column("revoked")
            for k in api_keys:
                k_table.add_row(
                    k["key_id"][:16] + "…",
                    k["name"] or "—",
                    k["user_id"],
                    "[red]yes[/red]" if k["revoked"] else "no",
                )
            console.print(k_table)

    @emergency_app.command("reset-admin")
    def reset_admin(
        user_id: str = typer.Option(
            "admin", "--user", "-u", help="ID пользователя (дефолт: admin)"
        ),
        password: str | None = typer.Option(
            None, "--password", "-p", help="Новый пароль (если не задан — запросит интерактивно)"
        ),
        revoke_sessions: bool = typer.Option(
            True, "--revoke-sessions/--keep-sessions",
            help="Инвалидировать все сессии пользователя после сброса"
        ),
        core_path: Path | None = core_path_opt,
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
    ) -> None:
        """Сбросить пароль пользователя напрямую в БД (без API).

        Использует bcrypt — тот же алгоритм что и Core.
        После сброса рекомендуется перезапустить Core для очистки in-memory кэшей.
        """
        console = Console()
        _, db_path = _get_db(console, core_path)

        # Проверяем что пользователь существует
        try:
            users = list_users(db_path)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)

        target = next((u for u in users if u["user_id"] == user_id), None)
        if target is None:
            known = [u["user_id"] for u in users]
            console.print(
                f"[red]Пользователь {user_id!r} не найден.[/red]\n"
                f"Известные: {', '.join(known) or '(нет пользователей)'}"
            )
            raise typer.Exit(code=1)

        console.print(
            Panel(
                f"[bold]user_id:[/bold] {target['user_id']}\n"
                f"[bold]username:[/bold] {target['username'] or '—'}\n"
                f"[bold]is_admin:[/bold] {'yes' if target['is_admin'] else 'no'}\n"
                f"[bold]БД:[/bold] {db_path}",
                title="[yellow]Emergency reset пароля[/yellow]",
                expand=False,
            )
        )

        if password is None:
            import getpass
            password = getpass.getpass(f"Новый пароль для {user_id!r}: ")
            confirm = getpass.getpass("Подтверди пароль: ")
            if password != confirm:
                console.print("[red]Пароли не совпадают.[/red]")
                raise typer.Exit(code=1)

        if not password:
            console.print("[red]Ошибка: пароль не может быть пустым.[/red]")
            raise typer.Exit(code=1)

        if not yes:
            confirmed = typer.confirm(
                f"Сбросить пароль {user_id!r}? Core должен быть ОСТАНОВЛЕН для корректной работы.",
                default=False,
            )
            if not confirmed:
                console.print("[dim]Отменено.[/dim]")
                raise typer.Exit(code=0)

        try:
            reset_password(db_path, user_id, password)
        except ValueError as e:
            console.print(f"[red]Ошибка: {e}[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]✓[/green] Пароль {user_id!r} обновлён в БД.")

        if revoke_sessions:
            removed = revoke_all_user_sessions(db_path, user_id)
            if removed:
                console.print(f"[green]✓[/green] Инвалидировано сессий: {removed}.")

        console.print(
            "[dim]Следующий шаг: запусти Core (`hc core up`) — "
            "он подхватит новый хеш из БД.[/dim]"
        )

    @emergency_app.command("list-users")
    def cmd_list_users(core_path: Path | None = core_path_opt) -> None:
        """Показать список пользователей напрямую из БД."""
        console = Console()
        _, db_path = _get_db(console, core_path)
        try:
            users = list_users(db_path)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)

        if not users:
            console.print("[yellow]Пользователей нет.[/yellow]")
            return

        table = Table(title="Users")
        table.add_column("user_id", style="bold")
        table.add_column("username")
        table.add_column("admin")
        table.add_column("password")
        for u in users:
            table.add_row(
                u["user_id"],
                u["username"] or "—",
                "[green]yes[/green]" if u["is_admin"] else "no",
                "[green]set[/green]" if u["has_password"] else "[red]not set[/red]",
            )
        console.print(table)

    @emergency_app.command("revoke-sessions")
    def cmd_revoke_sessions(
        user_id: str = typer.Argument(..., help="ID пользователя"),
        core_path: Path | None = core_path_opt,
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
    ) -> None:
        """Инвалидировать все сессии пользователя напрямую в БД."""
        console = Console()
        _, db_path = _get_db(console, core_path)

        if not yes:
            confirmed = typer.confirm(f"Удалить все сессии {user_id!r}?", default=False)
            if not confirmed:
                console.print("[dim]Отменено.[/dim]")
                raise typer.Exit(code=0)

        try:
            removed = revoke_all_user_sessions(db_path, user_id)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1)

        if removed:
            console.print(f"[green]✓[/green] Удалено сессий: {removed}.")
        else:
            console.print(f"[yellow]Активных сессий для {user_id!r} не найдено.[/yellow]")

    app.add_typer(emergency_app, name="emergency")
