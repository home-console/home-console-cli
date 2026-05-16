from __future__ import annotations

import os
import shlex
from typing import Iterable, Iterator, Sequence

import anyio
import click
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completion, Completer, WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console

from typer.main import get_command as _typer_get_command

from hc import __version__
from hc.config import Config
from hc.constants import APP_NAME, HISTORY_PATH
from hc.commands._client_helpers import require_client
from hc.update_check import get_update_notification


_GROUPS = {"core", "auth", "setup", "plugin", "module", "reset", "recovery", "deploy", "update"}


class _HCCompleter(Completer):
    def __init__(
        self,
        *,
        app: typer.Typer | None = None,
        commands: Iterable[str],
        plugins: Iterable[str],
        get_group_ctx: callable | None = None,
    ) -> None:
        self._app = app
        self._root: click.Command | None = None
        if app is not None:
            try:
                self._root = _typer_get_command(app)
            except RuntimeError:
                # In tests we may get an empty Typer() without commands.
                self._root = None
        self._cmd = WordCompleter(list(commands), ignore_case=True)
        self._plg = WordCompleter(list(plugins), ignore_case=True)
        self._get_group_ctx = get_group_ctx or (lambda: None)

    def _iter_options(self, cmd: click.Command) -> Iterator[str]:
        for p in getattr(cmd, "params", []) or []:
            if isinstance(p, click.Option):
                for opt in (p.opts or []) + (p.secondary_opts or []):
                    if opt.startswith("-"):
                        yield opt

    def _find_command(self, argv: Sequence[str]) -> tuple[click.Command, list[str]]:
        """
        Walk click command tree, consuming group/subcommands.
        Returns (current_command, remaining_args_after_command_path).
        """
        if self._root is None:
            return click.Command("hc"), list(argv)
        cmd: click.Command = self._root
        rest = list(argv)
        while rest:
            if not isinstance(cmd, click.Group):
                break
            token = rest[0]
            nxt = cmd.commands.get(token) if token else None
            if nxt is None:
                break
            cmd = nxt
            rest = rest[1:]
        return cmd, rest

    def _completions_for_tokens(self, parts: list[str], raw_text: str, cursor_pos: int) -> Iterator[Completion]:
        """
        Context-aware completion:
        - root commands or group subcommands if in group context
        - options for the current resolved command
        - plugin names for install/remove and plugin start/stop/info/restart/reload/restart-container/logs
        """
        group_ctx: str | None = self._get_group_ctx()

        # Special meta command: `use <group>`
        if parts and parts[0] == "use":
            if len(parts) <= 2:
                word = parts[1] if len(parts) == 2 else ""
                start = raw_text.rfind(word)
                for g in sorted(_GROUPS):
                    if g.lower().startswith(word.lower()):
                        yield Completion(g, start_position=-len(word))
            return

        # Resolve effective argv taking group context into account (like execution path does).
        argv = parts[:]
        if group_ctx and (not argv or argv[0] not in _GROUPS):
            argv = [group_ctx, *argv]

        cmd, _rest = self._find_command(argv)

        # If we are at a group and about to type a subcommand → suggest subcommands.
        if isinstance(cmd, click.Group):
            # Determine which token is being completed (last token).
            last = parts[-1] if parts else ""
            # If cursor is after a space, last token is empty.
            if raw_text.endswith(" "):
                last = ""
            # If user is typing an option, don't suggest subcommands here.
            if last.startswith("-"):
                for opt in sorted(set(self._iter_options(cmd))):
                    if opt.startswith(last):
                        yield Completion(opt, start_position=-len(last))
                return

            # Suggest group subcommands by the *visible* context.
            for name in sorted(cmd.commands.keys()):
                if name.lower().startswith(last.lower()):
                    yield Completion(name, start_position=-len(last))
            # Also suggest group options.
            for opt in sorted(set(self._iter_options(cmd))):
                if opt.startswith(last):
                    yield Completion(opt, start_position=-len(last))
            return

        # Leaf command: suggest options
        last = parts[-1] if parts else ""
        if raw_text.endswith(" "):
            last = ""
        if last.startswith("-") or True:
            opts = sorted(set(self._iter_options(cmd)))
            for opt in opts:
                if not last or opt.startswith(last):
                    yield Completion(opt, start_position=-len(last))

        # Plugin name completion (simple but useful)
        if parts:
            # Complete plugin as the next token.
            def _complete_from_list(values: list[str]) -> Iterator[Completion]:
                word = "" if raw_text.endswith(" ") else (parts[-1] if parts else "")
                for v in sorted(set(values)):
                    if not word or v.lower().startswith(word.lower()):
                        yield Completion(v, start_position=-len(word))

            if parts[0] in {"install", "remove"} and len(parts) <= 2:
                yield from _complete_from_list(list(getattr(self._plg, "words", [])))
                return
            if parts[0] == "plugin" and len(parts) >= 2 and parts[1] in {
                "start",
                "stop",
                "info",
                "restart",
                "reload",
                "restart-container",
                "logs",
            }:
                # Complete plugin name as the 3rd token.
                if len(parts) <= 3:
                    yield from _complete_from_list(list(getattr(self._plg, "words", [])))
                return

    def get_completions(self, document, complete_event):
        raw = document.text_before_cursor
        text = raw.lstrip()
        try:
            parts = shlex.split(text, posix=True) if text else []
        except ValueError:
            # Unclosed quote etc. → fallback to root completer
            yield from self._cmd.get_completions(document, complete_event)
            return

        if not parts and not raw.endswith(" "):
            yield from self._cmd.get_completions(document, complete_event)
            return

        yielded = False
        for c in self._completions_for_tokens(parts, raw_text=raw, cursor_pos=document.cursor_position):
            yielded = True
            yield c
        if not yielded:
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
    cfg_ok = bool(cfg.core.host.strip()) and bool(token.strip())
    hostport = f"{cfg.core.host}:{cfg.core.port}"

    plugins: list[str] = []
    connected = False
    if cfg_ok:
        client = require_client(console, silent=True)

        async def _get_names() -> tuple[bool, list[str]]:
            # Пытаемся получить имена плагинов из inspector (админский источник истины).
            insp = await client.inspector_plugins()
            if isinstance(insp, dict):
                arr = insp.get("plugins") or []
                if isinstance(arr, list):
                    names = [str(p["name"]) for p in arr if isinstance(p, dict) and p.get("name")]
                    if names:
                        return True, names
            # Fallback на старый эндпоинт (если он есть).
            items = await client.get_plugins()
            if items is not None:
                return True, [str(p.get("name", "")) for p in items if p.get("name")]
            return False, []

        connected, plugins = anyio.run(_get_names)

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
    group_ctx: str | None = None

    def _get_ctx() -> str | None:
        return group_ctx

    completer = _HCCompleter(app=app, commands=commands, plugins=plugins, get_group_ctx=_get_ctx)

    if connected:
        status = f"connected to {hostport}"
    elif cfg_ok:
        status = f"offline • configured for {hostport}"
    else:
        status = "not connected"
    console.print(f"{APP_NAME} {__version__} | {status}")
    console.print("Type 'help' or '?' for commands, 'exit' to quit")

    latest = get_update_notification(__version__)
    if latest:
        console.print(
            f"[yellow]→ Доступна новая версия [bold]{latest}[/bold] "
            f"(текущая {__version__})[/yellow]"
        )
        console.print("[dim]  pipx upgrade homeconsole-cli  |  pip install --upgrade homeconsole-cli[/dim]")

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(_prompt(None), history=FileHistory(str(HISTORY_PATH)), completer=completer)

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

