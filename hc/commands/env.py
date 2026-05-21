from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hc.config import Config
from hc.core_ops import ComposeProject, compose_project_from_source, require_docker
from hc.core_source import CoreSource, get_core_source_from_repo, get_core_source_local, init_core_source
from hc.env_state import load_last_env, save_last_env
from hc.errors import CoreSourcesNotFoundError, HcCliError
from hc.hints import ENV_STACK_HELP, ENV_VS_CORE_DOTENV
from hc.json_output import print_json


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


@dataclass(frozen=True)
class EnvUpPlan:
    mode: str
    service_names: list[str]
    compose_profiles: list[str]
    db_option: _DbOption
    project: ComposeProject
    running: set[str]


# ─── Constants ────────────────────────────────────────────────────────────────

_MODE_DEFAULT = "dev-reload"
_MODE_HELP = "dev-reload | dev | dev-image  (по умолчанию: dev-reload = hot-reload)"
_PROFILE_HELP = (
    "Пресет: base | backend | platform | hmr | full  "
    "(без --profile: интерактивный выбор)"
)
_DB_HELP = "sqlite | postgres  (без --db: интерактивный выбор если core-runtime выбран)"


# ─── Helpers ──────────────────────────────────────────────────────────────────

# Known siblings of core-runtime-service that confirm we're in the monorepo root.
# Without them, a standalone core-runtime-service clone in any parent dir would be mistaken for monorepo.
_MONOREPO_SIBLINGS = frozenset({"home-console-cli", "packages", "platform-home-console"})


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            if any((p / s).exists() for s in _MONOREPO_SIBLINGS):
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

    # On a fresh machine: offer to auto-clone core-runtime-service.
    from hc.constants import CORE_SRC_DIR, DEFAULT_CORE_REPO
    console.print(f"[yellow]Исходники Core не найдены.[/yellow] ({CORE_SRC_DIR})")

    if sys.stdin.isatty():
        try:
            import questionary
            answer = questionary.confirm(
                f"Скачать core-runtime-service в {CORE_SRC_DIR}?",
                default=True,
            ).ask()
        except ImportError:
            answer = None

        if answer is None:
            raise typer.Abort()
        if not answer:
            raise CoreSourcesNotFoundError(
                message="Исходники Core не найдены.",
                exit_code=1,
                hint=f"Запусти: hc core init  (клонирует {DEFAULT_CORE_REPO})",
            )

        return init_core_source(console, None, None)

    raise CoreSourcesNotFoundError(
        message="Исходники Core не найдены.",
        exit_code=1,
        hint=f"Запусти: hc core init  (клонирует {DEFAULT_CORE_REPO})",
    )


def _run(cmd: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> None:
    env = {**os.environ, **extra_env} if extra_env else None
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=False)  # noqa: S603
    if p.returncode != 0:
        raise typer.Exit(code=p.returncode)


