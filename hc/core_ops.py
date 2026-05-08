from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from hc.config import Config
from hc.core_source import VALID_MODES, CoreSource
from hc.env_bootstrap import ensure_core_env
from hc.errors import DockerNotFoundError, HcCliError


@dataclass(slots=True)
class ComposeProject:
    compose_file: Path

    @property
    def cwd(self) -> Path:
        return self.compose_file.parent


def require_docker(console: Console) -> None:
    if shutil.which("docker") is None:
        raise DockerNotFoundError(
            message="docker не найден.",
            exit_code=1,
            hint="Установи Docker/OrbStack и проверь, что `docker ps` работает.",
        )


def compose_project_from_source(console: Console, src: CoreSource, mode: str | None = None) -> ComposeProject:
    ensure_core_env(console, src.path)
    if mode is None:
        mode = Config.load().recovery.mode
    try:
        compose = src.compose_file(mode=mode)
    except ValueError as exc:
        valid = " | ".join(sorted(VALID_MODES))
        raise HcCliError(
            message=str(exc),
            exit_code=2,
            hint=f"Допустимые режимы: {valid}",
        ) from exc
    if not compose.exists():
        raise HcCliError(
            message=f"Не найден compose-файл: {compose}",
            exit_code=1,
            hint=f"Режим {mode!r} → {src.compose_rel(mode)}. Проверь наличие файла в core-runtime-service.",
        )
    return ComposeProject(compose_file=compose)


def run_compose(console: Console, args: list[str], cwd: Path) -> None:
    p = subprocess.run(  # noqa: S603
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if p.returncode == 0:
        if p.stdout:
            console.print(p.stdout.rstrip())
        return

    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if "permission denied while trying to connect to the docker daemon socket" in out.lower():
        console.print("[red]Ошибка: нет доступа к Docker daemon socket.[/red]")
        console.print("Проверь, что Docker/OrbStack запущен и у тебя есть права на сокет.")
        console.print("Для проверки: `docker ps`")
        raise typer.Exit(code=1)

    if out.strip():
        console.print(out.rstrip())
    console.print("[red]Ошибка: команда docker compose завершилась с ошибкой[/red]")
    raise typer.Exit(code=1)


def core_up(console: Console, project: ComposeProject, no_ui: bool) -> None:
    services = ["core-runtime"] if no_ui else []
    cmd = ["docker", "compose", "-f", str(project.compose_file), "up", "-d", *services]
    run_compose(console, cmd, cwd=project.cwd)


def core_down(console: Console, project: ComposeProject, volumes: bool) -> None:
    cmd = ["docker", "compose", "-f", str(project.compose_file), "down"]
    if volumes:
        cmd.append("-v")
    run_compose(console, cmd, cwd=project.cwd)


def core_status(console: Console, project: ComposeProject) -> None:
    # Печатаем обычный `ps`, но ещё умеем понять “не запущено” и вернуть exit code 1.
    ps = subprocess.run(  # noqa: S603
        ["docker", "compose", "-f", str(project.compose_file), "ps", "core-runtime"],
        cwd=str(project.cwd),
        text=True,
        capture_output=True,
    )
    if ps.returncode != 0:
        run_compose(console, ["docker", "compose", "-f", str(project.compose_file), "ps", "core-runtime"], cwd=project.cwd)
        return
    if ps.stdout:
        console.print(ps.stdout.rstrip())

    running = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(project.compose_file),
            "ps",
            "--status",
            "running",
            "core-runtime",
        ],
        cwd=str(project.cwd),
        text=True,
        capture_output=True,
    )
    out = (running.stdout or "").strip()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # Если строк меньше 2 (обычно только заголовок) — контейнера нет/не running.
    if len(lines) < 2:
        console.print("[yellow]CoreRuntime не запущен.[/yellow] Запусти: `hc core up`")
        raise typer.Exit(code=1)


def core_logs(console: Console, project: ComposeProject, follow: bool, tail: int) -> None:
    cmd = ["docker", "compose", "-f", str(project.compose_file), "logs", "--tail", str(tail)]
    if follow:
        cmd.append("-f")
    cmd.append("core-runtime")
    run_compose(console, cmd, cwd=project.cwd)

