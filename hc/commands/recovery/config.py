from __future__ import annotations

import os
import shlex
import subprocess

import typer
from rich.console import Console
from rich.panel import Panel

from hc.constants import CONFIG_DIR, CONFIG_PATH, DATA_DIR, SETUP_LOG_PATH
from hc.commands.recovery import RecoveryContext


def build_app(_ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Конфиг и пути hc",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("paths")
    def paths() -> None:
        console = Console()
        body = f"config dir: {CONFIG_DIR}\nconfig file: {CONFIG_PATH}\ndata dir: {DATA_DIR}\n"
        console.print(Panel(body, title="hc paths"))

    @app.command("show")
    def show() -> None:
        console = Console()
        if not CONFIG_PATH.exists():
            console.print("[yellow]Конфига нет.[/yellow]")
            raise typer.Exit(code=0)
        console.print(CONFIG_PATH.read_text(encoding="utf-8", errors="replace"))

    @app.command("edit")
    def edit() -> None:
        console = Console()
        editor = os.getenv("VISUAL") or os.getenv("EDITOR")
        if not editor:
            console.print("[red]Ошибка: не задан $EDITOR (или $VISUAL).[/red]")
            raise typer.Exit(code=1)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text("", encoding="utf-8")
        cmd = [*shlex.split(editor), str(CONFIG_PATH)]
        subprocess.run(cmd, check=False)  # noqa: S603

    @app.command("open-setup-log")
    def open_setup_log() -> None:
        console = Console()
        if not SETUP_LOG_PATH.exists():
            console.print("[yellow]Лога setup нет.[/yellow]")
            raise typer.Exit(code=0)
        pager = os.getenv("PAGER") or "less -R"
        cmd = [*shlex.split(pager), str(SETUP_LOG_PATH)]
        subprocess.run(cmd, check=False)  # noqa: S603

    return app

