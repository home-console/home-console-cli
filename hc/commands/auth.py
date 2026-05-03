from __future__ import annotations

import getpass

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.client import HCClient
from hc.config import Config
from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    auth_app = typer.Typer(
        help="Аутентификация (JWT) и управление сессией",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @auth_app.command("login")
    def login(
        host: str = typer.Option("localhost", "--host", help="Хост CoreRuntime"),
        port: int = typer.Option(18000, "--port", help="Порт CoreRuntime"),
        user_id: str = typer.Option(..., "--user-id", "-u", help="User ID (например admin)"),
        password: str | None = typer.Option(None, "--password", "-p", help="Пароль (не рекомендуется)"),
    ) -> None:
        """Логин по паролю и сохранение JWT в конфиг."""
        console = Console()
        if password is None:
            password = getpass.getpass("Password: ").strip()
        if not password:
            console.print("[red]Ошибка: пароль не задан[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{host}:{port}"
        client = HCClient(base_url=base_url, token="", auth="bearer")
        data, session_cookie = anyio.run(client.auth_login_full, user_id, password)
        payload = (data.get("result") if isinstance(data, dict) else None) or data
        if not isinstance(payload, dict) or "access_token" not in payload:
            console.print("[red]Ошибка: не удалось выполнить login[/red]")
            raise typer.Exit(code=1)

        token = str(payload["access_token"])
        cfg = Config.load()
        cfg.core.host = host
        cfg.core.port = port
        cfg.core.token = token
        cfg.core.refresh_token = session_cookie
        cfg.core.auth = "bearer"
        cfg.save()
        console.print("[green]✓[/green] Вошёл. Токен сохранён в конфиг.")
        console.print("Проверка: `hc status`")

    @auth_app.command("bootstrap")
    def bootstrap(host: str = typer.Option("localhost", "--host"), port: int = typer.Option(18000, "--port")) -> None:
        """Проверить initialized (первый запуск)."""
        console = Console()
        client = HCClient(base_url=f"http://{host}:{port}", token="", auth="bearer")
        data = anyio.run(client.auth_bootstrap)
        if not data:
            console.print("[red]Ошибка: не удалось получить bootstrap[/red]")
            raise typer.Exit(code=1)
        initialized = None
        if isinstance(data.get("initialized"), bool):
            initialized = bool(data["initialized"])
        elif isinstance(data.get("result"), dict) and isinstance(data["result"].get("initialized"), bool):
            initialized = bool(data["result"]["initialized"])
        console.print(f"initialized: {initialized}")

    @auth_app.command("init")
    def init(
        host: str = typer.Option("localhost", "--host"),
        port: int = typer.Option(18000, "--port"),
        user_id: str = typer.Option("admin", "--user-id", help="User ID первичного админа"),
        username: str = typer.Option("admin", "--username", help="Username первичного админа"),
    ) -> None:
        """Первичная инициализация (создание первого админа)."""
        console = Console()
        base_url = f"http://{host}:{port}"
        client = HCClient(base_url=base_url, token="", auth="bearer")

        boot = anyio.run(client.auth_bootstrap)
        if boot:
            initd = None
            if isinstance(boot.get("initialized"), bool):
                initd = bool(boot["initialized"])
            elif isinstance(boot.get("result"), dict) and isinstance(boot["result"].get("initialized"), bool):
                initd = bool(boot["result"]["initialized"])
            if initd is True:
                console.print("[yellow]Система уже инициализирована.[/yellow]")
                raise typer.Exit(code=0)

        pw1 = getpass.getpass("New admin password: ").strip()
        pw2 = getpass.getpass("Repeat password: ").strip()
        if not pw1 or pw1 != pw2:
            console.print("[red]Ошибка: пароли пустые или не совпадают[/red]")
            raise typer.Exit(code=1)

        data = anyio.run(client.auth_initialize, user_id, username, pw1)
        if not data:
            console.print("[red]Ошибка: не удалось выполнить initialize[/red]")
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] Инициализация выполнена.")
        console.print("Дальше: `hc auth login -u admin`")

    @auth_app.command("whoami")
    def whoami() -> None:
        """Проверить текущий токен и показать пользователя."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.auth_me)
        if not data:
            console.print("[red]Ошибка: не удалось получить /auth/v1/me[/red]")
            raise typer.Exit(code=1)

        table = Table(title="Whoami")
        table.add_column("Поле", style="bold")
        table.add_column("Значение", overflow="fold")
        for k, v in data.items():
            table.add_row(str(k), str(v))
        console.print(table)

    @auth_app.command("check")
    def check() -> None:
        """Проверить, что токен валиден (exit code 0/1)."""
        console = Console()
        client = require_client(console)
        me = anyio.run(client.auth_me)
        if not me:
            console.print("[red]Ошибка: не авторизован (401/403).[/red]")
            console.print("Попробуй: `hc auth login -u admin`")
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] Авторизация ок.")

    @auth_app.command("logout")
    def logout() -> None:
        """Разлогиниться: best-effort logout + очистка токена из конфига."""
        console = Console()
        cfg = Config.load()
        if not cfg.core.host.strip():
            console.print("[red]Ошибка: не настроен host. Нечего разлогинивать.[/red]")
            raise typer.Exit(code=1)

        if cfg.core.token.strip():
            client = require_client(console)
            # Cookie-based logout может быть best-effort; если не получилось — всё равно чистим конфиг.
            _ = anyio.run(client.auth_logout)

        cfg.core.token = ""
        cfg.core.refresh_token = ""
        cfg.save()
        console.print("[green]✓[/green] Токен очищен из конфига.")

    user_app = typer.Typer(help="Управление пользователями")

    @user_app.command("list")
    def user_list() -> None:
        """Список всех пользователей."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.list_users)
        if data is None:
            console.print("[red]Ошибка: не удалось получить список пользователей[/red]")
            raise typer.Exit(code=1)
        if isinstance(data, list):
            raw = None
        else:
            raw = data.get("users") or data.get("items")
        if raw is None and isinstance(data, list):
            users = data
        elif isinstance(raw, list):
            users = raw
        else:
            users = []
        table = Table(title="Пользователи")
        table.add_column("user_id", style="bold")
        table.add_column("username")
        table.add_column("admin")
        table.add_column("scopes")
        table.add_column("created_at")
        for u in users:
            if not isinstance(u, dict):
                continue
            scopes_val = u.get("scopes") or []
            if isinstance(scopes_val, list):
                scopes_str = ", ".join(str(x) for x in scopes_val)
            else:
                scopes_str = str(scopes_val)
            table.add_row(
                str(u.get("user_id", "")),
                str(u.get("username", "")),
                "[red]да[/red]" if u.get("is_admin") else "нет",
                scopes_str,
                str(u.get("created_at", "")),
            )
        console.print(table)

    @user_app.command("create")
    def user_create(
        user_id: str = typer.Option(..., "--user-id", help="Уникальный ID пользователя"),
        username: str = typer.Option(..., "--username", help="Отображаемое имя"),
        is_admin: bool = typer.Option(False, "--admin", help="Сделать администратором"),
    ) -> None:
        """Создать нового пользователя."""
        console = Console()
        password = getpass.getpass("Password: ").strip()
        if not password:
            console.print("[red]Ошибка: пароль не задан[/red]")
            raise typer.Exit(code=1)
        client = require_client(console)
        data = anyio.run(client.create_user, user_id, username, password, is_admin)
        if data is None:
            console.print("[red]Ошибка: не удалось создать пользователя[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] Пользователь [bold]{user_id}[/bold] создан.")

    sessions_app = typer.Typer(help="Управление активными сессиями")

    @sessions_app.command("list")
    def sessions_list() -> None:
        """Список активных сессий."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.list_sessions)
        if data is None:
            console.print("[red]Ошибка: не удалось получить сессии[/red]")
            raise typer.Exit(code=1)
        if isinstance(data, list):
            raw = None
        else:
            raw = data.get("sessions") or data.get("items")
        if raw is None and isinstance(data, list):
            sessions = data
        elif isinstance(raw, list):
            sessions = raw
        else:
            sessions = []
        table = Table(title="Активные сессии")
        table.add_column("session_id", style="bold")
        table.add_column("user_id")
        table.add_column("created_at")
        table.add_column("expires_at")
        for s in sessions:
            if not isinstance(s, dict):
                continue
            table.add_row(
                str(s.get("session_id", s.get("id", ""))),
                str(s.get("user_id", "")),
                str(s.get("created_at", "")),
                str(s.get("expires_at", "")),
            )
        console.print(table)

    @sessions_app.command("revoke")
    def sessions_revoke(
        session_id: str = typer.Argument(..., help="ID сессии для отзыва"),
    ) -> None:
        """Отозвать конкретную сессию."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.revoke_session, session_id)
        if data is None:
            console.print("[red]Ошибка: не удалось отозвать сессию[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] Сессия {session_id} отозвана.")

    @sessions_app.command("revoke-all")
    def sessions_revoke_all() -> None:
        """Отозвать все активные сессии (кроме текущей)."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.revoke_all_sessions)
        if data is None:
            console.print("[red]Ошибка: не удалось отозвать сессии[/red]")
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] Все сессии отозваны.")

    auth_app.add_typer(user_app, name="user")
    auth_app.add_typer(sessions_app, name="sessions")

    api_app = typer.Typer(help="API keys (если доступны в этой версии Core)")

    @api_app.command("list")
    def api_list() -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.api_keys_list)
        if not data:
            console.print("[red]Ошибка: API keys недоступны в этой версии Core[/red]")
            raise typer.Exit(code=1)
        items = data.get("items") or data.get("keys") or data.get("api_keys") or []
        if not isinstance(items, list):
            items = []
        table = Table(title="API keys")
        table.add_column("id", style="bold")
        table.add_column("name")
        table.add_column("created_at")
        table.add_column("last_used_at")
        for it in items:
            if not isinstance(it, dict):
                continue
            table.add_row(
                str(it.get("id", it.get("key_id", ""))),
                str(it.get("name", "")),
                str(it.get("created_at", "")),
                str(it.get("last_used_at", "")),
            )
        console.print(table)

    @api_app.command("create")
    def api_create(
        name: str | None = typer.Option(None, "--name", help="Имя ключа"),
        save: bool = typer.Option(False, "--save", help="Сохранить ключ в config.toml и переключиться на X-API-Key"),
    ) -> None:
        console = Console()
        client = require_client(console)
        cfg = Config.load()
        data = anyio.run(client.api_keys_create, name)
        if not data:
            console.print("[red]Ошибка: API keys недоступны в этой версии Core[/red]")
            raise typer.Exit(code=1)
        key = data.get("api_key") or data.get("key") or data.get("token")
        if not key:
            console.print(f"[yellow]Создано:[/yellow] {data}")
            raise typer.Exit(code=0)
        console.print("[green]✓[/green] API key создан.")
        console.print(str(key))
        if save:
            cfg.core.token = str(key)
            cfg.core.auth = "api-key"
            cfg.save()
            console.print("[green]✓[/green] Сохранил ключ в конфиг и переключился на X-API-Key.")

    @api_app.command("revoke")
    def api_revoke(key_id: str = typer.Argument(..., help="ID ключа")) -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.api_keys_revoke, key_id)
        if not data:
            console.print("[red]Ошибка: не удалось revoke (или API keys недоступны)[/red]")
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] Ключ отозван.")

    @api_app.command("rotate")
    def api_rotate(
        key_id: str = typer.Argument(..., help="ID ключа"),
        save: bool = typer.Option(False, "--save", help="Сохранить новый ключ в конфиг и переключиться на X-API-Key"),
    ) -> None:
        console = Console()
        client = require_client(console)
        cfg = Config.load()
        data = anyio.run(client.api_keys_rotate, key_id)
        if not data:
            console.print("[red]Ошибка: не удалось rotate (или API keys недоступны)[/red]")
            raise typer.Exit(code=1)
        key = data.get("api_key") or data.get("key") or data.get("token")
        if key:
            console.print("[green]✓[/green] Ключ обновлён.")
            console.print(str(key))
            if save:
                cfg.core.token = str(key)
                cfg.core.auth = "api-key"
                cfg.save()
                console.print("[green]✓[/green] Сохранил ключ в конфиг и переключился на X-API-Key.")
        else:
            console.print("[green]✓[/green] Ключ обновлён.")

    auth_app.add_typer(api_app, name="api-key")

    app.add_typer(auth_app, name="auth")

