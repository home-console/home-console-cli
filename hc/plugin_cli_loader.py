"""
Динамическая загрузка CLI-команд из плагинов.

Соглашение по именованию:
    Плагин должен использовать уникальное имя группы, не совпадающее со
    встроенными командами hc. Если совпадение обнаружено — в plugin.json
    можно задать альтернативное через поле "cli_name":

        {"name": "status", "cli_name": "my_plugin_status", ...}

    Встроенные команды защищены в RESERVED_COMMANDS ниже.

Плагин объявляет в plugin.json:
    {
        "cli": {
            "subcommands": [
                {
                    "name": "agent",
                    "description": "Управление агентами",
                    "commands": [
                        {
                            "name": "list",
                            "description": "Список агентов",
                            "service": "client_manager.list_clients",
                            "args": []
                        },
                        {
                            "name": "exec",
                            "description": "Выполнить команду",
                            "service": "client_manager.execute_command",
                            "args": [
                                {"name": "client_id", "help": "ID агента"},
                                {"name": "command",   "help": "Команда"}
                            ]
                        }
                    ]
                }
            ]
        }
    }

Кеш хранится в ~/.config/hc/plugin_cli_cache.json.
Обновляется при hc connect и hc plugin install/remove.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anyio
import typer
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table

logger = logging.getLogger(__name__)

_CACHE_PATH = Path.home() / ".config" / "hc" / "plugin_cli_cache.json"

# Зарезервированные имена верхнего уровня — плагин не может их занять.
# Если plugin.json объявляет такое имя, нужно использовать поле "cli_name".
RESERVED_COMMANDS: frozenset[str] = frozenset({
    "action", "agent", "auth", "cloud", "completion", "config", "connect",
    "core", "deploy", "doctor", "emergency", "env", "event", "install",
    "logs", "marketplace", "module", "nav", "ping", "plugin", "recovery",
    "remove", "repl", "reset", "rollback", "search", "secrets", "service",
    "setup", "shell", "shell-config", "status", "update", "upgrade",
    "version", "wg", "workspace",
})


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

def load_cache() -> list[dict[str, Any]]:
    """Загрузить кешированные cli-декларации плагинов."""
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text())
    except Exception:
        pass
    return []


def save_cache(declarations: list[dict[str, Any]]) -> None:
    """Сохранить cli-декларации плагинов в кеш."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(declarations, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.debug("plugin_cli_loader: failed to save cache: %s", e)


def clear_cache() -> None:
    try:
        _CACHE_PATH.unlink(missing_ok=True)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Fetch from server
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch_declarations(client: Any) -> list[dict[str, Any]]:
    """Получить cli-декларации всех установленных плагинов с сервера."""
    try:
        plugins = await client.get_plugins()
        if not plugins:
            return []
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for plugin in plugins:
        cli_block = plugin.get("cli") or plugin.get("manifest", {}).get("cli")
        if not cli_block:
            continue
        subcommands = cli_block.get("subcommands", [])
        if subcommands:
            result.append({
                "plugin": plugin.get("name", ""),
                "subcommands": subcommands,
            })
    return result


def refresh_cache(client: Any, *, warn: bool = True) -> None:
    """Синхронная обёртка: обновить кеш cli-деклараций. Вызывать после connect/install."""
    try:
        declarations = anyio.run(_fetch_declarations, client)
        if warn:
            _check_conflicts(declarations)
        save_cache(declarations)
    except Exception as e:
        logger.debug("plugin_cli_loader: refresh failed: %s", e)


def _check_conflicts(declarations: list[dict[str, Any]]) -> None:
    """Предупредить если плагин объявляет зарезервированное имя без cli_name."""
    console = Console()
    for decl in declarations:
        plugin = decl.get("plugin", "?")
        for sub in decl.get("subcommands", []):
            name     = sub.get("name", "")
            cli_name = sub.get("cli_name", "")
            effective = cli_name or name
            if effective in RESERVED_COMMANDS:
                console.print(
                    f"[yellow]⚠ plugin '{plugin}': cli subcommand '{effective}' конфликтует "
                    f"со встроенной командой hc.[/yellow]\n"
                    f"  Добавь в plugin.json: [bold]\"cli_name\": \"{plugin}_{effective}\"[/bold]"
                )


# ──────────────────────────────────────────────────────────────────────────────
# Typer command generation
# ──────────────────────────────────────────────────────────────────────────────

