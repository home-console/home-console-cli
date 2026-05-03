from __future__ import annotations

import typer
from rich.console import Console

from hc.config import Config
from hc.commands.recovery import RecoveryContext


def build_app(_ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Режим recovery: dev(build) vs image(prod-like)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("show")
    def show() -> None:
        console = Console()
        cfg = Config.load()
        console.print(f"recovery.mode = [bold]{cfg.recovery.mode}[/bold]")

    @app.command("set")
    def set_mode(mode: str = typer.Argument(..., help="dev | image")) -> None:
        console = Console()
        mode = mode.strip().lower()
        if mode not in {"dev", "image"}:
            console.print("[red]Ошибка:[/red] mode должен быть dev или image")
            raise typer.Exit(code=2)
        cfg = Config.load()
        cfg.recovery.mode = mode
        cfg.save()
        console.print(f"[green]✓[/green] Установил recovery.mode = {mode}")

    return app

