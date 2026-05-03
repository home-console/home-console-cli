from __future__ import annotations

import os
import time

import anyio
import typer
from rich.console import Console

from hc.client import HCClient
from hc.config import Config


def register(app: typer.Typer) -> None:
    @app.command("ping")
    def ping(
        host: str | None = typer.Option(None, "--host", help="Хост (по умолчанию из конфига или HC_HOST)"),
        port: int | None = typer.Option(None, "--port", help="Порт (по умолчанию из конфига или HC_PORT)"),
    ) -> None:
        """Проверить доступность Core (без авторизации)."""
        console = Console()
        cfg = Config.load()
        h = host or os.getenv("HC_HOST") or cfg.core.host
        p = port if port is not None else int((os.getenv("HC_PORT") or "").strip() or cfg.core.port)
        base_url = f"http://{h}:{p}"
        client = HCClient(base_url=base_url, token="", verify_ssl=cfg.core.verify_ssl)

        t0 = time.monotonic()
        data = anyio.run(client.health)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if data is None:
            console.print(f"[red]✗[/red] Core недоступен на {h}:{p}")
            raise typer.Exit(code=1)

        status = data.get("status", "unknown")
        color = "green" if str(status).lower() in {"ok", "healthy", "running"} else "yellow"
        console.print(
            f"[{color}]✓[/{color}] {h}:{p} — [{color}]{status}[/{color}]"
            f"  [dim]{elapsed_ms:.0f}ms[/dim]"
        )
