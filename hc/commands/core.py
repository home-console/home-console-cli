from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from hc.constants import DEFAULT_CORE_REF, DEFAULT_CORE_REPO
from hc.core_source import (
    CoreSource,
    get_core_source_from_repo,
    get_core_source_local,
    init_core_source,
    update_core_source,
)
from hc.core_ops import (
    compose_project_from_source,
    core_down,
    core_logs,
    core_status,
    core_up,
    require_docker,
)
from hc.env_bootstrap import core_env_path, ensure_core_env
from hc.hints import CORE_DOTENV_HELP, ENV_VS_CORE_DOTENV
from hc.native_core import native_down, native_logs, native_ps, native_up

def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "core-runtime-service").exists():
            return p
    return None


def register(app: typer.Typer) -> None:
    core_app = typer.Typer(
        help="Управление CoreRuntime (docker/native)",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    def _resolve_source(console: Console) -> CoreSource:
        repo_root = _find_repo_root()
        if repo_root:
            src = get_core_source_from_repo(repo_root)
            if src:
                return src
        src = get_core_source_local()
        if src:
            return src
        console.print("[red]Ошибка: исходники Core не найдены.[/red]")
        console.print("Сделай: `hc core init --repo <git-url>`")
        raise typer.Exit(code=1)

    @core_app.command("init")
    def init(
        repo: str = typer.Option(
            DEFAULT_CORE_REPO,
            "--repo",
            help="Git URL репозитория с core-runtime-service",
            show_default=True,
        ),
        ref: str | None = typer.Option(
            DEFAULT_CORE_REF,
            "--ref",
            help="Ветка/тег",
            show_default=True,
        ),
    ) -> None:
        console = Console()
        src = init_core_source(console, repo_url=repo, ref=ref)
        console.print(f"[green]✓[/green] Core исходники готовы: {src.path}")

    @core_app.command("update")
    def core_update() -> None:
        """Обновить локальную копию исходников Core (git pull --ff-only)."""
        console = Console()
        src = update_core_source(console)
        console.print(f"[green]✓[/green] Обновлено: {src.path}")

    env_app = typer.Typer(
        help=CORE_DOTENV_HELP,
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    def _mask_env(text: str) -> str:
        masked: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in line:
                masked.append(line)
                continue
            key, val = line.split("=", 1)
            k = key.strip().upper()
            if any(x in k for x in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and val.strip():
                masked.append(f"{key}=***")
            else:
                masked.append(line)
        return "\n".join(masked) + ("\n" if text.endswith("\n") else "")

    @env_app.callback(invoke_without_command=True)
    def env(
        ctx: typer.Context,
        create: bool = typer.Option(True, "--create/--no-create", help="Создать .env если нет"),
    ) -> None:
        """Показать путь к `.env` Core (и при необходимости создать)."""
        if ctx.invoked_subcommand is not None:
            return
        console = Console()
        console.print(ENV_VS_CORE_DOTENV)
        src = _resolve_source(console)
        if create:
            ensure_core_env(console, src.path)
        console.print(str(core_env_path(src.path)))

    @env_app.command("path")
    def env_path_cmd() -> None:
        console = Console()
        src = _resolve_source(console)
        console.print(str(core_env_path(src.path)))

    @env_app.command("create")
    def env_create() -> None:
        console = Console()
        src = _resolve_source(console)
        ensure_core_env(console, src.path)
        console.print(str(core_env_path(src.path)))

    @env_app.command("show")
    def env_show(mask: bool = typer.Option(False, "--mask", help="Скрыть значения секретов")) -> None:
        console = Console()
        src = _resolve_source(console)
        ensure_core_env(console, src.path)
        p = core_env_path(src.path)
        if not p.exists():
            console.print("[red]Ошибка: .env не найден.[/red]")
            raise typer.Exit(code=1)
        text = p.read_text(encoding="utf-8", errors="replace")
        console.print(_mask_env(text) if mask else text, end="")

    core_app.add_typer(env_app, name="env")

    @core_app.command("up")
    def up(
        mode: str = typer.Option("docker", "--mode", help="docker|native"),
        no_ui: bool = typer.Option(True, "--no-ui/--with-ui", help="Запустить без UI"),
        use_hc_python: bool = typer.Option(
            False,
            "--use-hc-python",
            help="Native: запускать Core тем же интерпретатором, что и `hc`",
        ),
    ) -> None:
        console = Console()
        m = mode.lower().strip()
        if m == "native":
            src = _resolve_source(console)
            native_up(console, src, use_hc_python=use_hc_python, no_ui=no_ui)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)

        require_docker(console)
        src = _resolve_source(console)
        project = compose_project_from_source(console, src)
        core_up(console, project, no_ui=no_ui)
        console.print("[green]✓[/green] CoreRuntime поднят. Обычно это `http://localhost:18000`.")

    @core_app.command("down")
    def down(
        mode: str = typer.Option("docker", "--mode", help="docker|native"),
        volumes: bool = typer.Option(False, "-v", "--volumes", help="Удалить volumes (аналог down -v)"),
    ) -> None:
        console = Console()
        m = mode.lower().strip()
        if m == "native":
            native_down(console, volumes=volumes)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)

        require_docker(console)
        src = _resolve_source(console)
        project = compose_project_from_source(console, src)
        core_down(console, project, volumes=volumes)
        console.print("[green]✓[/green] CoreRuntime остановлен.")

    def _docker_ps(console: Console) -> None:
        require_docker(console)
        src = _resolve_source(console)
        project = compose_project_from_source(console, src)
        core_status(console, project)

    def _docker_logs(console: Console, *, follow: bool, tail: int) -> None:
        require_docker(console)
        src = _resolve_source(console)
        project = compose_project_from_source(console, src)
        core_logs(console, project, follow=follow, tail=tail)

    @core_app.command("ps")
    def ps(mode: str = typer.Option("docker", "--mode", help="docker|native")) -> None:
        console = Console()
        m = mode.lower().strip()
        if m == "native":
            src = _resolve_source(console)
            native_ps(console, src)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)
        _docker_ps(console)

    @core_app.command("docker-logs")
    def docker_logs(
        mode: str = typer.Option("docker", "--mode", help="docker|native"),
        follow: bool = typer.Option(False, "-f", "--follow", help="Следить за логами"),
        tail: int = typer.Option(200, "--tail", help="Сколько строк показать"),
    ) -> None:
        console = Console()
        m = mode.lower().strip()
        if m == "native":
            native_logs(console, follow=follow, tail=tail)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)
        _docker_logs(console, follow=follow, tail=tail)

    # Backward-compatible aliases (раньше назывались так же, как API команды на верхнем уровне).
    @core_app.command("status")
    def status(mode: str = typer.Option("docker", "--mode", help="docker|native")) -> None:
        console = Console()
        console.print("[dim]Подсказка:[/dim] `hc core status` переехала в `hc core ps`.")
        m = mode.lower().strip()
        if m == "native":
            src = _resolve_source(console)
            native_ps(console, src)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)
        _docker_ps(console)

    @core_app.command("logs")
    def logs(
        mode: str = typer.Option("docker", "--mode", help="docker|native"),
        follow: bool = typer.Option(False, "-f", "--follow", help="Следить за логами"),
        tail: int = typer.Option(200, "--tail", help="Сколько строк показать"),
    ) -> None:
        console = Console()
        console.print("[dim]Подсказка:[/dim] `hc core logs` переехала в `hc core docker-logs`.")
        m = mode.lower().strip()
        if m == "native":
            native_logs(console, follow=follow, tail=tail)
            return
        if m != "docker":
            console.print("[red]Ошибка: --mode должен быть docker или native.[/red]")
            raise typer.Exit(code=1)
        _docker_logs(console, follow=follow, tail=tail)

    app.add_typer(core_app, name="core")

