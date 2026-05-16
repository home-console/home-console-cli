from __future__ import annotations

import json
import shutil
import subprocess
import sys

import typer
from rich.console import Console

from hc import __version__
from hc.update_check import (
    _PYPI_PACKAGE,
    _fetch_latest,
    _is_newer,
    get_update_notification,
    print_update_banner,
    upgrade_hint,
)


def run_cli_upgrade(console: Console, *, check_only: bool = False) -> int:
    """
    Обновить CLI или только проверить наличие новой версии.
    Возвращает код выхода (0 = ok / актуально, 1 = есть апдейт при check_only, и т.д.).
    """
    latest = get_update_notification(__version__) or _fetch_latest()
    if not latest:
        console.print("[yellow]Не удалось проверить PyPI. Повтори позже.[/yellow]")
        return 1

    if not _is_newer(latest, __version__):
        console.print(f"[green]✓[/green] Уже последняя версия ({__version__})")
        return 0

    if check_only:
        console.print(
            f"[yellow]Доступна версия {latest}[/yellow] (текущая {__version__})"
        )
        console.print(f"[dim]{upgrade_hint()}[/dim]")
        return 1

    console.print(f"Обновление [bold]{__version__}[/bold] → [bold]{latest}[/bold]…")

    if _upgrade_via_pipx(console):
        console.print(f"[green]✓[/green] Обновлено до {latest} (pipx)")
        return 0

    code = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "install", "--upgrade", _PYPI_PACKAGE],
        check=False,
    ).returncode
    if code != 0:
        console.print("[red]Ошибка:[/red] pip install завершился с ошибкой.")
        console.print(f"[dim]Вручную:[/dim] {upgrade_hint()}")
        return code
    console.print("[green]✓[/green] Обновлено через pip. Перезапусти терминал.")
    console.print("[dim]Проверка:[/dim] hc version")
    return 0


def register(app: typer.Typer) -> None:
    @app.command("version")
    def version_cmd() -> None:
        """Версия CLI и проверка обновлений на PyPI."""
        console = Console()
        console.print(f"homeconsole-cli [bold]{__version__}[/bold]")
        print_update_banner(console, __version__)

    @app.command("upgrade")
    def upgrade_cmd(
        check: bool = typer.Option(
            False,
            "--check",
            help="Только проверить наличие обновления (код выхода 1, если есть новее)",
        ),
    ) -> None:
        """Обновить homeconsole-cli через pipx или pip."""
        console = Console()
        raise typer.Exit(code=run_cli_upgrade(console, check_only=check))


def _upgrade_via_pipx(console: Console) -> bool:
    pipx = shutil.which("pipx")
    if not pipx:
        return False
    if not _pipx_has_package(pipx):
        return False
    code = subprocess.run(  # noqa: S603
        [pipx, "upgrade", _PYPI_PACKAGE],
        check=False,
    ).returncode
    if code != 0:
        console.print("[yellow]pipx upgrade не удался, пробую pip…[/yellow]")
        return False
    return True


def _pipx_has_package(pipx: str) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            [pipx, "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return False
        data = json.loads(proc.stdout or "{}")
        venvs = data.get("venvs")
        if isinstance(venvs, dict):
            return _PYPI_PACKAGE in venvs
    except Exception:
        pass
    return False
