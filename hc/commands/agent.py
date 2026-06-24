"""hc agent — управление удалёнными агентами (client_manager plugin)."""
from __future__ import annotations

import asyncio
import os
import sys
import termios
import tty

import anyio
import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client
from hc.config import Config


# ---------------------------------------------------------------------------
# Interactive exec (PTY-сессия через client-manager WebSocket)
# ---------------------------------------------------------------------------

def _cm_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/")
    return Config.load().plugins.client_manager_url.rstrip("/")


def _agent_exec_interactive(
    console: Console,
    client_id: str,
    command: str,
    cm_url_override: str | None,
) -> None:
    """Интерактивная PTY-сессия через client-manager terminal WebSocket.

    Протокол:
      1. POST /api/clients/{client_id}/terminal/start  →  {session_id}
      2. WS   /api/ws/terminal/{session_id}?token=...
         - отправляем bytes = stdin агенту (base64 в JSON внутри WS)
         - получаем bytes = stdout/stderr от агента
    """
    import json as _json

    cm = _cm_base_url(cm_url_override)
    token = _get_cm_token()

    # 1. Стартуем terminal сессию
    try:
        r = httpx.post(
            f"{cm}/api/clients/{client_id}/terminal/start",
            headers={"Authorization": f"Bearer {token}"},
            json={"command": command},
            timeout=10.0,
        )
        if r.status_code == 404:
            console.print(f"[red]Агент {client_id!r} не подключён к client-manager.[/red]")
            raise typer.Exit(code=1)
        if not r.is_success:
            console.print(f"[red]Ошибка запуска сессии: HTTP {r.status_code}[/red]")
            console.print(f"[dim]URL: {cm} — задай через HC_CM_URL или --cm-url[/dim]")
            raise typer.Exit(code=1)
        session_id = r.json().get("session_id", "")
        if not session_id:
            console.print("[red]Сервер не вернул session_id.[/red]")
            raise typer.Exit(code=1)
    except httpx.ConnectError:
        console.print(f"[red]client-manager недоступен: {cm}[/red]")
        console.print("[dim]Задай URL: HC_CM_URL=http://host:41200 или --cm-url[/dim]")
        raise typer.Exit(code=1)

    ws_url = cm.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_url}/api/ws/terminal/{session_id}?token={token}"

    console.print(
        f"[dim]Подключение к [bold]{client_id}[/bold] · {command} · Ctrl+D для выхода[/dim]"
    )

    # 2. Переводим терминал в raw mode и запускаем WS-сессию
    _run_terminal_ws(ws_url)


def _get_cm_token() -> str:
    """Возвращает токен Core из конфига. client-manager принимает его как JWT."""
    cfg = Config.load()
    return cfg.core.token or os.environ.get("HC_TOKEN", "")


def _run_terminal_ws(ws_url: str) -> None:
    """Запускает async event loop для WebSocket PTY-сессии."""
    try:
        asyncio.run(_terminal_ws_session(ws_url))
    except KeyboardInterrupt:
        pass


async def _terminal_ws_session(ws_url: str) -> None:
    """Async: соединяется с WS, пробрасывает stdin→агент, stdout агента→терминал."""
    import websockets

    # Переводим stdin в raw mode чтобы Ctrl+C, стрелки, etc. работали
    is_tty = sys.stdin.isatty()
    old_settings: list | None = None
    if is_tty:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setraw(fd)

    try:
        async with websockets.connect(ws_url) as ws:
            await asyncio.gather(
                _stdin_to_ws(ws),
                _ws_to_stdout(ws),
                return_exceptions=True,
            )
    except Exception:
        pass
    finally:
        if is_tty and old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        sys.stdout.write("\r\n")
        sys.stdout.flush()


async def _stdin_to_ws(ws: object) -> None:
    """Читать stdin и отправлять как байты в WebSocket."""
    loop = asyncio.get_event_loop()
    try:
        while True:
            # Читаем 256 байт stdin в asyncio thread executor
            chunk = await loop.run_in_executor(None, sys.stdin.buffer.read1, 256)  # type: ignore[attr-defined]
            if not chunk:
                break
            # Ctrl+D (EOF) = завершить
            if b"\x04" in chunk:
                await ws.close()  # type: ignore[attr-defined]
                break
            await ws.send(chunk)  # type: ignore[attr-defined]
    except Exception:
        pass


async def _ws_to_stdout(ws: object) -> None:
    """Получать байты из WebSocket и писать в stdout."""
    import websockets

    try:
        async for message in ws:  # type: ignore[attr-defined]
            if isinstance(message, bytes):
                sys.stdout.buffer.write(message)
                sys.stdout.buffer.flush()
            elif isinstance(message, str):
                # Может быть JSON control (error, exit_code)
                try:
                    import json as _json
                    data = _json.loads(message)
                    if data.get("type") == "error":
                        sys.stdout.write(f"\r\n[error] {data.get('message', '')}\r\n")
                    elif data.get("type") == "exit":
                        break
                except Exception:
                    sys.stdout.write(message)
                sys.stdout.flush()
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception:
        pass


