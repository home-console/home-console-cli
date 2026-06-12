from __future__ import annotations

import typer
from rich.console import Console

from hc.doctor_lib import print_doctor_report, run_doctor


def run_doctor_cmd(
    *,
    quick: bool = False,
    api: bool = False,
    dev: bool = False,
    prod: bool = False,
    show_all: bool = False,
    json_out: bool = False,
) -> None:
    """Общая реализация doctor для `hc doctor` и алиасов env/deploy doctor."""
    console = Console()

    if quick and api:
        console.print("[red]Ошибка:[/red] укажи только один из флагов: --quick или --api")
        raise typer.Exit(code=2)

    flags = [dev, prod, show_all]
    if sum(bool(f) for f in flags) > 1:
        console.print(
            "[red]Ошибка:[/red] --dev / --prod / --all взаимоисключающие"
        )
        raise typer.Exit(code=2)

    if api:
        scope = "api"
    elif quick:
        scope = "quick"
    else:
        scope = "full"

    if dev:
        stack = "dev"
    elif prod:
        stack = "prod"
    elif show_all:
        stack = "all"
    else:
        stack = "auto"

    report = run_doctor(console, scope=scope, stack=stack)
    print_doctor_report(console, report, json_out=json_out)


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
        dev: bool = typer.Option(
            False, "--dev", help="Показать только DEV-порты (18080/18000/15173/15432/16379)"
        ),
        prod: bool = typer.Option(
            False, "--prod", help="Показать только PROD-порты (8080/8000/5432/6379)"
        ),
        show_all: bool = typer.Option(
            False, "--all", help="Показать обе секции (DEV+PROD) полностью, включая free"
        ),
        json_out: bool = typer.Option(False, "--json", help="Вывод в JSON"),
    ) -> None:
        """
        Диагностика системы.

        По умолчанию: полная проверка с авто-выбором секции портов
        (показывается то, что соответствует запущенным контейнерам;
        свободные порты неактивного стека скрываются).

          --quick      без портов и диска
          --api        только Core API
          --dev/--prod явно выбрать стек
          --all        показать всё, включая free
        """
        run_doctor_cmd(
            quick=quick,
            api=api,
            dev=dev,
            prod=prod,
            show_all=show_all,
            json_out=json_out,
        )
