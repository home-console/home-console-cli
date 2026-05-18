from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
import httpx
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from hc.config import Config
from hc.commands.connect import connect_and_save
from hc.commands._client_helpers import require_client
from hc.core_source import get_core_source_from_repo, get_core_source_local
from hc.core_ops import compose_project_from_source, core_up, require_docker
from hc import __version__
from hc.hints import SETUP_ENV_HINT
from hc.setup_runner import SetupProcess, start_background
from hc.update_check import get_update_notification, print_update_banner


async def _probe(url: str, verify_ssl: bool) -> bool:
    try:
        async with httpx.AsyncClient(base_url=url, timeout=5.0, verify=verify_ssl) as c:
            r = await c.get("/api/health")
    except httpx.RequestError:
        return False
    return r.status_code in {200, 401, 403}


async def _probe_ports(host: str, ports: list[int], verify_ssl: bool) -> list[int]:
    results = [False] * len(ports)

    async def _try(idx: int, p: int) -> None:
        results[idx] = await _probe(f"http://{host}:{p}", verify_ssl)

    async with anyio.create_task_group() as tg:
        for i, p in enumerate(ports):
            tg.start_soon(_try, i, p)

    return [ports[i] for i, ok in enumerate(results) if ok]


def _pick_port(host: str, cfg: Config) -> int:
    suggested = cfg.core.port
    if host == "localhost" and suggested == 8080:
        suggested = 18000
    return suggested or 8080


def _autofix_port(host: str, port: int, verify_ssl: bool) -> tuple[int, str] | None:
    candidates = list(dict.fromkeys([port, 18000, 8000, 8080]))
    responding = anyio.run(_probe_ports, host, candidates, verify_ssl)
    if not responding:
        return None
    found = responding[0]
    url = f"http://{host}:{found}"
    if found != port and not typer.confirm(
        f"Core отвечает на порту {found}. Использовать его вместо {port}?", default=True
    ):
        return None
    return found, url


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


def _offer_shell_completion(console: Console) -> None:
    if not sys.stdin.isatty():
        return
    shells = ("bash", "zsh", "fish")
    shell = "bash"
    try:
        from shellingham import detect_shell

        _path, name = detect_shell()
        if name in shells:
            shell = name
    except Exception:
        pass

    console.print("\n[bold]Автодополнение[/bold]")
    console.print(
        f"  Tab: [cyan]hc --install-completion {shell}[/cyan]\n"
        f"  Или: [dim]eval \"$(hc --show-completion {shell})\"[/dim]"
    )
    if not typer.confirm(f"Запустить установку completion для {shell}?", default=False):
        return

    hc_bin = shutil.which("hc")
    cmd = (
        [hc_bin, "--install-completion", shell]
        if hc_bin
        else [sys.executable, "-m", "hc.main", "--install-completion", shell]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.stdout.strip():
        console.print(proc.stdout)
    if proc.returncode != 0:
        hint = (proc.stderr or proc.stdout or "не удалось").strip()
        console.print(f"[yellow]{hint}[/yellow]")
        console.print(f"[dim]Вручную:[/dim] hc --install-completion {shell}")


def _maybe_upgrade_cli(console: Console) -> None:
    latest = get_update_notification(__version__)
    if not latest:
        return
    console.print(
        f"\n[yellow]→ На PyPI доступна homeconsole-cli {latest}[/yellow] "
        f"(сейчас {__version__})"
    )
    if typer.confirm("Обновить CLI сейчас (pipx / pip)?", default=True):
        from hc.commands.cli_version import run_cli_upgrade

        run_cli_upgrade(console)


def run_setup(background: bool) -> None:
    console = Console()
    print_update_banner(console, __version__)
    console.print(SETUP_ENV_HINT)
    console.print()
    cfg = Config.load()

    host = typer.prompt("Host", default=cfg.core.host or "localhost")
    port = int(typer.prompt("Port", default=str(_pick_port(host, cfg))))
    base_url = f"http://{host}:{port}"

    fixed = _autofix_port(host, port, cfg.core.verify_ssl)
    if fixed:
        port, base_url = fixed

    ok = anyio.run(_probe, base_url, cfg.core.verify_ssl)
    if not ok:
        console.print("[yellow]Core не запущен или недоступен на указанном адресе.[/yellow]")
        sp = _maybe_start_core(console, background=background)
        if sp and background:
            console.print("[green]✓[/green] CoreRuntime запускается в фоне.")
            console.print("Когда поднимется (10–20 сек), выполни:")
            console.print("  [bold cyan]hc connect localhost --port 18000[/bold cyan]")
            console.print("Логи: [dim]hc setup logs --follow[/dim]")
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
            console.print(SETUP_ENV_HINT)
            raise typer.Exit(code=1)

    token = getpass.getpass("Token: ").strip()
    if not token:
        console.print("[red]Ошибка: токен не задан[/red]")
        raise typer.Exit(code=1)

    health = connect_and_save(host=host, port=port, token=token)
    if not health:
        raise typer.Exit(code=1)
    client = require_client(console)

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
    SetupProcess.cleanup()
    _maybe_upgrade_cli(console)
    _offer_shell_completion(console)

