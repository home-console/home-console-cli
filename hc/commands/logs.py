from __future__ import annotations

import os
import re

import anyio
import typer
from rich.console import Console
from rich.text import Text

from hc.client import HCClient
from hc.config import Config

_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR)\s")


def _style_line(line: str) -> Text:
    m = _LEVEL_RE.search(line)
    if not m:
        return Text(line)
    level = m.group(1)
    t = Text(line)
    if level == "DEBUG":
        t.stylize("grey50")
    elif level == "INFO":
        t.stylize("white")
    elif level == "WARNING":
        t.stylize("yellow")
    elif level == "ERROR":
        t.stylize("red")
    return t


def register(app: typer.Typer) -> None:
    @app.command("logs")
    def logs(
        follow: bool = typer.Option(False, "--follow", help="Следить за логами (stream)"),
        module: str | None = typer.Option(None, "--module", help="Фильтр по модулю"),
        level: str | None = typer.Option(
            None, "--level", help="debug|info|warning|error (локальная фильтрация)"
        ),
    ) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)
        wanted = level.upper() if level else None

        async def _run() -> int:
            count = 0
            async for line in client.stream_logs(module=module, follow=follow):
                if wanted and wanted not in line.upper():
                    continue
                console.print(_style_line(line))
                count += 1
                if not follow and count >= 100:
                    break
            return 0

        anyio.run(_run)

