from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel

from hc.commands.core import register as register_core
from hc.commands.auth import register as register_auth
from hc.commands.env import register as register_env
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
from hc.commands.doctor import register as register_doctor
from hc.commands.marketplace import register as register_marketplace
from hc.commands.secrets import register as register_secrets
from hc.commands.cli_version import register as register_cli_version
from hc.commands.config_cmd import register as register_config
from hc import __version__
from hc.update_check import print_update_banner
from hc.cli_registry import NAV_TREE
from hc.shell import run_shell

_SKIP_UPDATE_NOTIFY = frozenset({"version", "upgrade", "shell", "repl", "nav"})

app = typer.Typer(
    add_completion=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _nav_resolve(path: list[str]) -> tuple[list[str], dict[str, object]]:
    node: dict[str, object] = {"desc": "hc CLI", "children": NAV_TREE}
    consumed: list[str] = []
    for part in path:
        children = node.get("children")
        if not isinstance(children, dict) or part not in children:
            raise typer.BadParameter(f"Неизвестный раздел: {' '.join([*consumed, part])}")
        child = children[part]
        if not isinstance(child, dict):
            raise typer.BadParameter(f"Повреждён nav-раздел: {' '.join([*consumed, part])}")
        node = child
        consumed.append(part)
    return consumed, node


@app.command("nav")
def nav(path: list[str] = typer.Argument(None, help="Путь по разделам, например: deploy dev")) -> None:
    """
    Навигация по командам без запоминания синтаксиса.

    Примеры:
    - hc nav
    - hc nav deploy
    - hc nav deploy dev
    """
    console = Console()
    parts = [p.strip() for p in (path or []) if p and p.strip()]
    try:
        consumed, node = _nav_resolve(parts)
    except typer.BadParameter as e:
        console.print(f"[red]Ошибка:[/red] {e}")
        console.print("[dim]Подсказка:[/dim] начни с `hc nav`")
        raise typer.Exit(code=2)

    children = node.get("children")
    title = "hc nav" if not consumed else f"hc nav {' '.join(consumed)}"
    desc = str(node.get("desc", "")).strip()
    lines = [desc] if desc else []
    if isinstance(children, dict) and children:
        lines.append("")
        lines.append("Доступные разделы:")
        for key in sorted(children):
            child = children[key]
            child_desc = ""
            if isinstance(child, dict):
                child_desc = str(child.get("desc", "")).strip()
            lines.append(f"- {key:12} {child_desc}".rstrip())
        lines.append("")
        lines.append(f"Провалиться глубже: `hc nav {' '.join([*consumed, '<section>']).strip()}`")
    else:
        cmd = "hc " + " ".join(consumed)
        lines.append("")
        lines.append(f"Запуск help для команды: `{cmd} --help`")

    lines.append("")
    lines.append("Быстрый старт:")
    lines.append("- `hc nav env`")
    lines.append("- `hc env up`               ← интерактивные чекбоксы")
    lines.append("- `hc env up --profile hmr`  ← core + caddy + Vite HMR")
    console.print(Panel.fit("\n".join(lines), title=title))


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
    sub = ctx.invoked_subcommand
    if sub and sub not in _SKIP_UPDATE_NOTIFY:
        print_update_banner(Console(), __version__)
    if sub is None:
        if not sys.stdin.isatty():
            Console().print("[red]Ошибка:[/red] укажи команду. Пример: `hc status`")
            raise typer.Exit(code=1)
        run_shell(app)


def _register_all() -> None:
    register_env(app)
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
    register_doctor(app)
    register_secrets(app)
    register_cli_version(app)
    register_config(app)


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
