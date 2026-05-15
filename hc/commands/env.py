from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hc.core_ops import compose_project_from_source, require_docker
from hc.core_source import CoreSource, get_core_source_from_repo, get_core_source_local
from hc.errors import CoreSourcesNotFoundError, HcCliError


# ─── Service catalogue ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Svc:
    name: str
    label: str
    default: bool
    compose_profile: str | None = None


# Postgres is NOT listed here — it's selected via the DB radio button, not the service checkbox.
_SERVICES: dict[str, list[_Svc]] = {
    "dev": [
        _Svc("core-runtime", "core-runtime  (Python бэкенд, build из src)",    default=True),
        _Svc("caddy",        "caddy         (edge proxy / статика)",            default=True),
        _Svc("redis",        "redis         (кэш / event bus)",                 default=False),
    ],
    "dev-reload": [
        _Svc("core-runtime",  "core-runtime   (Python hot-reload + watchfiles)", default=True),
        _Svc("caddy",         "caddy          (edge proxy / статика)",           default=True),
        _Svc("redis",         "redis          (кэш / event bus)",                default=False),
        _Svc("frontend-vite", "frontend-vite  (Vite HMR :15173)",               default=False,
             compose_profile="frontend"),
    ],
    "dev-image": [
        _Svc("core-runtime", "core-runtime   (образ из registry)", default=True),
        _Svc("edge",         "edge           (caddy proxy)",        default=True),
        _Svc("redis",        "redis          (кэш / event bus)",   default=False),
        _Svc("platform-web", "platform-web   (фронтенд образ)",    default=False),
    ],
}

_PROFILES: dict[str, list[str]] = {
    "base":     ["core-runtime", "caddy", "edge"],
    "backend":  ["redis", "core-runtime", "caddy", "edge"],
    "platform": ["core-runtime", "edge", "platform-web"],
    "hmr":      ["redis", "core-runtime", "caddy", "frontend-vite"],
    "full":     ["redis", "core-runtime", "caddy", "edge", "platform-web", "frontend-vite"],
}


# ─── DB options (radio) ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class _DbOption:
    key: str
    label: str
    env: dict[str, str] = field(default_factory=dict)
    service: str | None = None          # extra compose service name
    compose_profile: str | None = None  # docker compose --profile flag for that service


_DB_OPTIONS: list[_DbOption] = [
    _DbOption(
        key="sqlite",
        label="SQLite      (файлы /data/*.db, встроенная, без контейнера)",
        env={"RUNTIME_VAULT_STORAGE_TYPE": "sqlite"},
    ),
    _DbOption(
        key="postgres",
        label="PostgreSQL  (контейнер postgres, порт :5432)",
        env={
            "RUNTIME_VAULT_STORAGE_TYPE": "postgresql",
            # sslmode=disable: skip SSL negotiation for local dev container
            "RUNTIME_VAULT_PG_DSN": (
                "postgresql://homeconsole:homeconsole@postgres:5432/homeconsole"
                "?sslmode=disable"
            ),
        },
        service="postgres",
        compose_profile="postgres",
    ),
]

_DB_KEY_MAP: dict[str, _DbOption] = {o.key: o for o in _DB_OPTIONS}


# ─── Constants ────────────────────────────────────────────────────────────────

_MODE_DEFAULT = "dev-reload"
_MODE_HELP = "dev-reload | dev | dev-image  (по умолчанию: dev-reload = hot-reload)"
_PROFILE_HELP = (
    "Пресет: base | backend | platform | hmr | full  "
    "(без --profile: интерактивный выбор)"
)
_DB_HELP = "sqlite | postgres  (без --db: интерактивный выбор если core-runtime выбран)"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            return p
    return None


def _resolve_source(console: Console) -> CoreSource:
    repo_root = _find_repo_root()
    if repo_root:
        src = get_core_source_from_repo(repo_root)
        if src:
            return src
    src = get_core_source_local()
    if src:
        return src
    raise CoreSourcesNotFoundError(
        message="Исходники Core не найдены.",
        exit_code=1,
        hint="Сделай `hc core init` или запусти из монорепы HomeConsole.",
    )


