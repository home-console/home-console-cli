from __future__ import annotations

import typer
from rich.console import Console

from hc.core_ops import require_docker
from hc.commands.recovery import RecoveryContext


def build_app(ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Redis (docker compose service)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("status")
    def status() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["ps", "redis"])

    @app.command("up")
    def up() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["up", "-d", "redis"])
        console.print("[green]✓[/green] Redis поднят.")

    @app.command("down")
    def down() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["stop", "redis"])
        console.print("[green]✓[/green] Redis остановлен.")

    @app.command("flush")
    def flush(
        yes: bool = typer.Option(False, "--yes", help="Не спрашивать подтверждение"),
    ) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        if not yes:
            if not typer.confirm("Это сделает Redis FLUSHALL (удалит все ключи). Продолжить?", default=False):
                raise typer.Exit(code=0)
        ctx.run_compose(console, src, ["exec", "redis", "redis-cli", "FLUSHALL"])

    @app.command("cli")
    def cli() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["exec", "redis", "redis-cli"])

    return app

