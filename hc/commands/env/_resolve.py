"""Service/DB/mode resolution, interactive picks for env commands."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from hc.commands.env._catalog import (
    _Svc, _SERVICES, _PROFILE_DEFAULT_MODE, _PROFILES,
    _DbOption, _DB_OPTIONS, _DB_KEY_MAP, EnvUpPlan,
    _MODE_DEFAULT, QUESTIONARY_STYLE_KWARGS,
)
from hc.commands.env._compose import (
    _get_running_services, _detect_active_profiles, _detect_active_db,
)
from hc.commands.env._diagnostics import _get_needed_ports
from hc.core_source import CoreSource, resolve_workspace_root, get_core_source_from_repo, get_core_source_local, init_core_source
from hc.env_state import load_last_env
from hc.errors import CoreSourcesNotFoundError, HcCliError
from hc.core_ops import ComposeProject, compose_project_from_source, require_docker
from hc.config import Config


def _resolve_source(console: Console) -> CoreSource:
    repo_root = resolve_workspace_root()
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

    style = QStyle(list(QUESTIONARY_STYLE_KWARGS.items()))

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

    style = QStyle(list(QUESTIONARY_STYLE_KWARGS.items()))

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
        allowed = preset.get(mode)
        if allowed is None:
            preferred_mode = _PROFILE_DEFAULT_MODE.get(key)
            hint = f" Попробуй `--mode {preferred_mode}`." if preferred_mode else ""
            console.print(
                f"[red]Ошибка:[/red] профиль {profile!r} не поддерживается в режиме {mode!r}.{hint}"
            )
            raise typer.Exit(code=2)
        selected = [s for s in available if s.name in allowed]
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



def _resolve_mode(mode: str | None, profile: str | None) -> str:
    if mode:
        return mode.strip().lower()
    if profile:
        return _PROFILE_DEFAULT_MODE.get(profile.strip().lower(), _MODE_DEFAULT)
    return _MODE_DEFAULT



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