def register(app: typer.Typer) -> None:
    agent_app = typer.Typer(
        help="Управление удалёнными агентами (client_manager)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    # ------------------------------------------------------------------ list

    @agent_app.command("list")
    def agent_list(
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Список подключённых агентов."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "client_manager.list_clients", None)
        if data is None:
            console.print("[red]Ошибка: client_manager недоступен[/red]")
            raise typer.Exit(code=1)

        clients = _extract_list(data, ("clients", "result", "data"))

        if json_out:
            from hc.json_output import print_json
            print_json({"ok": True, "clients": clients, "total": len(clients)})
            return

        if not clients:
            console.print("[dim]Нет подключённых агентов.[/dim]")
            return

        table = Table(title=f"Агенты ({len(clients)})")
        table.add_column("ID",         style="bold cyan")
        table.add_column("Hostname",   style="bold")
        table.add_column("IP")
        table.add_column("OS / Type",  style="dim")
        table.add_column("Статус")

        for c in clients:
            status = c.get("status", "?")
            color = "green" if status == "connected" else "yellow"
            table.add_row(
                str(c.get("id", c.get("client_id", "?"))),
                str(c.get("hostname", "?")),
                str(c.get("ip", c.get("ip_address", "?"))),
                f"{c.get('os', '')} {c.get('device_type', '')}".strip(),
                Text(status, style=color),
            )
        console.print(table)

    # ------------------------------------------------------------------ get

    @agent_app.command("get")
    def agent_get(
        client_id: str = typer.Argument(..., help="ID агента"),
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Подробная информация об агенте."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "client_manager.get_client", {"client_id": client_id})
        if data is None:
            console.print(f"[red]Агент {client_id!r} не найден.[/red]")
            raise typer.Exit(code=1)

        payload = data.get("result", data) if isinstance(data, dict) else data

        if json_out:
            from hc.json_output import print_json
            print_json(payload)
            return

        from rich.pretty import Pretty
        console.print(Panel(Pretty(payload, expand_all=True), title=f"[bold]Agent: {client_id}[/bold]", expand=False))

    # ------------------------------------------------------------------ exec

    @agent_app.command("exec")
    def agent_exec(
        client_id:   str = typer.Argument(..., help="ID агента"),
        command:     str = typer.Argument(..., help="Команда для выполнения"),
        timeout:     int = typer.Option(30, "--timeout", "-t", help="Таймаут в секундах"),
        raw:        bool = typer.Option(False, "--raw", help="Показать сырой JSON ответ"),
        interactive: bool = typer.Option(
            False, "--interactive", "-i",
            help="Интерактивный режим: stdin → агент, stdout агента → терминал (PTY-сессия)",
        ),
        cm_url: str | None = typer.Option(
            None, "--cm-url",
            envvar="HC_CM_URL",
            help="URL client-manager (для --interactive). По умолчанию из конфига.",
        ),
    ) -> None:
        """Выполнить команду на удалённом агенте.

        По умолчанию — однократный запуск, ждёт результата.
        С флагом --interactive открывает PTY-сессию: stdin пробрасывается в агент,
        stdout/stderr агента выводится в реальном времени. Ctrl+D или Ctrl+C завершают сессию.

        Примеры:
          hc agent exec host01 "df -h"
          hc agent exec host01 "systemctl status nginx" --timeout 10
          hc agent exec host01 bash --interactive
          hc agent exec host01 "python3" -i --cm-url http://192.168.1.10:41200
        """
        console = Console()

        if interactive:
            _agent_exec_interactive(console, client_id, command, cm_url)
            return

        client = require_client(console)

        kwargs = {"client_id": client_id, "command": command, "timeout": timeout}
        data = anyio.run(client.call_service, "client_manager.execute_command", kwargs)

        if data is None:
            console.print("[red]Ошибка: нет ответа от агента.[/red]")
            raise typer.Exit(code=1)

        payload = data.get("result", data) if isinstance(data, dict) else data

        if raw:
            from hc.json_output import print_json
            print_json(payload)
            return

        exit_code = None
        output = ""

        if isinstance(payload, dict):
            exit_code = payload.get("exit_code")
            output    = str(payload.get("output", payload.get("stdout", "")))
            stderr    = str(payload.get("stderr", ""))
            if stderr:
                output += f"\n[stderr]\n{stderr}"
        else:
            output = str(payload)

        if output:
            console.print(output, end="")
            if not output.endswith("\n"):
                console.print()

        if exit_code is not None and exit_code != 0:
            console.print(f"[yellow]Код завершения: {exit_code}[/yellow]")
            raise typer.Exit(code=exit_code if isinstance(exit_code, int) else 1)

    # ------------------------------------------------------------------ disconnect

    @agent_app.command("disconnect")
    def agent_disconnect(
        client_id: str = typer.Argument(..., help="ID агента"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
    ) -> None:
        """Отключить агента от Core."""
        console = Console()
        if not yes:
            confirmed = typer.confirm(f"Отключить агента {client_id!r}?", default=False)
            if not confirmed:
                console.print("[dim]Отменено.[/dim]")
                raise typer.Exit(code=0)

        client = require_client(console)
        data = anyio.run(client.call_service, "client_manager.delete_client", {"client_id": client_id})

        if data is None:
            console.print("[red]Ошибка: нет ответа.[/red]")
            raise typer.Exit(code=1)

        console.print(f"[green]✓[/green] Агент [bold]{client_id}[/bold] отключён.")

    app.add_typer(agent_app, name="agent")


# ---------------------------------------------------------------------------

def _extract_list(data: object, keys: tuple[str, ...]) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []
