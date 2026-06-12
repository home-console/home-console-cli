from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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

    pipx_bin = shutil.which("pipx")
    if pipx_bin and _pipx_has_package(pipx_bin):
        installed = _upgrade_via_pipx(console, pipx_bin, latest)
        if installed:
            console.print(f"[green]✓[/green] Обновлено до {installed} (pipx)")
            _reexec(console, pipx_bin=pipx_bin)
            return 0
        console.print("[yellow]pipx не установил новую версию, пробую pip…[/yellow]")

    code = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "install", "--upgrade", _PYPI_PACKAGE],
        check=False,
    ).returncode
    if code != 0:
        console.print("[red]Ошибка:[/red] pip install завершился с ошибкой.")
        console.print(f"[dim]Вручную:[/dim] {upgrade_hint()}")
        return code

    new_ver = _disk_package_version() or __version__
    if _is_newer(latest, new_ver):
        console.print(
            f"[red]Ошибка:[/red] после pip всё ещё {new_ver}, ожидалась {latest}."
        )
        console.print(f"[dim]Вручную:[/dim] {upgrade_hint()}")
        return 1

    console.print(f"[green]✓[/green] Обновлено до {new_ver} (pip)")
    _reexec(console)
    return 0


def _disk_package_version() -> str | None:
    """Прочитать актуальную версию пакета с диска (после pip/pipx апгрейда).

    Не вызывает importlib.reload — он переписывает атрибуты модуля и ломает
    тесты, которые полагаются на стабильное hc.__version__.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        return str(_pkg_version(_PYPI_PACKAGE))
    except Exception:
        return None


def _pipx_home(pipx: str) -> Path:
    r = subprocess.run(  # noqa: S603
        [pipx, "environment", "--value", "PIPX_HOME"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return Path.home() / ".local" / "pipx"


def _pipx_package_version(pipx: str) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603
            [pipx, "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
        entry = data.get("venvs", {}).get(_PYPI_PACKAGE, {})
        meta = entry.get("metadata", {}).get("main_package", {})
        ver = meta.get("package_version")
        return str(ver) if ver else None
    except Exception:
        return None


def _pipx_hc_executable(pipx: str) -> str | None:
    """Прямой путь к hc в pipx-venv (надёжнее чем which после upgrade)."""
    candidate = _pipx_home(pipx) / "venvs" / _PYPI_PACKAGE / "bin" / "hc"
    if candidate.is_file():
        return str(candidate)
    return None


def _version_reached(target: str, installed: str | None) -> bool:
    """True если installed >= target (обновление удалось)."""
    if not installed:
        return False
    return not _is_newer(target, installed)


def _upgrade_via_pipx(console: Console, pipx: str, target: str) -> str | None:
    """
    Обновить через pipx и вернуть установленную версию, если она >= target.

    pipx upgrade может завершиться с кодом 0 и текстом «already at latest»,
    хотя на PyPI уже есть новее (устаревший индекс pip). Поэтому всегда
    проверяем фактическую версию и при необходимости делаем --force install.
    """
    # 1) Обычный upgrade с принудительным обновлением индекса pip.
    subprocess.run(  # noqa: S603
        [pipx, "upgrade", _PYPI_PACKAGE, "--pip-args", "--no-cache-dir"],
        check=False,
    )
    installed = _pipx_package_version(pipx)
    if _version_reached(target, installed):
        return installed

    # 2) Принудительная переустановка конкретной версии с PyPI.
    console.print(f"[dim]→ принудительная переустановка {_PYPI_PACKAGE}=={target}…[/dim]")
    subprocess.run(  # noqa: S603
        [
            pipx,
            "install",
            f"{_PYPI_PACKAGE}=={target}",
            "--force",
            "--pip-args",
            "--no-cache-dir",
        ],
        check=False,
    )
    installed = _pipx_package_version(pipx)
    if _version_reached(target, installed):
        return installed

    return None


def _reexec(console: Console, *, pipx_bin: str | None = None) -> None:
    """Заменить текущий процесс новой версией бинарника (без видимого перезапуска)."""
    exe = None
    if pipx_bin:
        exe = _pipx_hc_executable(pipx_bin)
    if not exe:
        exe = shutil.which("hc") or sys.argv[0]
    exe = os.path.realpath(exe)
    try:
        console.print("[dim]↺ Перезапуск с новой версией...[/dim]")
        os.execv(exe, [exe, *sys.argv[1:]])
    except OSError:
        console.print(f"[dim]Перезапусти сессию вручную: {exe}[/dim]")


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