def _make_command(cmd_decl: dict[str, Any], subgroup: typer.Typer) -> None:
    """Генерирует одну Typer-команду из декларации."""
    service   = cmd_decl.get("service", "")
    cmd_name  = cmd_decl.get("name", "")
    cmd_desc  = cmd_decl.get("description", "")
    args_decl = cmd_decl.get("args", [])

    if not service or not cmd_name:
        return

    # Строим функцию с positional аргументами через exec (Typer требует реальные аргументы)
    arg_names = [a["name"] for a in args_decl]
    arg_helps = {a["name"]: a.get("help", a["name"]) for a in args_decl}

    def _make_fn(svc: str, names: list[str]) -> Any:
        from hc.commands._client_helpers import require_client

        if not names:
            def fn() -> None:
                console = Console()
                client = require_client(console, silent=True)

                async def _call() -> Any:
                    return await client.call_service(svc)

                data = anyio.run(_call)
                _print_result(console, data)
            fn.__doc__ = cmd_desc
            return fn
        elif len(names) == 1:
            def fn1(arg0: str = typer.Argument(..., help=arg_helps[names[0]])) -> None:
                console = Console()
                client = require_client(console, silent=True)

                async def _call() -> Any:
                    return await client.call_service(svc, {names[0]: arg0})

                data = anyio.run(_call)
                _print_result(console, data)
            fn1.__doc__ = cmd_desc
            return fn1
        else:
            def fn2(
                arg0: str = typer.Argument(..., help=arg_helps[names[0]]),
                arg1: str = typer.Argument(..., help=arg_helps[names[1]]),
            ) -> None:
                console = Console()
                client = require_client(console, silent=True)
                kwargs = {names[0]: arg0, names[1]: arg1}

                async def _call() -> Any:
                    return await client.call_service(svc, kwargs)

                data = anyio.run(_call)
                _print_result(console, data)
            fn2.__doc__ = cmd_desc
            return fn2

    fn = _make_fn(service, arg_names)
    subgroup.command(cmd_name)(fn)


def _print_result(console: Console, data: Any) -> None:
    """Универсальный вывод результата сервиса."""
    if data is None:
        console.print("[yellow]Нет данных[/yellow]")
        return

    payload = data
    if isinstance(data, dict):
        payload = data.get("result", data)

    # Список словарей → Rich Table
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        keys = list(payload[0].keys())
        table = Table(*keys, show_header=True)
        for row in payload:
            table.add_row(*[str(row.get(k, "")) for k in keys])
        console.print(table)
        return

    console.print(Pretty(payload))


# ──────────────────────────────────────────────────────────────────────────────
# Register into app
# ──────────────────────────────────────────────────────────────────────────────

def register_plugin_commands(app: typer.Typer, *, silent: bool = True) -> list[str]:
    """
    Загрузить кешированные CLI-команды плагинов и зарегистрировать их в app.
    Возвращает список зарегистрированных групп.
    """
    declarations = load_cache()
    registered: list[str] = []

    console = Console()

    for decl in declarations:
        plugin_name = decl.get("plugin", "unknown")
        for sub in decl.get("subcommands", []):
            name      = sub.get("name", "").strip()
            cli_name  = sub.get("cli_name", "").strip()
            desc      = sub.get("description", "")
            commands  = sub.get("commands", [])

            if not name or not commands:
                continue

            # cli_name имеет приоритет над name
            effective_name = cli_name or name

            # Проверка на зарезервированные встроенные команды
            if effective_name in RESERVED_COMMANDS:
                console.print(
                    f"[red]✗ plugin '{plugin_name}': команда '{effective_name}' зарезервирована.[/red]\n"
                    f"  Добавь в plugin.json: [bold]\"cli_name\": \"{plugin_name}_{effective_name}\"[/bold]"
                )
                continue

            # Проверка на конфликт с уже зарегистрированными командами (другой плагин)
            registered_names = {g.name for g in app.registered_groups}
            if effective_name in registered_names:
                console.print(
                    f"[yellow]⚠ plugin '{plugin_name}': команда '{effective_name}' уже занята "
                    f"другим плагином. Используй \"cli_name\" для переименования.[/yellow]"
                )
                continue

            subgroup = typer.Typer(
                help=desc,
                context_settings={"help_option_names": ["-h", "--help"]},
            )

            for cmd_decl in commands:
                _make_command(cmd_decl, subgroup)

            app.add_typer(subgroup, name=effective_name)
            registered.append(effective_name)
            if not silent:
                suffix = f" (cli_name)" if cli_name else ""
                console.print(f"[dim]plugin cli: loaded '{effective_name}'{suffix} ({plugin_name})[/dim]")

    return registered