def pull_core_source(src: CoreSource, console: Console, *, quiet: bool = False) -> bool:
    """
    git pull --ff-only при чистом рабочем дереве.
    Возвращает True, если были новые коммиты.
    """
    status = subprocess.run(  # noqa: S603
        ["git", "status", "--porcelain"],
        cwd=str(src.path),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if status.returncode != 0:
        msg = "Не git-репозиторий или git недоступен."
        if quiet:
            return False
        console.print(f"[red]Ошибка:[/red] {msg}")
        raise typer.Exit(code=1)

    if status.stdout.strip():
        if quiet:
            return False
        console.print(
            "[yellow]![/yellow] Рабочее дерево core-runtime-service не чистое — "
            "сначала закоммить или stash изменения."
        )
        raise typer.Exit(code=1)

    pull = subprocess.run(  # noqa: S603
        ["git", "pull", "--ff-only"],
        cwd=str(src.path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if pull.returncode != 0:
        if quiet:
            return False
        err = (pull.stderr or pull.stdout or "git pull failed").strip()
        console.print(f"[red]Ошибка git pull:[/red] {err}")
        raise typer.Exit(code=pull.returncode)

    out = (pull.stdout or "").strip()
    if "Already up to date" in out or "Уже актуально" in out:
        if not quiet:
            console.print("[green]✓[/green] core-runtime-service уже актуален")
        return False

    if not quiet:
        console.print(f"[green]✓[/green] core-runtime-service обновлён")
        if out:
            for line in out.splitlines()[-3:]:
                console.print(f"  [dim]{line}[/dim]")
    return True


def _try_pull_source(src: CoreSource, console: Console) -> None:
    """Тихий pull перед env up — ошибки не критичны."""
    try:
        pull_core_source(src, console, quiet=True)
    except typer.Exit:
        pass
    except Exception:  # noqa: BLE001
        pass


def _compose_with_profiles(
    project: ComposeProject,
    running: set[str],
) -> list[str]:
    cmd = ["docker", "compose", "-f", str(project.compose_file)]
    for profile in sorted(_detect_active_profiles(running)):
        cmd += ["--profile", profile]
    return cmd


_KNOWN_ENDPOINTS: dict[str, str] = {
    "core-runtime": "http://localhost:18000",
    "caddy": "http://localhost:18080",
    "edge": "http://localhost:18080",
    "frontend-vite": "http://localhost:15173",
    "postgres": "localhost:5432",
    "platform-web": "http://localhost:3000",
    "redis": "localhost:6379",
}


def _compose_ps_rows(project: ComposeProject) -> list[dict[str, object]]:
    import json

    r = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(project.compose_file),
            "ps",
            "-a",
            "--format",
            "json",
        ],
        cwd=str(project.cwd),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    rows: list[dict[str, object]] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows


def _env_ps_entries(project: ComposeProject) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for row in _compose_ps_rows(project):
        service = str(row.get("Service") or row.get("Name") or "?")
        ports = str(row.get("Publishers") or row.get("Ports") or "")
        if ports in ("", "[]"):
            ports = ""
        entries.append(
            {
                "service": service,
                "state": str(row.get("State") or row.get("Status") or "?"),
                "ports": ports,
                "url_hint": _KNOWN_ENDPOINTS.get(service, ""),
            }
        )
    return entries


def _print_env_ps(console: Console, project: ComposeProject, *, json_out: bool = False) -> None:
    rows = _compose_ps_rows(project)
    if json_out:
        print_json(
            {
                "ok": True,
                "compose_file": str(project.compose_file),
                "containers": _env_ps_entries(project),
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

    for entry in _env_ps_entries(project):
        table.add_row(
            entry["service"],
            entry["state"],
            entry["ports"] or "—",
            entry["url_hint"],
        )

    console.print(table)
    console.print(f"\n[dim]compose:[/dim] {project.compose_file}")


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


def _warn_orphan_volumes(console: Console, cwd: Path) -> None:
    """After down -v, check for volumes that compose didn't remove (not in top-level volumes:)."""
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        project = cwd.name  # compose project name = directory name
        orphans = [
            v for v in r.stdout.splitlines()
            if v.startswith(f"{project}_") or v.startswith("dev_")
        ]
        if orphans:
            console.print(f"[yellow]![/yellow] Volumes не удалены (нет в top-level volumes секции compose):")
            for v in orphans:
                console.print(f"  [dim]docker volume rm {v}[/dim]")
    except Exception:  # noqa: BLE001
        pass


def _detect_active_profiles(running: set[str]) -> set[str]:
    """Return compose profiles that have at least one running container."""
    profiles: set[str] = set()
    for svcs in _SERVICES.values():
        for s in svcs:
            if s.compose_profile and s.name in running:
                profiles.add(s.compose_profile)
    for opt in _DB_OPTIONS:
        if opt.compose_profile and opt.service and opt.service in running:
            profiles.add(opt.compose_profile)
    return profiles


def _detect_active_db(running: set[str]) -> str:
    """Return the DB key ('postgres' | 'sqlite') based on running containers."""
    for opt in _DB_OPTIONS:
        if opt.service and opt.service in running:
            return opt.key
    return "sqlite"


def _pick_services_interactive(
    available: list[_Svc],
    running: set[str],
    *,
    preferred: set[str] | None = None,
) -> list[_Svc]:
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
        checked = is_running
        if not checked and preferred and s.name in preferred:
            checked = True
        if not checked:
            checked = s.default
        choices.append(questionary.Choice(title=title, value=s.name, checked=checked))

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


def _pick_db_interactive(running: set[str], *, preferred_db: str | None = None) -> _DbOption:
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

    pg_running = "postgres" in running
    if pg_running:
        default = "postgres"
    elif preferred_db and preferred_db in _DB_KEY_MAP:
        default = preferred_db
    else:
        default = "sqlite"

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

    last = load_last_env()
    preferred = set(last.services) if last and last.mode == mode else None

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
        return _pick_services_interactive(available, running, preferred=preferred)

    if preferred:
        picked = [s for s in available if s.name in preferred]
        if picked:
            return picked
    return [s for s in available if s.default]


def _resolve_db(
    *,
    mode: str,
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
        last = load_last_env()
        preferred_db = last.db if last and last.mode == mode else None
        return _pick_db_interactive(running, preferred_db=preferred_db)

    last = load_last_env()
    if needs_db and last and last.mode == mode and last.db in _DB_KEY_MAP:
        return _DB_KEY_MAP[last.db]
    return _DB_KEY_MAP["sqlite"]


def _resolve_env_up_plan(
    *,
    console: Console,
    mode: str,
    profile: str | None,
    db: str | None,
    src: CoreSource,
) -> EnvUpPlan:
    project = compose_project_from_source(console, src, mode=mode)
    running = _get_running_services(project.compose_file, project.cwd)

    selected = _resolve_services(mode=mode, profile=profile, console=console, running=running)
    service_names = [s.name for s in selected]
    compose_profiles = list({s.compose_profile for s in selected if s.compose_profile})

    needs_db = "core-runtime" in service_names
    db_flag = db.strip().lower().replace("pg", "postgres") if db else None
    db_option = _resolve_db(
        mode=mode,
        db_flag=db_flag,
        needs_db=needs_db,
        running=running,
        console=console,
    )

    if needs_db and db_option.service:
        if db_option.compose_profile and db_option.compose_profile not in compose_profiles:
            compose_profiles.append(db_option.compose_profile)
        if db_option.service not in service_names:
            service_names.append(db_option.service)

    return EnvUpPlan(
        mode=mode,
        service_names=service_names,
        compose_profiles=compose_profiles,
        db_option=db_option,
        project=project,
        running=running,
    )


def _compose_base_cmd(plan: EnvUpPlan) -> list[str]:
    cmd = ["docker", "compose", "-f", str(plan.project.compose_file)]
    for cp in sorted(plan.compose_profiles):
        cmd += ["--profile", cp]
    return cmd


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

    base = _compose_base_cmd(plan)
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
    project: ComposeProject,
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
        help=ENV_STACK_HELP,
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
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план без запуска compose"),
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
            if not dry_run:
                require_docker(console)
            mode = mode.strip().lower()
            if mode not in _SERVICES:
                console.print(f"[red]Ошибка:[/red] неизвестный режим {mode!r}. Допустимые: {' | '.join(_SERVICES)}")
                raise typer.Exit(code=2)

            src = _resolve_source(console)
            if not dry_run:
                _try_pull_source(src, console)

            plan = _resolve_env_up_plan(
                console=console,
                mode=mode,
                profile=profile,
                db=db,
                src=src,
            )
            save_last_env(
                mode=plan.mode,
                services=plan.service_names,
                db=plan.db_option.key,
            )

            if dry_run:
                _print_env_up_dry_run(console, plan, pull=pull, build=build, detach=detach)
                return

            console.print(
                f"\n[cyan]→[/cyan] env up  "
                f"mode=[bold]{plan.mode}[/bold]  "
                f"db=[bold]{plan.db_option.key}[/bold]  "
                f"services=[bold]{', '.join(plan.service_names)}[/bold]"
            )

            base_cmd = _compose_base_cmd(plan)
            extra_env = plan.db_option.env or {}

            if pull:
                _run(
                    [*base_cmd, "pull", *plan.service_names],
                    cwd=plan.project.cwd,
                    extra_env=extra_env,
                )

            up_cmd = [*base_cmd, "up"]
            if detach:
                up_cmd.append("-d")
            if build:
                up_cmd.append("--build")
            up_cmd += plan.service_names

            _run(up_cmd, cwd=plan.project.cwd, extra_env=extra_env)

            if detach:
                console.print("[green]✓[/green] env up ok")
                _print_summary(
                    mode=plan.mode,
                    compose_file=plan.project.compose_file,
                    services=plan.service_names,
                    was_running=plan.running,
                    db_option=plan.db_option,
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
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать план без остановки compose"),
    ) -> None:
        """Остановить dev-окружение (автоматически определяет активные профили)."""
        console = Console()
        try:
            if not dry_run:
                require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            running = _get_running_services(project.compose_file, project.cwd)
            active_profiles = _detect_active_profiles(running)
            active_db = _detect_active_db(running)

            if dry_run:
                _print_env_down_dry_run(
                    console,
                    mode=mode,
                    project=project,
                    running=running,
                    active_profiles=active_profiles,
                    active_db=active_db,
                    volumes=volumes,
                )
                return

            profile_hint = f"  profiles=[bold]{', '.join(sorted(active_profiles))}[/bold]" if active_profiles else ""
            console.print(f"[cyan]→[/cyan] env down  mode=[bold]{mode}[/bold]  db=[bold]{active_db}[/bold]{profile_hint}")

            cmd = ["docker", "compose", "-f", str(project.compose_file)]
            for profile in sorted(active_profiles):
                cmd += ["--profile", profile]
            cmd.append("down")

            if volumes:
                if active_db == "sqlite" and sys.stdin.isatty():
                    console.print(
                        "[yellow]![/yellow] SQLite хранит данные в volume [bold]core-data[/bold] — "
                        "они будут удалены вместе с остальными volumes."
                    )
                    try:
                        import questionary
                        confirmed = questionary.confirm(
                            "Удалить volumes (данные SQLite будут потеряны)?",
                            default=False,
                        ).ask()
                    except ImportError:
                        confirmed = True
                    if not confirmed:
                        console.print("[dim]Volumes оставлены.[/dim]")
                        raise typer.Exit(code=0)
                cmd.append("-v")

            _run(cmd, cwd=project.cwd)
            console.print("[green]✓[/green] env down ok")

            if volumes:
                _warn_orphan_volumes(console, project.cwd)

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

    @env_app.command("rebuild")
    def env_rebuild(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        profile: str | None = typer.Option(None, "--profile", "-p", help=_PROFILE_HELP),
        no_cache: bool = typer.Option(False, "--no-cache", help="Сборка без кэша Docker"),
    ) -> None:
        """
        Пересобрать образы и перезапустить сервисы (интерактивный выбор).

        Примеры:
          hc env rebuild                          # интерактив
          hc env rebuild --profile base           # core + caddy без вопросов
          hc env rebuild --no-cache               # интерактив, без кэша
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

            selected = _resolve_services(mode=mode, profile=profile, console=console, running=running)
            service_names = [s.name for s in selected]

            console.print(
                f"\n[cyan]→[/cyan] env rebuild  "
                f"mode=[bold]{mode}[/bold]  "
                f"services=[bold]{', '.join(service_names)}[/bold]"
                + ("  [dim]--no-cache[/dim]" if no_cache else "")
            )

            base_cmd = ["docker", "compose", "-f", str(project.compose_file)]

            build_cmd = [*base_cmd, "build"]
            if no_cache:
                build_cmd.append("--no-cache")
            build_cmd += service_names
            _run(build_cmd, cwd=project.cwd)

            _run([*base_cmd, "up", "-d", *service_names], cwd=project.cwd)
            console.print(f"[green]✓[/green] rebuild ok")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("pull")
    def env_pull() -> None:
        """Обновить исходники core-runtime-service (git pull --ff-only)."""
        console = Console()
        try:
            src = _resolve_source(console)
            pull_core_source(src, console, quiet=False)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("ps")
    def env_ps(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод в JSON"),
    ) -> None:
        """Контейнеры dev-стека: состояние, порты и подсказки URL."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            _print_env_ps(console, project, json_out=json_out)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("exec")
    def env_exec(
        service: str = typer.Argument(..., help="Имя сервиса в compose"),
        command: list[str] = typer.Argument(
            None,
            help="Команда внутри контейнера (по умолчанию sh)",
        ),
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Выполнить команду в контейнере (по умолчанию интерактивный sh)."""
        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)
            running = _get_running_services(project.compose_file, project.cwd)
            base = _compose_with_profiles(project, running)
            exec_cmd = [*base, "exec", "-it", service]
            exec_cmd.extend(command if command else ["sh"])
            console.print(f"[dim]$ {' '.join(exec_cmd)}[/dim]")
            p = subprocess.run(exec_cmd, cwd=str(project.cwd), check=False)  # noqa: S603
            raise typer.Exit(code=p.returncode)
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("status")
    def env_status(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Статус контейнеров dev-окружения (сырой docker compose ps)."""
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
            console.print(ENV_VS_CORE_DOTENV)

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("stats")
    def env_stats(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
        watch: bool = typer.Option(False, "--watch", "-w", help="Обновлять каждые N секунд"),
        interval: float = typer.Option(3.0, "--interval", "-n", help="Интервал обновления (сек)"),
    ) -> None:
        """CPU%, RAM, NET I/O контейнеров dev-окружения."""
        import json
        import time
        from rich.live import Live
        from rich.table import Table

        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            def _stats_table() -> Table:
                r = subprocess.run(  # noqa: S603
                    ["docker", "compose", "-f", str(project.compose_file),
                     "stats", "--no-stream", "--format", "{{json .}}"],
                    cwd=str(project.cwd),
                    capture_output=True, text=True, check=False,
                )
                table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
                table.add_column("Сервис")
                table.add_column("CPU%",     justify="right")
                table.add_column("RAM",      justify="right")
                table.add_column("RAM%",     justify="right")
                table.add_column("NET I/O",  justify="right")
                table.add_column("BLOCK I/O",justify="right")
                table.add_column("PIDs",     justify="right")

                for line in r.stdout.strip().splitlines():
                    try:
                        d = json.loads(line)
                        cpu_s = d.get("CPUPerc", "0%")
                        try:
                            cpu_f = float(cpu_s.rstrip("%"))
                            cpu_color = "red" if cpu_f > 80 else "yellow" if cpu_f > 40 else "green"
                        except ValueError:
                            cpu_color = "white"
                        table.add_row(
                            d.get("Name", "?"),
                            f"[{cpu_color}]{cpu_s}[/{cpu_color}]",
                            d.get("MemUsage", "?"),
                            d.get("MemPerc", "?"),
                            d.get("NetIO", "?"),
                            d.get("BlockIO", "?"),
                            d.get("PIDs", "?"),
                        )
                    except (json.JSONDecodeError, KeyError):
                        pass
                return table

            if watch:
                with Live(refresh_per_second=1, screen=False) as live:
                    while True:
                        live.update(_stats_table())
                        time.sleep(interval)
            else:
                console.print(_stats_table())

        except (KeyboardInterrupt, typer.Abort):
            pass
        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("health")
    def env_health(
        mode: str = typer.Option(_MODE_DEFAULT, "--mode", "-m", help=_MODE_HELP),
    ) -> None:
        """Healthcheck статус каждого сервиса окружения."""
        import json
        from rich.table import Table

        console = Console()
        try:
            require_docker(console)
            mode = mode.strip().lower()
            src = _resolve_source(console)
            project = compose_project_from_source(console, src, mode=mode)

            r = subprocess.run(  # noqa: S603
                ["docker", "compose", "-f", str(project.compose_file),
                 "ps", "--format", "json"],
                cwd=str(project.cwd),
                capture_output=True, text=True, check=False,
            )

            table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
            table.add_column("Сервис")
            table.add_column("Статус")
            table.add_column("Health")
            table.add_column("Порты")

            _STATUS_COLOR = {"running": "green", "exited": "red", "paused": "yellow"}
            _HEALTH_COLOR = {"healthy": "green", "unhealthy": "red",
                             "starting": "yellow", "none": "dim"}

            rows: list[dict] = []
            raw = r.stdout.strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    rows = parsed if isinstance(parsed, list) else [parsed]
                except json.JSONDecodeError:
                    for line in raw.splitlines():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            if not rows:
                console.print("[yellow]Нет запущенных контейнеров.[/yellow]")
                console.print(f"[dim]compose:[/dim] {project.compose_file}")
                return

            for row in rows:
                name = row.get("Service") or row.get("Name") or "?"
                state = str(row.get("State") or row.get("Status") or "?").lower()
                health = str(row.get("Health") or "none").lower()
                ports = row.get("Publishers") or row.get("Ports") or ""
                if isinstance(ports, list):
                    ports = ", ".join(
                        f"{p.get('PublishedPort', '')}→{p.get('TargetPort', '')}"
                        for p in ports if p.get("PublishedPort")
                    )

                sc = _STATUS_COLOR.get(state, "white")
                hc_color = _HEALTH_COLOR.get(health, "white")
                health_icon = {"healthy": "✓", "unhealthy": "✗",
                               "starting": "…", "none": "—"}.get(health, health)

                table.add_row(
                    f"[bold]{name}[/bold]",
                    f"[{sc}]{state}[/{sc}]",
                    f"[{hc_color}]{health_icon} {health}[/{hc_color}]",
                    str(ports),
                )

            console.print(table)
            console.print(f"\n[dim]compose:[/dim] {project.compose_file}")

        except HcCliError as e:
            console.print(f"[red]Ошибка:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]Подсказка:[/dim] {e.hint}")
            raise typer.Exit(code=int(e.exit_code or 1))

    @env_app.command("clean")
    def env_clean(
        volumes: bool = typer.Option(False, "--volumes", help="Удалить также orphan volumes"),
        all_images: bool = typer.Option(False, "--all-images", help="Удалить все неиспользуемые образы (не только dangling)"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать что будет удалено без выполнения"),
    ) -> None:
        """Очистить orphan Docker ресурсы (dangling images, неиспользуемые volumes)."""
        console = Console()
        require_docker(console)

        image_cmd = ["docker", "image", "prune", "-f"]
        if all_images:
            image_cmd.append("--all")

        if dry_run:
            console.print("[yellow]Dry run:[/yellow] будет выполнено:")
            console.print(f"  {' '.join(image_cmd)}")
            if volumes:
                console.print("  docker volume prune -f")
            return

        console.print("[cyan]→[/cyan] Очистка Docker ресурсов...")
        p = subprocess.run(image_cmd, capture_output=True, text=True, check=False)  # noqa: S603
        out = (p.stdout or "").strip()
        if p.returncode == 0:
            console.print(f"[green]✓[/green] Images: {out or 'nothing to remove'}")
        else:
            console.print(f"[yellow]Images:[/yellow] {(p.stderr or '').strip()}")

        if volumes:
            p = subprocess.run(  # noqa: S603
                ["docker", "volume", "prune", "-f"], capture_output=True, text=True, check=False
            )
            out = (p.stdout or "").strip()
            if p.returncode == 0:
                console.print(f"[green]✓[/green] Volumes: {out or 'nothing to remove'}")
            else:
                console.print(f"[yellow]Volumes:[/yellow] {(p.stderr or '').strip()}")

    # --- hc env dotenv: управление .env файлом core-runtime-service ---

    _SECRET_RE = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|PASS|PRIVATE|MASTER)", re.IGNORECASE)

    def _dotenv_local_path() -> tuple[bool, "Path | None"]:
        """Найти .env локально через _resolve_source. Возвращает (found, path)."""
        from rich.console import Console as _C
        try:
            src = _resolve_source(_C(stderr=True))
            env_path = src.path / ".env"
            return True, env_path
        except SystemExit:
            return False, None

    def _parse_dotenv(text: str) -> list[tuple[str, str, str]]:
        """Parse .env → list of (raw_line, key, value). Preserves comments/blanks as ('line', '', '')."""
        result = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                result.append((line, "", ""))
            elif "=" in stripped:
                key, _, val = stripped.partition("=")
                result.append((line, key.strip(), val))
            else:
                result.append((line, "", ""))
        return result

    def _serialize_dotenv(entries: list[tuple[str, str, str]]) -> str:
        return "\n".join(e[0] for e in entries) + "\n"

    def _mask(key: str, val: str) -> str:
        return "***" if _SECRET_RE.search(key) and val else val

    dotenv_app = typer.Typer(
        help="Управление .env файлом core-runtime-service",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @dotenv_app.command("show")
    def dotenv_show(
        no_mask: bool = typer.Option(False, "--no-mask", help="Показать секреты без маскировки"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из deploy config)"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на удалённом сервере"),
    ) -> None:
        """Показать содержимое .env (секреты маскируются по умолчанию)."""
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file}"],
                capture_output=True, text=True, check=False,
            )
            if p.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p.stderr or '').strip()}")
                raise typer.Exit(code=p.returncode)
            text = p.stdout
            console.print(f"[dim]{resolved_ssh}:{remote_file}[/dim]")
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                console.print(f"[yellow].env не найден:[/yellow] {path}")
                raise typer.Exit(code=0)
            text = path.read_text(encoding="utf-8", errors="replace")
            console.print(f"[dim]{path}[/dim]")

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                console.print(f"[dim]{line}[/dim]")
            elif "=" in stripped:
                key, _, val = stripped.partition("=")
                display_val = val if no_mask else _mask(key.strip(), val)
                console.print(f"[bold cyan]{key}[/bold cyan]={display_val}")
            else:
                console.print(line)

    @dotenv_app.command("set")
    def dotenv_set(
        assignment: str = typer.Argument(..., help="KEY=VALUE"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host (по умолчанию из deploy config)"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на удалённом сервере"),
    ) -> None:
        """Добавить или обновить переменную в .env. Формат: KEY=VALUE."""
        console = Console()
        if "=" not in assignment:
            console.print("[red]Ошибка:[/red] формат KEY=VALUE")
            raise typer.Exit(code=1)
        key, _, val = assignment.partition("=")
        key = key.strip()
        if not key:
            console.print("[red]Ошибка:[/red] ключ не может быть пустым")
            raise typer.Exit(code=1)

        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            # Download, modify, upload
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            text = p.stdout or ""
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

        entries = _parse_dotenv(text)
        updated = False
        new_line = f"{key}={val}"
        for i, (raw, k, v) in enumerate(entries):
            if k == key:
                entries[i] = (new_line, key, val)
                updated = True
                break
        if not updated:
            entries.append((new_line, key, val))

        new_text = _serialize_dotenv(entries)

        if resolved_ssh:
            remote_file = env_path or f"{cfg.deploy.path}/.env"
            import shlex as _shlex
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] {key} {'обновлён' if updated else 'добавлен'} в {resolved_ssh}:{remote_file}")
        else:
            path.write_text(new_text, encoding="utf-8")  # type: ignore[union-attr]
            console.print(f"[green]✓[/green] {key} {'обновлён' if updated else 'добавлен'} в {path}")

    @dotenv_app.command("unset")
    def dotenv_unset(
        key: str = typer.Argument(..., help="Имя переменной для удаления"),
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на сервере"),
    ) -> None:
        """Удалить переменную из .env."""
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            text = p.stdout or ""
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                console.print(f"[yellow].env не найден:[/yellow] {path}")
                raise typer.Exit(code=0)
            text = path.read_text(encoding="utf-8", errors="replace")

        entries = _parse_dotenv(text)
        before = len(entries)
        entries = [(raw, k, v) for raw, k, v in entries if k != key]
        if len(entries) == before:
            console.print(f"[yellow]{key} не найден в .env[/yellow]")
            raise typer.Exit(code=0)

        new_text = _serialize_dotenv(entries)

        if resolved_ssh:
            import shlex as _shlex
            remote_file = env_path or f"{cfg.deploy.path}/.env"
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] {key} удалён из {resolved_ssh}:{remote_file}")
        else:
            path.write_text(new_text, encoding="utf-8")  # type: ignore[union-attr]
            console.print(f"[green]✓[/green] {key} удалён из {path}")

    @dotenv_app.command("edit")
    def dotenv_edit(
        ssh: str | None = typer.Option(None, "--ssh", help="user@host"),
        env_path: str | None = typer.Option(None, "--env-path", help="Путь к .env на сервере"),
    ) -> None:
        """Открыть .env в $EDITOR (для SSH — скачивает, редактирует, загружает)."""
        import shlex as _shlex
        import shutil as _shutil
        import tempfile as _tempfile
        console = Console()
        cfg = Config.load()
        resolved_ssh = ssh or cfg.deploy.ssh or None

        editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
        if not editor:
            for cand in ("nvim", "vim", "nano", "micro"):
                if _shutil.which(cand):
                    editor = cand
                    break
        if not editor:
            console.print("[red]Ошибка:[/red] не задан редактор. Укажи переменную EDITOR или VISUAL.")
            raise typer.Exit(code=2)

        if resolved_ssh:
            remote_file = env_path or (f"{cfg.deploy.path}/.env" if cfg.deploy.path else "")
            if not remote_file:
                console.print("[red]Ошибка:[/red] укажи --env-path или задай deploy.path в config.")
                raise typer.Exit(code=1)
            with _tempfile.NamedTemporaryFile(suffix=".env", delete=False) as tmp:
                tmp_path = tmp.name
            p = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat {remote_file} 2>/dev/null || true"],
                capture_output=True, text=True, check=False,
            )
            open(tmp_path, "w").write(p.stdout or "")  # noqa: WPS515
            cmd = [*_shlex.split(editor), tmp_path]
            subprocess.run(cmd, check=False)  # noqa: S603
            new_text = open(tmp_path).read()  # noqa: WPS515
            os.unlink(tmp_path)
            p2 = subprocess.run(  # noqa: S603
                ["ssh", resolved_ssh, f"cat > {_shlex.quote(remote_file)}"],
                input=new_text, capture_output=True, text=True, check=False,
            )
            if p2.returncode != 0:
                console.print(f"[red]SSH ошибка:[/red] {(p2.stderr or '').strip()}")
                raise typer.Exit(code=p2.returncode)
            console.print(f"[green]✓[/green] .env сохранён на {resolved_ssh}:{remote_file}")
        else:
            found, path = _dotenv_local_path()
            if not found or path is None:
                raise typer.Exit(code=2)
            if not path.exists():
                path.touch()
            cmd = [*_shlex.split(editor), str(path)]
            p = subprocess.run(cmd, check=False)  # noqa: S603
            if p.returncode != 0:
                console.print(f"[yellow]Редактор завершился с кодом {p.returncode}[/yellow]")
            else:
                console.print("[green]✓[/green] .env сохранён")

    env_app.add_typer(dotenv_app, name="dotenv")
    app.add_typer(env_app, name="env")
