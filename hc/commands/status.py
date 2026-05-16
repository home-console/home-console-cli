from __future__ import annotations

import time

import anyio
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    @app.command("status")
    def status(
        watch: bool = typer.Option(False, "--watch", "-w", help="Live-мониторинг (Ctrl+C для выхода)"),
        interval: float = typer.Option(5.0, "--interval", "-n", help="Интервал обновления в секундах"),
    ) -> None:
        """Статус Core: версия, uptime, плагины, модули. С --watch — live-мониторинг."""
        console = Console()
        client = require_client(console)

        latencies: list[float] = []

        async def _fetch() -> tuple[dict | None, int | None, tuple[int, int] | None, float]:
            t0 = time.monotonic()
            health = await client.admin_status()
            if not health:
                health = await client.health()
            latency_ms = (time.monotonic() - t0) * 1000

            plugins = await client.get_plugins()
            modules = await client.get_modules()

            active_plugins = None
            if isinstance(plugins, list):
                active_plugins = sum(1 for p in plugins if str(p.get("status", "")).lower() == "running")

            modules_stat = None
            if isinstance(modules, list):
                total = len(modules)
                ok = sum(1 for m in modules if str(m.get("status", "")).lower() in {"running", "ok"})
                modules_stat = (ok, total)

            return health, active_plugins, modules_stat, latency_ms

        def _build_panel(
            health: dict | None,
            active_plugins: int | None,
            modules_stat: tuple[int, int] | None,
            latency_ms: float,
        ) -> Panel:
            if not health:
                return Panel(Text("Core недоступен", style="red"), title="HomeConsole", border_style="red")

            latencies.append(latency_ms)
            if len(latencies) > 10:
                latencies.pop(0)

            version = str(health.get("version", "unknown"))
            status_value = str(health.get("status", "running"))
            uptime = str(health.get("uptime", "unknown"))

            status_text = Text(status_value)
            if status_value.lower() in {"running", "ok"}:
                status_text.stylize("green")
                status_text = Text("✓ ") + status_text
            else:
                status_text.stylize("red")

            lat_color = "green" if latency_ms < 100 else "yellow" if latency_ms < 500 else "red"
            lat_avg = sum(latencies) / len(latencies)
            lat_line = f"{latency_ms:.0f}ms  [dim](avg {lat_avg:.0f}ms)[/dim]"

            lines: list[Text] = [
                Text.assemble(("Версия:   ", "bold"), (version, "")),
                Text.assemble(("Статус:   ", "bold"), status_text),
                Text.assemble(("Latency:  ", "bold"), Text(lat_line, style=lat_color)),
            ]
            if active_plugins is not None:
                lines.append(Text.assemble(("Плагинов: ", "bold"), (f"{active_plugins} активных", "")))
            if modules_stat is not None:
                ok, total = modules_stat
                col = "green" if ok == total else "yellow"
                lines.append(Text.assemble(("Модулей:  ", "bold"), Text(f"{ok} / {total}", style=col)))
            lines.append(Text.assemble(("Uptime:   ", "bold"), (uptime, "")))

            title = "HomeConsole"
            if watch:
                ts = time.strftime("%H:%M:%S")
                title += f"  [dim]{ts}[/dim]"

            return Panel(Text("\n").join(lines), title=title, border_style="cyan")

        if not watch:
            health, active_plugins, modules_stat, latency_ms = anyio.run(_fetch)
            console.print(_build_panel(health, active_plugins, modules_stat, latency_ms))
            if not health:
                raise typer.Exit(code=1)
            return

        try:
            with Live(refresh_per_second=1, screen=False) as live:
                while True:
                    health, active_plugins, modules_stat, latency_ms = anyio.run(_fetch)
                    live.update(_build_panel(health, active_plugins, modules_stat, latency_ms))
                    time.sleep(interval)
        except (KeyboardInterrupt, typer.Abort):
            pass
