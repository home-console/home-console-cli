from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import anyio
import typer
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client
from hc.json_output import print_json
from hc.config import Config
from hc.core_ops import require_docker
from hc.core_source import get_core_source_from_repo

_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR)\s")


def _find_repo_root() -> Path | None:
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").is_dir():
            return p
    return None


def _resolve_core_root(console: Console) -> Path:
    repo = _find_repo_root()
    if repo:
        src = get_core_source_from_repo(repo)
        if src:
            return Path(src.path)
    console.print(
        "[red]Ошибка:[/red] не найден `core-runtime-service` (запусти из монорепы HomeConsole)."
    )
    raise typer.Exit(code=2)


def _ssh_cmd(ssh: str, remote_cmd: str) -> list[str]:
    return ["ssh", ssh, remote_cmd]


def _run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _prepare_local_staging(core_root: Path, source: Path, *, console: Console) -> Path:
    """Собрать содержимое плагинов в core_root/.hc-plugin-staging (удаляет старый staging)."""
    staging = core_root / ".hc-plugin-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    if source.is_file():
        if source.suffix.lower() != ".zip":
            console.print("[red]Ошибка:[/red] поддерживается только .zip как архив.")
            raise typer.Exit(code=2)
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(staging)
        return staging

    if not source.is_dir():
        console.print(f"[red]Ошибка:[/red] не каталог и не zip: {source}")
        raise typer.Exit(code=2)

    # Каталог: копируем содержимое (как rsync trailing slash).
    for item in source.iterdir():
        dest = staging / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    return staging


