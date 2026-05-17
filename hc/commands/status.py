from __future__ import annotations

import time

import anyio
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from hc.commands._client_helpers import require_client
from hc.json_output import print_json


def _show_components(console: Console, client: object) -> None:
    """Показать статус каждого компонента: API, модули, плагины, БД."""
    from rich.table import Table

    async def _gather():
        health  = await client.health()               # type: ignore[union-attr]
        modules = await client.get_modules()          # type: ignore[union-attr]
        plugins = await client.get_plugins()          # type: ignore[union-attr]
        sys_h   = await client._request_json_absolute("GET", "/api/v1/admin/inspector/system_health")  # type: ignore[union-attr]
        return health, modules, plugins, sys_h

    health, modules, plugins, sys_health = anyio.run(_gather)

    table = Table(title="Components", show_header=True)
    table.add_column("Компонент", style="bold", width=22)
    table.add_column("Статус", width=12)
    table.add_column("Детали")

    def _row(name: str, ok: bool | None, detail: str = "") -> None:
        if ok is True:
            status = Text("● online", style="green")
        elif ok is False:
            status = Text("○ offline", style="red")
        else:
            status = Text("? unknown", style="yellow")
        table.add_row(name, status, detail)

    # API
    _row("API", health is not None,
         f"v{health.get('version', '?')}  uptime={health.get('uptime', '?')}" if health else "недоступен")

    # Modules
    if isinstance(modules, list):
        ok_m = sum(1 for m in modules if str(m.get("status", "")).lower() in {"running", "ok"})
        total_m = len(modules)
        _row("Modules", ok_m == total_m, f"{ok_m} / {total_m} running")
        # Отдельная строка для каждого не-running модуля
        for m in modules:
            if str(m.get("status", "")).lower() not in {"running", "ok"}:
                table.add_row(
                    f"  └ {m.get('name', '?')}",
                    Text(str(m.get("status", "?")), style="yellow"),
                    "required" if m.get("required") else "optional",
                )
    else:
        _row("Modules", None, "нет данных")

    # Plugins
    if isinstance(plugins, list):
        ok_p = sum(1 for p in plugins if str(p.get("status", "")).lower() == "running")
        _row("Plugins", ok_p == len(plugins) or not plugins,
             f"{ok_p} / {len(plugins)} running")
    else:
        _row("Plugins", None, "нет данных")

    # Storage (из system_health)
    if isinstance(sys_health, dict):
        result = sys_health.get("result") or sys_health
        storage = result.get("storage") if isinstance(result, dict) else None
        if isinstance(storage, dict):
            st = storage.get("status", "unknown")
            _row("Storage", st in {"ok", "healthy"}, st)
        else:
            _row("Storage", None, "нет данных")
    else:
        _row("Storage", None, "Core недоступен")

    console.print(table)


from rich.text import Text  # noqa: E402 — нужен для _show_components


def register(app: typer.Typer) -> None:
    @app.command("status")
    def status(
        watch: bool = typer.Option(False, "--watch", "-w", help="Live-мониторинг (Ctrl+C для выхода)"),
        interval: float = typer.Option(5.0, "--interval", "-n", help="Интервал обновления в секундах"),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
        components: bool = typer.Option(False, "--components", "-c", help="Показать статус каждого компонента отдельно"),
    ) -> None:
        """Статус Core: версия, uptime, плагины, модули. С --watch — live-мониторинг."""
        console = Console()
        if json_out and watch:
            console.print("[red]Ошибка:[/red] --json несовместим с --watch")
            raise typer.Exit(code=2)
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

        if components:
            _show_components(console, client)
            return

        if not watch:
            health, active_plugins, modules_stat, latency_ms = anyio.run(_fetch)
            if json_out:
                if not health:
                    print_json({"ok": False, "error": "Core недоступен"})
                    raise typer.Exit(code=1)
                payload: dict[str, object] = {
                    "ok": True,
                    "version": health.get("version"),
                    "status": health.get("status"),
                    "uptime": health.get("uptime"),
                    "latency_ms": round(latency_ms, 2),
                }
                if active_plugins is not None:
                    payload["plugins_active"] = active_plugins
                if modules_stat is not None:
                    payload["modules"] = {"ok": modules_stat[0], "total": modules_stat[1]}
                print_json(payload)
                return
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
