from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from hc.config import Config
from hc.core_source import COMPOSE_MODES, VALID_MODES, CoreSource
from hc.env_bootstrap import ensure_core_env
from hc.errors import DockerNotFoundError, HcCliError


@dataclass(slots=True)
class ComposeProject:
    compose_file: Path

    @property
    def cwd(self) -> Path:
        return self.compose_file.parent


def require_docker(console: Console) -> None:
    """Проверить Docker до запуска; предложить фикс если нет или нет прав."""
    if shutil.which("docker") is None:
        _handle_no_docker_binary(console)
        return

    r = subprocess.run(  # noqa: S603
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if r.returncode == 0:
        return

    combined = (r.stderr or "") + (r.stdout or "")
    if "permission denied" in combined.lower():
        _handle_docker_no_permission(console)
    else:
        raise DockerNotFoundError(
            message="Docker daemon недоступен.",
            exit_code=1,
            hint="Убедись что Docker/OrbStack запущен: docker info",
        )


def _handle_no_docker_binary(console: Console) -> None:
    import sys

    console.print("[red]✗ Docker не установлен[/red] — команда `docker` не найдена в PATH.")

    if not sys.stdin.isatty():
        raise DockerNotFoundError(
            message="Docker не установлен.",
            exit_code=1,
            hint="Установи Docker: https://docs.docker.com/engine/install/",
        )

    if shutil.which("apt-get"):
        install_cmds = [
            ["sudo", "apt-get", "update", "-qq"],
            ["sudo", "apt-get", "install", "-y", "docker.io"],
            ["sudo", "systemctl", "enable", "--now", "docker"],
        ]
        pkg = "apt"
    elif shutil.which("dnf"):
        install_cmds = [
            ["sudo", "dnf", "install", "-y", "docker"],
            ["sudo", "systemctl", "enable", "--now", "docker"],
        ]
        pkg = "dnf"
    elif shutil.which("pacman"):
        install_cmds = [
            ["sudo", "pacman", "-S", "--noconfirm", "docker"],
            ["sudo", "systemctl", "enable", "--now", "docker"],
        ]
        pkg = "pacman"
    elif shutil.which("brew"):
        install_cmds = [["brew", "install", "--cask", "docker"]]
        pkg = "brew"
    else:
        install_cmds = []
        pkg = None

    try:
        import questionary
    except ImportError:
        raise DockerNotFoundError(
            message="Docker не установлен.",
            exit_code=1,
            hint="Установи Docker: https://docs.docker.com/engine/install/",
        )

    if install_cmds and pkg:
        cmds_preview = " && ".join(" ".join(c) for c in install_cmds)
        console.print(f"[dim]Установка через {pkg}:[/dim] {cmds_preview}")
        answer = questionary.confirm("Установить Docker сейчас?", default=True).ask()
        if answer is None:
            raise typer.Abort()
        if answer:
            for cmd in install_cmds:
                ret = subprocess.run(cmd, check=False).returncode  # noqa: S603
                if ret != 0:
                    console.print(f"[red]Ошибка:[/red] {' '.join(cmd)}")
                    raise typer.Exit(code=1)
            console.print("[green]✓[/green] Docker установлен.")
            console.print("[yellow]![/yellow] Открой новую сессию или повтори команду.")
            raise typer.Exit(code=0)
    else:
        console.print("[dim]Установи Docker вручную:[/dim] https://docs.docker.com/engine/install/")

    raise DockerNotFoundError(
        message="Docker не установлен.",
        exit_code=1,
        hint="Установи Docker: https://docs.docker.com/engine/install/",
    )


def _handle_docker_no_permission(console: Console) -> None:
    import getpass
    import sys

    username = getpass.getuser()
    console.print("[red]✗ Нет прав на Docker daemon socket[/red]")
    console.print(f"  Пользователь [bold]{username}[/bold] не в группе [bold]docker[/bold].")
    console.print("  Сокет: [dim]unix:///var/run/docker.sock[/dim]")

    fix_cmd = ["sudo", "usermod", "-aG", "docker", username]
    fix_str = " ".join(fix_cmd)

    if not sys.stdin.isatty():
        raise HcCliError(
            message="Нет прав на Docker daemon socket.",
            exit_code=1,
            hint=f"Выполни: {fix_str}  и перелогинься.",
        )

    try:
        import questionary
    except ImportError:
        raise HcCliError(
            message="Нет прав на Docker daemon socket.",
            exit_code=1,
            hint=f"Выполни: {fix_str}  и перелогинься.",
        )

    console.print(f"\n[yellow]Фикс:[/yellow] [bold]{fix_str}[/bold]")
    answer = questionary.confirm(
        f"Добавить {username} в группу docker (потребуется sudo)?",
        default=True,
    ).ask()

    if answer is None:
        raise typer.Abort()

    if not answer:
        raise HcCliError(
            message="Нет прав на Docker daemon socket.",
            exit_code=1,
            hint=f"Выполни вручную: {fix_str}  и перелогинься.",
        )

    ret = subprocess.run(fix_cmd, check=False).returncode  # noqa: S603
    if ret != 0:
        raise HcCliError(
            message="Не удалось добавить пользователя в группу docker.",
            exit_code=1,
            hint=f"Выполни вручную: {fix_str}",
        )

    console.print(f"[green]✓[/green] Пользователь [bold]{username}[/bold] добавлен в группу [bold]docker[/bold].")
    console.print()
    console.print("[yellow]![/yellow] Группа применится после переоткрытия сессии.")
    console.print("  Применить сейчас (откроет новый шелл): [bold]newgrp docker[/bold]")
    console.print("  Или: выйди и зайди снова, затем повтори [bold]hc env up[/bold].")
    raise typer.Exit(code=0)


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
        available = [m for m, rel in COMPOSE_MODES.items() if (src.path / rel).exists()]
        hint = f"Режим {mode!r} → {src.compose_rel(mode)}. Файл не найден в {src.path}."
        if available:
            hint += f"\n  Доступные режимы: {' | '.join(available)}"
            hint += f"\n  Попробуй: hc env up --mode {available[0]}"
        else:
            hint += "\n  Сделай `hc core init` для загрузки исходников Core."
        raise HcCliError(
            message=f"Не найден compose-файл: {compose}",
            exit_code=1,
            hint=hint,
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