def _run(cmd: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> None:
    env = {**os.environ, **extra_env} if extra_env else None
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def _get_running_services(compose_file: Path, cwd: Path) -> set[str]:
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(compose_file),
             "ps", "--services", "--filter", "status=running"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if r.returncode == 0:
            return {s.strip() for s in r.stdout.splitlines() if s.strip()}
    except Exception:  # noqa: BLE001
        pass
    return set()


def _pick_services_interactive(available: list[_Svc], running: set[str]) -> list[_Svc]:
    try:
        import questionary
        from questionary import Style as QStyle
    except ImportError:
        raise HcCliError(
            message="Пакет questionary не установлен.",
            exit_code=1,
            hint="pip install questionary",
        )

    style = QStyle([
        ("qmark",       "fg:#00bfff bold"),
        ("question",    "bold"),
        ("pointer",     "fg:#00bfff bold"),
        ("highlighted", "fg:#00bfff bold"),
        ("selected",    "fg:#00ff00"),
        ("instruction", "fg:#808080 italic"),
    ])

    choices = []
    for s in available:
        is_running = s.name in running
        title: object = (
            [("", s.label), ("fg:ansigreen bold", "  ● running")]
            if is_running
            else s.label
        )
        choices.append(
            questionary.Choice(title=title, value=s.name, checked=is_running or s.default)
        )

    result = questionary.checkbox(
        "Выбери сервисы (SPACE = вкл/выкл  ↑↓ = навигация  ENTER = дальше):",
        choices=choices,
        style=style,
    ).ask()

    if result is None:
        raise typer.Abort()
    if not result:
        Console().print("[yellow]Ничего не выбрано — выход.[/yellow]")
        raise typer.Exit(code=0)

    return [s for s in available if s.name in result]


def _pick_db_interactive(running: set[str]) -> _DbOption:
    """Radio-button выбор бэкенда БД."""
    try:
        import questionary
        from questionary import Style as QStyle
    except ImportError:
        raise HcCliError(
            message="Пакет questionary не установлен.",
            exit_code=1,
            hint="pip install questionary",
        )

    style = QStyle([
        ("qmark",       "fg:#00bfff bold"),
        ("question",    "bold"),
        ("pointer",     "fg:#00bfff bold"),
        ("highlighted", "fg:#00bfff bold"),
        ("selected",    "fg:#00ff00"),
        ("instruction", "fg:#808080 italic"),
    ])

    choices = []
    for opt in _DB_OPTIONS:
        is_running = bool(opt.service and opt.service in running)
        title: object = (
            [("", opt.label), ("fg:ansigreen bold", "  ● running")]
            if is_running
            else opt.label
        )
        choices.append(questionary.Choice(title=title, value=opt.key))

    # Default to postgres if it's already running, else sqlite
    pg_running = "postgres" in running
    default = "postgres" if pg_running else "sqlite"

    result = questionary.select(
        "База данных (ENTER = подтвердить):",
        choices=choices,
        default=default,
        style=style,
    ).ask()

    if result is None:
        raise typer.Abort()

    return _DB_KEY_MAP[result]


def _resolve_services(
    *,
    mode: str,
    profile: str | None,
    console: Console,
    running: set[str],
) -> list[_Svc]:
    available = _SERVICES.get(mode, [])
    if not available:
        console.print(f"[red]Ошибка:[/red] неизвестный режим {mode!r}. Допустимые: {' | '.join(_SERVICES)}")
        raise typer.Exit(code=2)

    if profile:
        key = profile.strip().lower()
        preset = _PROFILES.get(key)
        if preset is None:
            console.print(f"[red]Ошибка:[/red] неизвестный профиль {profile!r}. Допустимые: {' | '.join(sorted(_PROFILES))}")
            raise typer.Exit(code=2)
        selected = [s for s in available if s.name in preset]
        if not selected:
            console.print(
                f"[yellow]Профиль {profile!r} не содержит сервисов для режима {mode!r}.[/yellow]\n"
                f"Доступные: {', '.join(s.name for s in available)}"
            )
            raise typer.Exit(code=2)
        return selected

    if sys.stdin.isatty():
        return _pick_services_interactive(available, running)

    return [s for s in available if s.default]


def _resolve_db(
    *,
    db_flag: str | None,
    needs_db: bool,
    running: set[str],
    console: Console,
) -> _DbOption:
    """Resolve DB option: flag → interactive → default sqlite."""
    if db_flag:
        key = db_flag.strip().lower()
        opt = _DB_KEY_MAP.get(key)
        if opt is None:
            console.print(f"[red]Ошибка:[/red] --db {db_flag!r} неизвестен. Допустимые: {' | '.join(_DB_KEY_MAP)}")
            raise typer.Exit(code=2)
        return opt

    if needs_db and sys.stdin.isatty():
        return _pick_db_interactive(running)

    return _DB_KEY_MAP["sqlite"]


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
        if "frontend-vite" in services:
            urls.append(("HMR", "http://localhost:15173"))
        if "postgres" in services:
            urls.append(("PG ", "localhost:5432"))
    elif mode == "dev-image":
        if "edge" in services:
            urls.append(("UI ", "http://localhost:18080"))
        if "core-runtime" in services:
            urls.append(("API", "http://localhost:18000"))
        if "platform-web" in services:
            urls.append(("App", "http://localhost:3000"))

    for label, url in urls:
        console.print(f"  [dim]{label}:[/dim]      [cyan]{url}[/cyan]")

    console.print(f"\n  [dim]compose:[/dim] {compose_file}\n")


# ─── Command registration ─────────────────────────────────────────────────────

def register(app: typer.Typer) -> None:
    env_app = typer.Typer(
        help="Локальное dev-окружение: up / down / logs / restart / status",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @env_app.command("up")
    def env_up(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_HELP),
        db: str | None = typer.Option(None, "--db", help=_DB_HELP),
        pull: bool = typer.Option(False, "--pull/--no-pull", help="docker compose pull перед up"),
        build: bool = typer.Option(False, "--build/--no-build", help="Пересобрать образы перед up"),
        detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Запустить в фоне"),
    ) -> None:
        """
        Поднять dev-окружение.

        Шаг 1: чекбоксы — выбор сервисов (уже запущенные помечены ● running).
        Шаг 2: radio — выбор БД (SQLite / PostgreSQL).

        Примеры:
          hc env up                           # интерактив
          hc env up --profile hmr             # core + caddy + Vite HMR, спросит DB
          hc env up --profile base --db pg    # core + caddy, PostgreSQL, без вопросов
          hc env up --build                   # пересобрать образ и поднять
        """
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            if mode not in _SERVICES:
                console.print(f"[red]Ошибка:[/red] неизвестный режим {mode!r}. Допустимые: {' | '.join(_SERVICES)}")
                raise typer.Exit(code=2)

            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            running = _get_running_services(project.compose_file, project.cwd)

            # Step 1: service checkboxes
            selected = _resolve_services(mode=mode, profile=profile, console=console, running=running)
            service_names = [s.name for s in selected]
            compose_profiles = list({s.compose_profile for s in selected if s.compose_profile})

            # Step 2: DB radio (only when core-runtime is in the selection)
            needs_db = "core-runtime" in service_names
            db_flag = db.strip().lower().replace("pg", "postgres") if db else None
            db_option = _resolve_db(db_flag=db_flag, needs_db=needs_db, running=running, console=console)

            # If postgres selected: activate its compose profile and add to service list.
            # The profile makes `depends_on: postgres: required: false` kick in,
            # so core-runtime waits for postgres to be healthy before starting.
            if needs_db and db_option.service:
                if db_option.compose_profile and db_option.compose_profile not in compose_profiles:
                    compose_profiles.append(db_option.compose_profile)
                if db_option.service not in service_names:
                    service_names.append(db_option.service)

            console.print(
                f"\n[cyan]→[/cyan] env up  "
                f"mode=[bold]{mode}[/bold]  "
                f"db=[bold]{db_option.key}[/bold]  "
                f"services=[bold]{', '.join(service_names)}[/bold]"
            )

            base_cmd = ["docker", "compose", "-f", str(project.compose_file)]
            for cp in compose_profiles:
                base_cmd += ["--profile", cp]

            extra_env = db_option.env or {}

            if pull:
                _run([*base_cmd, "pull", *service_names], cwd=project.cwd, extra_env=extra_env)

            up_cmd = [*base_cmd, "up"]
            if detach:
                up_cmd.append("-d")
            if build:
                up_cmd.append("--build")
            up_cmd += service_names

            _run(up_cmd, cwd=project.cwd, extra_env=extra_env)

            if detach:
                console.print("[green]✓[/green] env up ok")
                _print_summary(
                    mode=mode,
                    compose_file=project.compose_file,
                    services=service_names,
                    was_running=running,
                    db_option=db_option,
                    console=console,
                )

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("down")
    def env_down(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        volumes: bool = typer.Option(False, "--volumes", "-v", help="Удалить volumes (БД, кэш)"),
    ) -> None:
        """Остановить dev-окружение."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            console.print(f"[cyan]→[/cyan] env down  mode=[bold]{mode}[/bold]")
            cmd = ["docker", "compose", "-f", str(project.compose_file), "down"]
            if volumes:
                cmd.append("-v")
            _run(cmd, cwd=project.cwd)
            console.print("[green]✓[/green] env down ok")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("logs")
    def env_logs(
        service: str | None = typer.Argument(None, help="Сервис (пусто = все)"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        follow: bool = typer.Option(False, "-f", "--follow", help="Следить за логами"),
        tail: int = typer.Option(100, "--tail", help="Кол-во последних строк"),
    ) -> None:
        """Логи сервисов dev-окружения."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            cmd = [
                "docker", "compose", "-f", str(project.compose_file),
                "logs", "--tail", str(tail),
            ]
            if follow:
                cmd.append("-f")
            if service:
                cmd.append(service)

            _run(cmd, cwd=project.cwd)

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("restart")
    def env_restart(
        service: str | None = typer.Argument(None, help="Сервис (пусто = все запущенные)"),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        build: bool = typer.Option(False, "--build", help="Пересобрать образ перед рестартом (только для сервисов build из src)"),
    ) -> None:
        """Перезапустить сервис(ы). С --build: пересобрать образ и поднять заново."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            if build:
                # rebuild + up (replaces the container)
                build_targets = [service] if service else []
                console.print(f"[cyan]→[/cyan] build {'[bold]' + service + '[/bold]' if service else 'all'}")
                _run(
                    ["docker", "compose", "-f", str(project.compose_file), "build", *build_targets],
                    cwd=project.cwd,
                )
                up_targets = [service] if service else []
                _run(
                    ["docker", "compose", "-f", str(project.compose_file), "up", "-d", *up_targets],
                    cwd=project.cwd,
                )
                console.print("[green]✓[/green] rebuild + up ok")
                return

            cmd = ["docker", "compose", "-f", str(project.compose_file), "restart"]
            if service:
                cmd.append(service)
                console.print(f"[cyan]→[/cyan] restart [bold]{service}[/bold]")
            else:
                console.print("[cyan]→[/cyan] restart all")

            _run(cmd, cwd=project.cwd)
            console.print("[green]✓[/green] restart ok")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("status")
    def env_status(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Статус контейнеров dev-окружения."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            subprocess.run(  # noqa: S603
                ["docker", "compose", "-f", str(project.compose_file), "ps"],
                cwd=str(project.cwd),
                check=False,
            )
            console.print(f"\n[dim]compose:[/dim] {project.compose_file}")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    app.add_typer(env_app, name="env")
