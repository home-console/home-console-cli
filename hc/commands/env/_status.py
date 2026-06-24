"""Status dashboard, ps printing, dry-run output for env commands."""
from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hc.commands.env._catalog import (
    EnvUpPlan, _DbOption, _STATE_COLOR, KNOWN_ENDPOINTS,
)
from hc.commands.env import _compose
from hc.commands.env._diagnostics import (
    detect_compose_stack_split_for_project,
    _warn_compose_stack_split,
)
from hc.env_state import load_last_env
from hc.hints import ENV_VS_CORE_DOTENV
from hc.json_output import print_json


def _print_env_status_dashboard(console: Console, project: "ComposeProject") -> None:
    """Компактный dashboard: статус каждого сервиса + health + uptime + URL."""
    rows = _compose._compose_ps_rows(project)

    if not rows:
        console.print(
            "[yellow]![/yellow] Стек не запущен. Подними: "
            "[cyan]hc env up[/cyan]"
        )
        console.print(f"\n[dim]compose:[/dim] {project.compose_file}")
        return

    # Сводка по состояниям.
    state_counts: dict[str, int] = {}
    for r in rows:
        state = str(r.get("State") or "unknown").lower()
        state_counts[state] = state_counts.get(state, 0) + 1

    def _state_chip(state: str, count: int) -> str:
        color = _STATE_COLOR.get(state, "white")
        return f"[{color}]{count} {state}[/{color}]"

    summary = "  ".join(_state_chip(s, n) for s, n in sorted(state_counts.items()))
    console.print(f"\n[bold]Стек:[/bold]  {summary}")

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    table.add_column("Сервис", min_width=22)
    table.add_column("State")
    table.add_column("Health")
    table.add_column("Uptime", style="dim")
    table.add_column("URL / порт", style="cyan")

    health_icon = {
        "healthy":   "[green]✓ healthy[/green]",
        "unhealthy": "[red]✗ unhealthy[/red]",
        "starting":  "[yellow]… starting[/yellow]",
        "":          "[dim]—[/dim]",
        "none":      "[dim]—[/dim]",
    }

    state_order = {"running": 0, "restarting": 1, "exited": 2, "dead": 3}
    rows_sorted = sorted(
        rows, key=lambda r: (state_order.get(str(r.get("State") or "").lower(), 9),
                              str(r.get("Service") or ""))
    )

    for r in rows_sorted:
        service = str(r.get("Service") or r.get("Name") or "?")
        state = str(r.get("State") or "?").lower()
        health = str(r.get("Health") or "").lower()
        uptime = str(r.get("RunningFor") or r.get("Status") or "—")
        url = KNOWN_ENDPOINTS.get(service, "")
        ports = str(r.get("Publishers") or r.get("Ports") or "")
        if not url and ports and ports != "[]":
            url = ports

        color = _STATE_COLOR.get(state, "white")
        table.add_row(
            service,
            f"[{color}]{state}[/{color}]",
            health_icon.get(health, f"[dim]{health}[/dim]"),
            uptime,
            url or "—",
        )

    console.print(table)

    # Блок URL endpoints
    running_urls = [
        (str(r.get("Service")), KNOWN_ENDPOINTS.get(str(r.get("Service") or ""), ""))
        for r in rows_sorted
        if str(r.get("State") or "").lower() == "running"
    ]
    running_urls = [(s, u) for s, u in running_urls if u]
    if running_urls:
        console.print("\n[bold]URL:[/bold]")
        for s, u in running_urls:
            console.print(f"  [dim]{s:22}[/dim] {u}")

    # Подсказки при проблемах.
    troubled = [r for r in rows if str(r.get("State") or "").lower() in {"exited", "dead", "restarting"}]
    unhealthy = [r for r in rows if str(r.get("Health") or "").lower() == "unhealthy"]
    if troubled or unhealthy:
        console.print()
        for r in troubled:
            svc = r.get("Service") or "?"
            console.print(
                f"[yellow]![/yellow] {svc}: state={r.get('State')}  → "
                f"[cyan]hc env logs {svc} --tail 200[/cyan]"
            )
        for r in unhealthy:
            svc = r.get("Service") or "?"
            console.print(
                f"[yellow]![/yellow] {svc}: unhealthy  → "
                f"[cyan]hc env logs {svc} --tail 200[/cyan]"
            )
        console.print("[dim]Полная диагностика:[/dim] [cyan]hc doctor[/cyan]")

    project_name = _compose.compose_project_name_from_compose(project)
    split_issue = detect_compose_stack_split_for_project(project_name)
    if split_issue:
        _warn_compose_stack_split(console, split_issue)

    console.print(f"\n[dim]compose:[/dim] {project.compose_file}")


def _print_env_ps(console: Console, project: "ComposeProject", *, json_out: bool = False) -> None:
    rows = _compose._compose_ps_rows(project)
    if json_out:
        print_json(
            {
                "ok": True,
                "compose_file": str(project.compose_file),
                "containers": _compose._env_ps_entries(project),
            }
        )
        return

    if not rows:
        subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(project.compose_file), "ps", "-a"],
            cwd=str(project.cwd),
            check=False,
        )
        console.print(f"\n[dim]compose:[/dim] {project.compose_file}")
        console.print(ENV_VS_CORE_DOTENV)
        return

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    table.add_column("Сервис")
    table.add_column("Состояние")
    table.add_column("Порты")
    table.add_column("URL / хост", style="cyan")

    for entry in _compose._env_ps_entries(project):
        table.add_row(
            entry["service"],
            entry["state"],
            entry["ports"] or "—",
            entry["url_hint"],
        )

    console.print(table)
    console.print(f"\n[dim]compose:[/dim] {project.compose_file}")


