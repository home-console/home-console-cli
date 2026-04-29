from __future__ import annotations

import shutil

import typer
from rich.console import Console

from hc.constants import (
    CONFIG_DIR,
    CONFIG_PATH,
    CORE_SRC_DIR,
    DATA_DIR,
    HISTORY_PATH,
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


def register(app: typer.Typer) -> None:
    reset_app = typer.Typer(
        help="Полная очистка (конфиг/кэш/Core)",
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
        # Если папка данных пуста — прибираем.
        try:
            if DATA_DIR.exists() and not any(DATA_DIR.iterdir()):
                _rm(DATA_DIR)
        except OSError:
            pass
        console.print("[green]✓[/green] Кэш Core удалён.")

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

    @reset_app.command("all")
    def reset_all() -> None:
        """Удалить всё, что создавала утилита (конфиг + кэш Core)."""
        console = Console()
        if not typer.confirm("Удалить ВСЁ (конфиг hc + кэш Core)?", default=False):
            raise typer.Exit(code=0)
        for p in [CORE_SRC_DIR, DATA_DIR, CONFIG_PATH, HISTORY_PATH, SETUP_LOG_PATH, SETUP_PID_PATH, CONFIG_DIR]:
            try:
                _rm(p)
            except OSError:
                continue
        console.print("[green]✓[/green] Всё удалено.")

    app.add_typer(reset_app, name="reset")

