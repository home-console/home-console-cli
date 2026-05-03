from __future__ import annotations

import os
import shlex
from typing import Iterable

import anyio
import click
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console

from hc.client import HCClient
from hc.config import Config
from hc.constants import APP_NAME, HISTORY_PATH
from hc.commands._client_helpers import require_client


_GROUPS = {"core", "auth", "setup", "plugin", "module", "reset", "recovery", "deploy", "update"}


class _HCCompleter(Completer):
    def __init__(self, commands: Iterable[str], plugins: Iterable[str]) -> None:
        self._cmd = WordCompleter(list(commands), ignore_case=True)
        self._plg = WordCompleter(list(plugins), ignore_case=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        parts = shlex.split(text, posix=True) if text else []
        if not parts:
            yield from self._cmd.get_completions(document, complete_event)
            return
        if parts[0] in {"install", "remove"} and len(parts) <= 2:
            yield from self._plg.get_completions(document, complete_event)
            return
        if parts[0] == "plugin" and len(parts) >= 2 and parts[1] in {"start", "stop", "info"}:
            yield from self._plg.get_completions(document, complete_event)
            return
        yield from self._cmd.get_completions(document, complete_event)


def _prompt(prefix: str | None) -> str:
    return f"{prefix}> " if prefix else "hc> "

def _split_batch(line: str) -> list[tuple[str, str | None]]:
    """
    Разбивает строку на команды по `;` и `&&` вне кавычек.
    Возвращает список (cmd, op_to_next) где op_to_next ∈ {None, ';', '&&'}.
    """
    out: list[tuple[str, str | None]] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if line.startswith("&&", i):
            cmd = "".join(buf).strip()
            if cmd:
                out.append((cmd, "&&"))
            buf = []
            i += 2
            continue
        if ch == ";":
            cmd = "".join(buf).strip()
            if cmd:
                out.append((cmd, ";"))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cmd = "".join(buf).strip()
    if cmd:
        out.append((cmd, None))
    return out


def run_repl(app: typer.Typer) -> None:
    console = Console()
    cfg = Config.load()
    token = os.getenv("HC_TOKEN") or cfg.core.token
    connected = bool(cfg.core.host.strip()) and bool(token.strip())
    hostport = f"{cfg.core.host}:{cfg.core.port}"

    plugins: list[str] = []
    if connected:
        client = require_client(console)

        async def _get_names() -> list[str]:
            # Пытаемся получить имена плагинов из inspector (админский источник истины).
            insp = await client.inspector_plugins()
            if isinstance(insp, dict):
                arr = insp.get("plugins") or []
                if isinstance(arr, list):
                    names = []
                    for p in arr:
                        if isinstance(p, dict) and p.get("name"):
                            names.append(str(p["name"]))
                    if names:
                        return names
            # Fallback на старый эндпоинт (если он есть).
            items = await client.get_plugins()
            if not items:
                return []
            return [str(p.get("name", "")) for p in items if p.get("name")]

        plugins = anyio.run(_get_names)

    commands = [
        "connect",
        "status",
        "install",
        "remove",
        "search",
        "logs",
        "core",
        "auth",
        "reset",
        "plugin",
        "module",
        "setup",
        "recovery",
        "deploy",
        "update",
        "shell",
        "repl",
        "help",
        "?",
        "exit",
        "back",
        "..",
        "use",
        "history",
        "clear",
    ]
    completer = _HCCompleter(commands=commands, plugins=plugins)

    console.print(f"{APP_NAME} 0.0.1 | " + (f"connected to {hostport}" if connected else "not connected"))
    console.print("Type 'help' or '?' for commands, 'exit' to quit")

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(_prompt(None), history=FileHistory(str(HISTORY_PATH)), completer=completer)

    group_ctx: str | None = None

    while True:
        try:
            line = session.prompt()
        except (EOFError, KeyboardInterrupt):
            break

        line = line.strip()
        if not line:
            continue
        if line == "clear":
            console.clear()
            continue
        if line in {"exit", "quit"}:
            break
        if line in {"back", ".."}:
            group_ctx = None
            session.message = _prompt(None)
            continue
        if line in {"help", "?"}:
            console.print("Команды: " + ", ".join(commands))
            continue
        if line.startswith("use "):
            target = line.removeprefix("use ").strip()
            if target in _GROUPS:
                group_ctx = target
                session.message = _prompt(f"hc {group_ctx}")
                try:
                    app(prog_name="hc", args=[group_ctx, "--help"], standalone_mode=False)
                except Exception:
                    pass
            else:
                console.print(f"[red]Ошибка: неизвестный контекст '{target}'[/red]")
            continue
        if line.startswith("history"):
            parts = line.split()
            n = 50
            if len(parts) >= 2:
                try:
                    n = int(parts[1])
                except ValueError:
                    n = 50
            if HISTORY_PATH.exists():
                text = HISTORY_PATH.read_text(encoding="utf-8", errors="replace")
                tail = "\n".join(text.splitlines()[-n:])
                if tail.strip():
                    console.print(tail)
            continue

        def _run_one(cmd_line: str) -> bool:
            nonlocal group_ctx
            try:
                args = shlex.split(cmd_line, posix=True)
            except ValueError as e:
                console.print(f"[red]Ошибка: {e}[/red]")
                return False
            # UX: внутри `hc shell` люди часто по привычке пишут `hc ...`
            # Считаем это допустимым и просто убираем префикс.
            if args and args[0] == "hc":
                args = args[1:]

            # `setup help` / `core ?` → показываем help.
            if args and args[-1] in {"help", "?"}:
                args = [*args[:-1], "--help"]

            # Проваливаемся в контекст: `core` → core>
            if len(args) == 1 and args[0] in _GROUPS:
                group_ctx = args[0]
                session.message = _prompt(f"hc {group_ctx}")
                try:
                    app(prog_name="hc", args=[group_ctx, "--help"], standalone_mode=False)
                except Exception:
                    pass
                return True

            # Если мы внутри группы, подставляем префикс.
            if group_ctx and (not args or args[0] not in _GROUPS):
                args = [group_ctx, *args]

            try:
                app(prog_name="hc", args=args, standalone_mode=False)
                return True
            except click.ClickException as e:
                msg = e.format_message()
                if args and args[0] in _GROUPS and msg == "Missing command.":
                    try:
                        app(prog_name="hc", args=[args[0], "--help"], standalone_mode=False)
                    except Exception:
                        console.print(f"[red]{msg}[/red]")
                    return False
                if args and args[0] == "connect" and "Missing argument" in msg and "HOST" in msg:
                    console.print("[red]Ошибка: не указан адрес CoreRuntime.[/red]")
                    console.print("Пример: [bold]connect localhost --port 18000[/bold]")
                    console.print("Токен можно передать `--token`, через `HC_TOKEN`, или ввести интерактивно.")
                    return False
                console.print(f"[red]{msg}[/red]")
                return False
            except SystemExit as e:
                return int(getattr(e, "code", 0) or 0) == 0
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]Ошибка: {e}[/red]")
                return False

        ok = True
        for cmd, op in _split_batch(line):
            ok = _run_one(cmd)
            if op == "&&" and not ok:
                break