def _print_env_up_dry_run(
    console: Console,
    plan: EnvUpPlan,
    *,
    pull: bool,
    build: bool,
    detach: bool,
) -> None:
    console.print("[bold cyan]dry run[/bold cyan] — [dim]env up (ничего не запущено)[/dim]\n")
    console.print(f"  mode      [bold]{plan.mode}[/bold]")
    console.print(f"  db        [bold]{plan.db_option.key}[/bold]")
    console.print(f"  services  [bold]{', '.join(plan.service_names)}[/bold]")
    if plan.compose_profiles:
        console.print(f"  profiles  [bold]{', '.join(sorted(plan.compose_profiles))}[/bold]")
    console.print(f"  compose   [dim]{plan.project.compose_file}[/dim]\n")

    base = _compose._compose_base_cmd(plan)
    extra = plan.db_option.env or {}
    if extra:
        console.print("  [dim]env:[/dim]")
        for k, v in sorted(extra.items()):
            console.print(f"    {k}={v}")
        console.print()

    if pull:
        console.print(f"  [dim]$ {' '.join([*base, 'pull', *plan.service_names])}[/dim]")
    up_cmd = [*base, "up"]
    if detach:
        up_cmd.append("-d")
    if build:
        up_cmd.append("--build")
    up_cmd += plan.service_names
    console.print(f"  [dim]$ {' '.join(up_cmd)}[/dim]")
    last = load_last_env()
    if last and last.mode == plan.mode:
        console.print("\n  [dim]последний выбор:[/dim] " + ", ".join(last.services) + f"  db={last.db}")


def _print_env_down_dry_run(
    console: Console,
    *,
    mode: str,
    project: "ComposeProject",
    running: set[str],
    active_profiles: set[str],
    active_db: str,
    volumes: bool,
) -> None:
    console.print("[bold cyan]dry run[/bold cyan] — [dim]env down (ничего не остановлено)[/dim]\n")
    console.print(f"  mode      [bold]{mode}[/bold]")
    console.print(f"  db        [bold]{active_db}[/bold]")
    if active_profiles:
        console.print(f"  profiles  [bold]{', '.join(sorted(active_profiles))}[/bold]")
    if running:
        console.print(f"  running   [bold]{', '.join(sorted(running))}[/bold]")
    else:
        console.print("  running   [dim](нет запущенных сервисов)[/dim]")

    cmd = ["docker", "compose", "-f", str(project.compose_file)]
    for profile in sorted(active_profiles):
        cmd += ["--profile", profile]
    cmd.append("down")
    if volumes:
        cmd.append("-v")
        if active_db == "sqlite":
            console.print(
                "\n  [yellow]![/yellow] [dim]-v удалит volume core-data (SQLite)[/dim]"
            )
    console.print(f"\n  [dim]$ {' '.join(cmd)}[/dim]")


def _print_summary(
    *,
    mode: str,
    compose_file: Path,
    services: list[str],
    was_running: set[str],
    db_option: _DbOption,
    console: Console,
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", min_width=16)
    table.add_column()

    for name in services:
        status = "[dim]● already running[/dim]" if name in was_running else "[green]● started[/green]"
        table.add_row(name, status)

    console.print()
    console.print(table)

    # DB line
    db_label = "PostgreSQL" if db_option.key == "postgres" else "SQLite"
    console.print(f"\n  [dim]db:[/dim]      {db_label}")

    # URLs
    urls: list[tuple[str, str]] = []
    if mode in ("dev", "dev-reload"):
        if "caddy" in services:
            urls.append(("UI ", "http://localhost:18080"))
        if "core-runtime" in services:
            urls.append(("API", "http://localhost:18000"))
        if "frontend-vite" in services and "caddy" not in services:
            urls.append(("HMR", "http://localhost:15173"))
        if "postgres" in services:
            urls.append(("PG ", "localhost:15432"))
        if "redis" in services:
            urls.append(("RDS", "localhost:16379"))
    elif mode == "dev-image":
        if "edge" in services:
            urls.append(("UI ", "http://localhost:18080"))
        if "core-runtime" in services:
            urls.append(("API", "http://localhost:18000"))
        if "redis" in services:
            urls.append(("RDS", "localhost:16379"))
        if "platform-web" in services:
            urls.append(("App", "http://localhost:3000"))

    for label, url in urls:
        console.print(f"  [dim]{label}:[/dim]      [cyan]{url}[/cyan]")

    if mode in ("dev", "dev-reload") and "frontend-vite" in services and "caddy" in services:
        console.print(
            "  [dim]vite:[/dim]    [dim]HMR через caddy → :18080 "
            "(прямой :15173 — только отладка)[/dim]"
        )

    console.print(f"\n  [dim]compose:[/dim] {compose_file}\n")
