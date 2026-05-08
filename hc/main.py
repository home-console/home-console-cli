from __future__ import annotations

import sys

import typer
from rich.console import Console

from hc.commands.core import register as register_core
from hc.commands.auth import register as register_auth
from hc.commands.reset import register as register_reset
from hc.commands.recovery import register as register_recovery
from hc.commands.connect import register as register_connect
from hc.commands.install import register as register_install
from hc.commands.logs import register as register_logs
from hc.commands.module import register as register_module
from hc.commands.plugin import register as register_plugin
from hc.commands.remove import register as register_remove
from hc.commands.search import register as register_search
from hc.commands.setup import register as register_setup
from hc.commands.status import register as register_status
from hc.commands.deploy import register as register_deploy
from hc.commands.update import register as register_update
from hc.commands.ping import register as register_ping
from hc.commands.marketplace import register as register_marketplace
from hc.commands.secrets import register as register_secrets
from hc.shell import run_shell

app = typer.Typer(
    add_completion=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command("repl")
def repl(args: list[str] = typer.Argument(None)) -> None:
    """Интерактивный режим (REPL) или однократный запуск команды."""
    console = Console()
    if args:
        try:
            app(prog_name="hc", args=args, standalone_mode=False)
        except typer.Exit:
            raise
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]Ошибка: {e}[/red]")
            raise typer.Exit(code=1)
        return
    run_shell(app)


@app.command("shell")
def shell() -> None:
    """Алиас для REPL (как `hc repl` без аргументов)."""
    run_shell(app)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        if not sys.stdin.isatty():
            Console().print("[red]Ошибка:[/red] укажи команду. Пример: `hc status`")
            raise typer.Exit(code=1)
        run_shell(app)


def _register_all() -> None:
    register_core(app)
    register_auth(app)
    register_reset(app)
    register_recovery(app)
    register_connect(app)
    register_status(app)
    register_install(app)
    register_remove(app)
    register_plugin(app)
    register_module(app)
    register_logs(app)
    register_search(app)
    register_setup(app)
    register_deploy(app)
    register_update(app)
    register_ping(app)
    register_marketplace(app)
    register_secrets(app)


_register_all()


def main() -> None:
    console = Console()
    try:
        app()
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Ошибка: {e}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    main()

