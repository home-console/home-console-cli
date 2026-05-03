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
        data = anyio.run(client.auth_login, user_id, password)
        payload = (data.get("result") if isinstance(data, dict) else None) or data
        if not isinstance(payload, dict) or "access_token" not in payload:
            console.print("[red]Ошибка: не удалось выполнить login[/red]")
            raise typer.Exit(code=1)

        token = str(payload["access_token"])
        cfg = Config.load()
        cfg.core.host = host
        cfg.core.port = port
        cfg.core.token = token
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
        cfg.save()
        console.print("[green]✓[/green] Токен очищен из конфига.")

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