def _status_cell(status: str) -> Text:
    s = status.lower()
    t = Text(status)
    if s in {"running", "ok"}:
        t.stylize("green")
    elif s in {"stopped", "paused"}:
        t.stylize("yellow")
    else:
        t.stylize("red")
    return t


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
    plugin_app = typer.Typer(
        help="Управление плагинами",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @plugin_app.command("list")
    def list_plugins(
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        console = Console()
        client = require_client(console)

        data = anyio.run(client.inspector_plugins)
        if not data:
            raise typer.Exit(code=1)

        # Inspector response shape:
        # - {"ok": true, "result": [ ...plugins... ]}  (current)
        # - {"plugins": [...]} / {"result": {"plugins": [...]}} (legacy variants)
        payload = data.get("result") if isinstance(data, dict) else None
        plugins = None
        if isinstance(payload, list):
            plugins = payload
        elif isinstance(payload, dict):
            maybe = payload.get("plugins")
            plugins = maybe if isinstance(maybe, list) else None
        elif isinstance(data, dict):
            maybe = data.get("plugins")
            plugins = maybe if isinstance(maybe, list) else None

        if not isinstance(plugins, list):
            console.print("[red]Ошибка:[/red] не удалось получить список плагинов.")
            raise typer.Exit(code=1)

        if json_out:
            print_json({"ok": True, "plugins": plugins})
            return

        table = Table(title="Plugins")
        table.add_column("Плагин", style="bold")
        table.add_column("Версия")
        table.add_column("Статус")
        table.add_column("Режим")
        table.add_column("Uptime")

        for p in plugins:
            table.add_row(
                str(p.get("name", "")),
                str(p.get("version", "")),
                _status_cell(str(p.get("status", "") or ("running" if p.get("started") else "stopped"))),
                str(p.get("execution_mode", p.get("mode", ""))),
                str(p.get("uptime", "")),
            )
        console.print(table)

    @plugin_app.command("start")
    def start(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.start_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} запущен")

    @plugin_app.command("stop")
    def stop(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        console = Console()
        client = require_client(console)
        data = anyio.run(client.stop_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} остановлен")

    @plugin_app.command("restart")
    def restart(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        """Перезапустить плагин (stop → start). Для hot-reload без остановки используй `reload`."""
        console = Console()
        client = require_client(console)
        stop_res = anyio.run(client.stop_plugin, name)
        if stop_res is None:
            raise typer.Exit(code=1)
        start_res = anyio.run(client.start_plugin, name)
        if start_res is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} перезапущен")

    @plugin_app.command("reload")
    def reload(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        """Hot-reload плагина в памяти (без перезапуска контейнера)."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.reload_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} перезагружен (hot-reload)")

    @plugin_app.command("restart-container")
    def restart_container(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        """Перезапустить контейнер плагина (полный рестарт Docker-контейнера)."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.restart_plugin_container, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} — контейнер перезапущен")

    @plugin_app.command("logs")
    def logs(
        name: str = typer.Argument(..., help="Имя плагина"),
        follow: bool = typer.Option(False, "--follow", help="Следить за логами (stream)"),
        level: str | None = typer.Option(
            None, "--level", help="debug|info|warning|error (локальная фильтрация)"
        ),
    ) -> None:
        console = Console()
        client = require_client(console)
        wanted = level.upper() if level else None

        async def _run() -> int:
            count = 0
            async for line in client.stream_logs(module=name, follow=follow):
                if wanted and wanted not in line.upper():
                    continue
                console.print(_style_line(line))
                count += 1
                if not follow and count >= 100:
                    break
            return 0

        anyio.run(_run)

    @plugin_app.command("info")
    def info(name: str = typer.Argument(..., help="Имя плагина")) -> None:
        """Показать детали плагина."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.get_plugin_info, name)
        if data is None:
            raise typer.Exit(code=1)

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Поле", style="bold dim", width=18)
        table.add_column("Значение", overflow="fold")

        primary_keys = [
            "name",
            "version",
            "status",
            "mode",
            "execution_mode",
            "uptime",
            "description",
            "author",
        ]
        for key in primary_keys:
            val = data.get(key)
            if val is not None:
                if key == "status":
                    color = "green" if str(val).lower() in {"running", "ok"} else "yellow"
                    table.add_row(key, f"[{color}]{val}[/{color}]")
                else:
                    table.add_row(key, str(val))

        extra = {k: v for k, v in data.items() if k not in primary_keys and v is not None}

        console.print(Panel(table, title=f"[bold]Plugin: {name}[/bold]", expand=False))
        if extra:
            console.print(Panel(Pretty(extra, expand_all=True), title="metadata", expand=False))

    @plugin_app.command("sync")
    def sync(
        source: Path | None = typer.Argument(
            None,
            help="Каталог с плагинами или .zip; по умолчанию core-runtime-service/plugins",
        ),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (если не задан — локальный docker compose)"),
        path: str | None = typer.Option(
            None,
            "--path",
            help="Каталог core-runtime-service на сервере (как у hc deploy stack)",
        ),
        mode: str = typer.Option(
            "volume",
            "--mode",
            help="volume: docker compose cp → контейнер (именованный том); bind: rsync в ./plugins на хосте",
        ),
        compose: str = typer.Option(
            "deploy/prod/docker-compose.image.yml",
            "--compose",
            help="Compose-файл относительно корня core-runtime-service",
        ),
        delete: bool = typer.Option(False, "--delete", help="С rsync --delete (только bind и staging на хосте)"),
        restart: bool = typer.Option(
            True,
            "--restart/--no-restart",
            help="После загрузки перезапустить сервис core-runtime",
        ),
    ) -> None:
        """
        Доставить плагины в деплой: в Docker-том `/app/plugins` или в bind-mount `./plugins`.

        Режим **volume** (по умолчанию): файлы попадают в контейнер через `docker compose cp`
        (актуально для `PLUGINS_VOLUME` по умолчанию — именованный том в compose).

        Режим **bind**: rsync на `{path}/plugins` на сервере или локально в `core-runtime-service/plugins`.
        """
        console = Console()
        cfg = Config.load()
        resolved_ssh = (ssh or "").strip() or (cfg.deploy.ssh or "")
        resolved_path = (path or "").strip() or (cfg.deploy.path or "")

        m = mode.strip().lower()
        if m not in {"volume", "bind"}:
            console.print("[red]Ошибка:[/red] --mode должен быть volume или bind.")
            raise typer.Exit(code=2)

        core_root = _resolve_core_root(console)
        default_plugins = core_root / "plugins"
        src = source.expanduser().resolve() if source else default_plugins

        if not src.exists():
            console.print(f"[red]Ошибка:[/red] источник не найден: {src}")
            raise typer.Exit(code=2)
        if src.is_dir() and not any(src.iterdir()) and source is None:
            console.print(f"[yellow]Предупреждение:[/yellow] каталог пустой: {src}")

        compose_file = core_root / compose
        if not compose_file.is_file():
            console.print(f"[red]Ошибка:[/red] compose не найден: {compose_file}")
            raise typer.Exit(code=2)

        compose_part = f"docker compose -f {shlex.quote(compose)}"

        if resolved_ssh:
            if not resolved_path:
                console.print("[red]Ошибка:[/red] для --ssh нужен --path к core-runtime-service на сервере.")
                raise typer.Exit(code=2)

            if m == "bind":
                remote_dest = f"{resolved_path.rstrip('/')}/plugins/"
                rsync_cmd = ["rsync", "-az", "--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r"]
                if delete:
                    rsync_cmd.append("--delete")
                if src.is_file():
                    console.print("[red]Ошибка:[/red] в режиме bind укажи каталог плагинов, не zip.")
                    raise typer.Exit(code=2)
                rsync_cmd.extend([f"{str(src).rstrip('/')}/", f"{resolved_ssh}:{remote_dest}"])
                console.print(
                    f"[cyan]→[/cyan] rsync (bind) → [bold]{resolved_ssh}[/bold]:{remote_dest}"
                )
                p = subprocess.run(rsync_cmd, check=False)  # noqa: S603
                if p.returncode != 0:
                    raise typer.Exit(code=p.returncode)
                if restart:
                    r_restart = (
                        f"cd {shlex.quote(resolved_path)} && {compose_part} restart core-runtime"
                    )
                    _run_cmd(_ssh_cmd(resolved_ssh, r_restart))
                console.print("[green]✓[/green] plugins synced (bind)")
                return

            # volume + remote: staging через rsync → compose cp
            staging_rel = ".hc-plugin-staging"
            remote_base = f"{resolved_ssh}:{resolved_path.rstrip('/')}/{staging_rel}/"
            prep = (
                f"cd {shlex.quote(resolved_path)} && rm -rf {staging_rel} && mkdir -p {staging_rel}"
            )
            _run_cmd(_ssh_cmd(resolved_ssh, prep))
            rsync_cmd = ["rsync", "-az", "--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r"]
            if delete:
                rsync_cmd.append("--delete")

            if src.is_file():
                with tempfile.TemporaryDirectory() as tmp:
                    unpack = Path(tmp) / "unpacked"
                    unpack.mkdir()
                    if src.suffix.lower() != ".zip":
                        console.print("[red]Ошибка:[/red] для архива нужен .zip")
                        raise typer.Exit(code=2)
                    with zipfile.ZipFile(src, "r") as zf:
                        zf.extractall(unpack)
                    rsync_cmd.extend([f"{str(unpack).rstrip('/')}/", remote_base])
                    console.print(
                        f"[cyan]→[/cyan] unpack zip → rsync staging → [bold]{resolved_ssh}[/bold]:…/{staging_rel}/"
                    )
                    p = subprocess.run(rsync_cmd, check=False)  # noqa: S603
                    if p.returncode != 0:
                        raise typer.Exit(code=p.returncode)
            else:
                rsync_cmd.extend([f"{str(src).rstrip('/')}/", remote_base])
                console.print(
                    f"[cyan]→[/cyan] rsync → [bold]{resolved_ssh}[/bold]:…/{staging_rel}/ → docker cp"
                )
                p = subprocess.run(rsync_cmd, check=False)  # noqa: S603
                if p.returncode != 0:
                    raise typer.Exit(code=p.returncode)

            inner_cp = f"{compose_part} cp {staging_rel}/. core-runtime:/app/plugins/"
            # Container runs as nobody; ensure the volume mount is writable for that user.
            inner_cp += f" && {compose_part} exec -T -u 0 core-runtime sh -lc {shlex.quote('chown -R nobody:nogroup /app/plugins || true')}"
            if restart:
                inner_cp += f" && {compose_part} restart core-runtime"
            _run_cmd(_ssh_cmd(resolved_ssh, f"cd {shlex.quote(resolved_path)} && {inner_cp}"))
            console.print("[green]✓[/green] plugins synced into container volume")
            return

        # Локально (без ssh)
        if m == "bind":
            if src.is_file():
                console.print("[red]Ошибка:[/red] в режиме bind укажи каталог, не zip.")
                raise typer.Exit(code=2)
            dest = core_root / "plugins"
            dest.mkdir(parents=True, exist_ok=True)
            rsync_cmd = [
                "rsync",
                "-az",
            ]
            if delete:
                rsync_cmd.append("--delete")
            rsync_cmd.extend([f"{str(src).rstrip('/')}/", f"{str(dest).rstrip('/')}/"])
            console.print(f"[cyan]→[/cyan] rsync (bind) → {dest}")
            p = subprocess.run(rsync_cmd, check=False)  # noqa: S603
            if p.returncode != 0:
                raise typer.Exit(code=p.returncode)
            if restart:
                require_docker(console)
                _run_cmd(
                    ["sh", "-lc", f"cd {shlex.quote(str(core_root))} && {compose_part} restart core-runtime"],
                )
            console.print("[green]✓[/green] plugins synced (local bind)")
            return

        require_docker(console)
        _prepare_local_staging(core_root, src, console=console)
        inner_cp = f"{compose_part} cp .hc-plugin-staging/. core-runtime:/app/plugins/"
        inner_cp += f" && {compose_part} exec -T -u 0 core-runtime sh -lc {shlex.quote('chown -R nobody:nogroup /app/plugins || true')}"
        if restart:
            inner_cp += f" && {compose_part} restart core-runtime"
        console.print("[cyan]→[/cyan] docker compose cp → core-runtime:/app/plugins/")
        _run_cmd(["sh", "-lc", inner_cp], cwd=core_root)
        console.print("[green]✓[/green] plugins synced into local container volume")

    from hc.commands.plugin_new import register_new
    register_new(plugin_app)

    # capabilities subgroup
    cap_app = typer.Typer(
        help="Инспекция capability registry",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @cap_app.command("list")
    def cap_list(
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Показать все зарегистрированные capability и их провайдеры."""
        console = Console()
        client = require_client(console)
        caps = anyio.run(client.list_capabilities)
        if caps is None:
            console.print("[red]Ошибка: не удалось получить список capabilities.[/red]")
            raise typer.Exit(code=1)
        if json_out:
            from hc.json_output import print_json
            print_json({"ok": True, "capabilities": caps})
            return
        from rich.table import Table
        table = Table(title=f"Capabilities ({len(caps)})")
        table.add_column("ID", style="bold cyan")
        table.add_column("Провайдеры")
        table.add_column("Local")
        table.add_column("Remote")
        for c in caps:
            providers = [p.get("name", "?") for p in c.get("providers", [])]
            table.add_row(
                c.get("id", ""),
                ", ".join(providers) or "—",
                str(c.get("local_provider_count", 0)),
                str(c.get("remote_provider_count", 0)),
            )
        console.print(table)

    @cap_app.command("who-provides")
    def cap_who_provides(
        cap_id: str = typer.Argument(..., help="ID capability (напр. oauth:yandex)"),
    ) -> None:
        """Какой плагин предоставляет capability."""
        console = Console()
        client = require_client(console)
        caps = anyio.run(client.list_capabilities)
        if caps is None:
            raise typer.Exit(code=1)
        found = [c for c in caps if c.get("id", "") == cap_id]
        if not found:
            console.print(f"[yellow]Capability {cap_id!r} не зарегистрирован.[/yellow]")
            raise typer.Exit(code=1)
        cap = found[0]
        console.print(f"[bold]{cap_id}[/bold]")
        for p in cap.get("providers", []):
            ptype = p.get("type", "local")
            color = "green" if ptype == "local" else "yellow"
            console.print(f"  [{color}]{ptype}[/{color}]  {p.get('name', '?')}")

    plugin_app.add_typer(cap_app, name="capabilities")

    from hc.commands.plugin_dev import register_dev
    register_dev(plugin_app)

    app.add_typer(plugin_app, name="plugin")

