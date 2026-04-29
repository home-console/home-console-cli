from __future__ import annotations

import getpass
import shutil
import subprocess
from pathlib import Path

import anyio
import httpx
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from hc.client import HCClient
from hc.config import Config
from hc.commands.connect import connect_and_save
from hc.core_source import get_core_source_from_repo, get_core_source_local
from hc.core_ops import compose_project_from_source, core_up, require_docker
from hc.setup_runner import SetupProcess, start_background


async def _probe(url: str, verify_ssl: bool) -> bool:
    try:
        async with httpx.AsyncClient(base_url=url, timeout=5.0, verify=verify_ssl) as c:
            r = await c.get("/api/health")
    except httpx.RequestError:
        return False
    return r.status_code in {200, 401, 403}


def _pick_port(host: str, cfg: Config) -> int:
    suggested = cfg.core.port
    if host == "localhost" and suggested == 8080:
        suggested = 18000
    return suggested or 8080


def _autofix_port(console: Console, host: str, port: int, verify_ssl: bool) -> tuple[int, str] | None:
    common = [port, 18000, 8000, 8080]
    seen: set[int] = set()
    for p in common:
        if p in seen:
            continue
        seen.add(p)
        url = f"http://{host}:{p}"
        if anyio.run(_probe, url, verify_ssl):
            if p != port and typer.confirm(
                f"Core отвечает на порту {p}. Использовать его вместо {port}?", default=True
            ):
                return p, url
            if p == port:
                return p, url
    return None


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            return p
    return None


def _maybe_start_core(console: Console, background: bool) -> SetupProcess | None:
    repo_root = _find_repo_root()
    core_src = None
    if repo_root:
        core_src = get_core_source_from_repo(repo_root)
    if not core_src:
        core_src = get_core_source_local()
    if not core_src:
        return None

    require_docker(console)
    project = compose_project_from_source(console, core_src)

    if not typer.confirm("Попробовать поднять CoreRuntime автоматически (docker compose)?", default=True):
        return None

    if background:
        # В фоне запускаем docker compose напрямую (так же, как `hc core up`).
        cmd = ["docker", "compose", "-f", str(project.compose_file), "up", "-d", "core-runtime"]
        return start_background(cmd, cwd=project.cwd)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        t = p.add_task("Запускаю CoreRuntime...", total=None)
        try:
            core_up(console, project, no_ui=True)
        except typer.Exit:
            p.update(t, description="Не получилось запустить CoreRuntime")
            console.print("[red]Ошибка: не удалось поднять CoreRuntime автоматически.[/red]")
            console.print("Попробуй вручную: `hc core up` (или `docker compose ... up -d core-runtime`)")
            raise typer.Exit(code=1)
        p.update(t, description="CoreRuntime запущен")
    return SetupProcess.load()


def run_setup(background: bool) -> None:
    console = Console()
    cfg = Config.load()

    host = typer.prompt("Host", default=cfg.core.host or "localhost")
    port = int(typer.prompt("Port", default=str(_pick_port(host, cfg))))
    base_url = f"http://{host}:{port}"

    fixed = _autofix_port(console, host, port, cfg.core.verify_ssl)
    if fixed:
        port, base_url = fixed

    ok = anyio.run(_probe, base_url, cfg.core.verify_ssl)
    if not ok:
        console.print("[yellow]Core не запущен или недоступен на указанном адресе.[/yellow]")
        sp = _maybe_start_core(console, background=background)
        if sp and background:
            console.print("[green]✓[/green] Запустил CoreRuntime в фоне.")
            console.print("Дай ему 10–20 секунд, потом продолжим подключение.")
            console.print("Смотри логи: `hc setup logs --follow`")
            raise typer.Exit(code=0)
        if sp:
            host, port = "localhost", 18000
            base_url = f"http://{host}:{port}"
            if not anyio.run(_probe, base_url, cfg.core.verify_ssl):
                console.print("[red]Ошибка: CoreRuntime всё ещё не отвечает после запуска.[/red]")
                raise typer.Exit(code=1)
        else:
            console.print("Подними CoreRuntime и повтори `hc setup` (или используй `hc connect`).")
            console.print("Подсказка: в этом репо обычно `http://localhost:18000`.")
            raise typer.Exit(code=1)

    token = getpass.getpass("Token: ").strip()
    if not token:
        console.print("[red]Ошибка: токен не задан[/red]")
        raise typer.Exit(code=1)

    health = connect_and_save(host=host, port=port, token=token)
    if not health:
        raise typer.Exit(code=1)
    client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)

    if typer.confirm("Установить базовые плагины (ui, automation)?", default=True):
        async def _install_base() -> None:
            with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
                t = p.add_task("Установка ui...", total=None)
                async for msg in client.install_plugin("ui"):
                    p.update(t, description=msg or "Установка ui...")
                p.update(t, description="Установка automation...")
                async for msg in client.install_plugin("automation"):
                    p.update(t, description=msg or "Установка automation...")

        anyio.run(_install_base)

    console.print(f"[green]✓[/green] Готово. Подключено к {host}:{port}.")

