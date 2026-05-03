from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from hc.core_ops import require_docker
from hc.commands.recovery import RecoveryContext


def build_app(ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Управление UI",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("up")
    def up() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["up", "-d"])
        console.print("[green]✓[/green] CoreRuntime + UI подняты.")

    @app.command("down")
    def down(service: str = typer.Option("caddy", "--service")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["stop", service])

    @app.command("status")
    def status(service: str = typer.Option("caddy", "--service")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["ps", service])

    @app.command("dev")
    def dev(run: bool = typer.Option(False, "--run", help="Запустить pnpm web (иначе только покажет команду)")) -> None:
        console = Console()
        repo_root = ctx.find_repo_root()
        if not repo_root:
            console.print("[red]Ошибка: не нашёл корень монорепы.[/red]")
            raise typer.Exit(code=1)
        web_root = repo_root / "platform-home-console"
        if not web_root.exists():
            console.print(f"[red]Ошибка: не найден {web_root}[/red]")
            raise typer.Exit(code=1)
        cmd = f"cd {shlex.quote(str(web_root))} && pnpm web"
        console.print("Команда для dev UI (Vite):")
        console.print(f"  [bold]{cmd}[/bold]")
        console.print("Потом открыть: http://localhost:5173 (proxy на core:18000 уже в Vite).")
        if not run:
            return
        subprocess.run(cmd, shell=True, check=False)  # noqa: S602,S603

    return app

