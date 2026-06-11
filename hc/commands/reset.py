from __future__ import annotations

import shutil
import subprocess

import typer
from rich.console import Console

from hc.constants import (
    CONFIG_DIR,
    CONFIG_PATH,
    CORE_SRC_DIR,
    DATA_DIR,
    HISTORY_PATH,
    PLATFORM_SRC_DIR,
    SETUP_LOG_PATH,
    SETUP_PID_PATH,
)


def _rm(path) -> None:  # noqa: ANN001
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _cleanup_empty_data_dir() -> None:
    try:
        if DATA_DIR.exists() and not any(DATA_DIR.iterdir()):
            _rm(DATA_DIR)
    except OSError:
        pass


def _docker_cmd_ok() -> bool:
    return shutil.which("docker") is not None


def _docker_targets() -> tuple[list[str], list[str]]:
    """Возвращает (volumes, images) которые относятся к стеку HC."""
    if not _docker_cmd_ok():
        return [], []
    volumes: list[str] = []
    images: list[str] = []
    # Volumes из dev-стека (compose project name = 'dev' → префикс dev_)
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith(("dev_", "prod_", "hc_")) or "core-runtime" in line:
                volumes.append(line)
    except (subprocess.SubprocessError, OSError):
        pass
    # Images core-runtime и связанные
    try:
        r = subprocess.run(  # noqa: S603
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if "core-runtime" in line or "platform-home-console" in line:
                images.append(line)
    except (subprocess.SubprocessError, OSError):
        pass
    return volumes, images


def _docker_remove(console: Console, volumes: list[str], images: list[str]) -> None:
    if not _docker_cmd_ok():
        console.print("[yellow]docker не установлен — пропускаю docker-чистку.[/yellow]")
        return
    if volumes:
        console.print(f"[cyan]→ удаляю volumes:[/cyan] {', '.join(volumes)}")
        subprocess.run(  # noqa: S603
            ["docker", "volume", "rm", "-f", *volumes],
            check=False, capture_output=True,
        )
    else:
        console.print("[dim]volumes не найдены.[/dim]")
    if images:
        console.print(f"[cyan]→ удаляю images:[/cyan] {', '.join(images)}")
        subprocess.run(  # noqa: S603
            ["docker", "rmi", "-f", *images],
            check=False, capture_output=True,
        )
    else:
        console.print("[dim]images не найдены.[/dim]")


def register(app: typer.Typer) -> None:
    reset_app = typer.Typer(
        help="Полная очистка (конфиг/кэш/Core/platform/docker)",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @reset_app.command("core")
    def reset_core() -> None:
        """Удалить скачанные исходники Core (git clone кэш)."""
        console = Console()
        if not CORE_SRC_DIR.exists():
            console.print("Кэш Core не найден.")
            raise typer.Exit(code=0)
        if not typer.confirm(f"Удалить {CORE_SRC_DIR}?", default=False):
            raise typer.Exit(code=0)
        _rm(CORE_SRC_DIR)
        _cleanup_empty_data_dir()
        console.print("[green]✓[/green] Кэш Core удалён.")

    @reset_app.command("platform")
    def reset_platform() -> None:
        """Удалить скачанный platform-home-console (фронтенд)."""
        console = Console()
        if not PLATFORM_SRC_DIR.exists():
            console.print("Кэш platform-home-console не найден.")
            raise typer.Exit(code=0)
        if not typer.confirm(f"Удалить {PLATFORM_SRC_DIR}?", default=False):
            raise typer.Exit(code=0)
        _rm(PLATFORM_SRC_DIR)
        _cleanup_empty_data_dir()
        console.print("[green]✓[/green] Кэш platform-home-console удалён.")

    @reset_app.command("config")
    def reset_config() -> None:
        """Удалить конфиг/историю/логи `hc`."""
        console = Console()
        targets = [CONFIG_PATH, HISTORY_PATH, SETUP_LOG_PATH, SETUP_PID_PATH]
        if not any(p.exists() for p in targets):
            console.print("Конфиг и логи не найдены.")
            raise typer.Exit(code=0)
        if not typer.confirm(f"Удалить {CONFIG_DIR} (конфиг/логи/история)?", default=False):
            raise typer.Exit(code=0)
        for p in targets:
            _rm(p)
        try:
            if CONFIG_DIR.exists() and not any(CONFIG_DIR.iterdir()):
                _rm(CONFIG_DIR)
        except OSError:
            pass
        console.print("[green]✓[/green] Конфиг и логи удалены.")

    @reset_app.command("docker")
    def reset_docker(
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Показать что будет удалено, не трогать"),
    ) -> None:
        """Удалить docker volumes и images, связанные со стеком HC."""
        console = Console()
        if not _docker_cmd_ok():
            console.print("[red]docker не найден в PATH.[/red]")
            raise typer.Exit(code=1)
        volumes, images = _docker_targets()
        if not volumes and not images:
            console.print("Ничего не найдено для удаления.")
            raise typer.Exit(code=0)
        console.print("[cyan]Найдено:[/cyan]")
        if volumes:
            console.print(f"  volumes: {', '.join(volumes)}")
        if images:
            console.print(f"  images:  {', '.join(images)}")
        if dry_run:
            console.print("[dim](dry-run: ничего не удалено)[/dim]")
            raise typer.Exit(code=0)
        if not yes:
            if not typer.confirm("Удалить?", default=False):
                raise typer.Exit(code=0)
        _docker_remove(console, volumes, images)
        console.print("[green]✓[/green] Docker-чистка завершена.")

    @reset_app.command("all")
    def reset_all(
        yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение"),
        include_docker: bool = typer.Option(
            False, "--include-docker",
            help="Также удалить docker volumes и images (по умолчанию НЕ удаляются)",
        ),
        keep_config: bool = typer.Option(
            False, "--keep-config",
            help="Сохранить конфиг hc (~/.config/hc), удалить только кэши",
        ),
    ) -> None:
        """
        Полная очистка: исходники Core, platform, конфиг hc.

        По умолчанию docker-чистка НЕ включена — добавь --include-docker.
        Конфиг hc (host/port/token) удаляется по умолчанию — оставить через --keep-config.
        """
        console = Console()
        scope = ["кэш Core", "кэш platform-home-console"]
        if not keep_config:
            scope.append("конфиг + история + логи hc")
        if include_docker:
            scope.append("docker volumes/images стека HC")
        if not yes:
            console.print(f"[yellow]Будет удалено:[/yellow] {', '.join(scope)}")
            if not typer.confirm("Подтвердить?", default=False):
                raise typer.Exit(code=0)

        for p in [CORE_SRC_DIR, PLATFORM_SRC_DIR]:
            try:
                _rm(p)
            except OSError:
                continue
        _cleanup_empty_data_dir()

        if not keep_config:
            for p in [CONFIG_PATH, HISTORY_PATH, SETUP_LOG_PATH, SETUP_PID_PATH, CONFIG_DIR]:
                try:
                    _rm(p)
                except OSError:
                    continue

        if include_docker:
            volumes, images = _docker_targets()
            _docker_remove(console, volumes, images)

        console.print(f"[green]✓[/green] Очистка завершена ({', '.join(scope)}).")

    app.add_typer(reset_app, name="reset")
