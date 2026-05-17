from __future__ import annotations

import typer
from rich.console import Console

from hc.doctor_lib import print_doctor_report, run_doctor


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def doctor(
        quick: bool = typer.Option(
            False,
            "--quick",
            "-q",
            help="Только Docker, git, конфиг и режимы (без портов и диска)",
        ),
        api: bool = typer.Option(
            False,
            "--api",
            help="Только подключение к Core API (нужен hc connect)",
        ),
        json_out: bool = typer.Option(False, "--json", help="Вывод в JSON"),
    ) -> None:
        """
        Диагностика системы.

        По умолчанию — полная проверка. --quick — быстрая локальная.
        --api — доступность Core и латентность.
        """
        console = Console()
        if quick and api:
            console.print("[red]Ошибка:[/red] укажи только один из флагов: --quick или --api")
            raise typer.Exit(code=2)

        if api:
            scope = "api"
        elif quick:
            scope = "quick"
        else:
            scope = "full"

        report = run_doctor(console, scope=scope)
        print_doctor_report(console, report, json_out=json_out)
