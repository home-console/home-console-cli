from __future__ import annotations

import time
import typer
from rich.console import Console

from hc.constants import SETUP_LOG_PATH
from hc.setup_runner import SetupProcess
from hc.commands.setup_wizard import run_setup


def register(app: typer.Typer) -> None:
    setup_app = typer.Typer(
        help="Мастер первого запуска",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    @setup_app.callback(invoke_without_command=True)
    def setup(
        ctx: typer.Context,
        background: bool = typer.Option(False, "--background", help="Запустить в фоне"),
    ) -> None:
        """Интерактивный wizard: подключение, (опционально) подъём Core, базовые плагины."""
        # Важно: callback вызывается и для подкоманд.
        # Wizard запускаем только если подкоманда не указана.
        if ctx.invoked_subcommand is None:
            run_setup(background=background)

    @setup_app.command("status")
    def status() -> None:
        console = Console()
        sp = SetupProcess.load()
        if not sp:
            console.print("Фоновый мастер не запущен.")
            raise typer.Exit(code=0)
        if sp.is_running():
            console.print(f"[green]✓[/green] Мастер работает (pid={sp.pid}).")
            console.print(f"Логи: [bold]{sp.log_path}[/bold]")
        else:
            console.print(f"[yellow]Мастер завершён[/yellow] (pid={sp.pid}).")
            console.print(f"Логи: [bold]{sp.log_path}[/bold]")

    @setup_app.command("logs")
    def logs(
        follow: bool = typer.Option(False, "--follow", help="Следить за логом (tail -f)"),
        lines: int = typer.Option(200, "--lines", help="Сколько последних строк показать"),
    ) -> None:
        console = Console()
        if not SETUP_LOG_PATH.exists():
            console.print("Лог мастера ещё не создан.")
            raise typer.Exit(code=1)
        text = SETUP_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-lines:])
        if tail.strip():
            console.print(tail)
        if follow:
            # Простой tail -f: читаем дописываемое в файл.
            pos = SETUP_LOG_PATH.stat().st_size
            while True:
                try:
                    size = SETUP_LOG_PATH.stat().st_size
                    if size > pos:
                        with SETUP_LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            chunk = f.read()
                        if chunk:
                            console.print(chunk, end="")
                        pos = size
                except KeyboardInterrupt:
                    break
                time.sleep(0.5)

    app.add_typer(setup_app, name="setup")

