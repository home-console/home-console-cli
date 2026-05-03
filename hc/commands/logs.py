from __future__ import annotations

import os
import re

import anyio
import typer
from rich.console import Console
from rich.text import Text

from hc.commands._client_helpers import require_client

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
        client = require_client(console)
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

