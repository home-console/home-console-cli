"""hc service — инспекция зарегистрированных сервисов ядра."""
from __future__ import annotations

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client
from hc.json_output import print_json


def register(app: typer.Typer) -> None:
    service_app = typer.Typer(
        help="Инспекция service registry ядра",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @service_app.command("list")
    def list_services(
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод JSON"),
        plugin: str | None = typer.Option(None, "--plugin", "-p", help="Фильтр по имени плагина"),
        filter: str | None = typer.Option(None, "--filter", "-f", help="Фильтр по подстроке в имени сервиса"),
    ) -> None:
        """Показать все зарегистрированные сервисы ядра."""
        console = Console()
        client = require_client(console)
        services = anyio.run(client.list_services_inspector)
        if services is None:
            console.print("[red]Ошибка: не удалось получить список сервисов.[/red]")
            raise typer.Exit(code=1)

        if plugin:
            services = [s for s in services if s.get("plugin_name", "") == plugin]
        if filter:
            services = [s for s in services if filter.lower() in s.get("service_name", "").lower()]

        if json_out:
            print_json({"ok": True, "services": services, "total": len(services)})
            return

        table = Table(title=f"Services ({len(services)})")
        table.add_column("Service", style="bold")
        table.add_column("Plugin", style="dim")
        for s in sorted(services, key=lambda x: x.get("service_name", "")):
            table.add_row(
                s.get("service_name", ""),
                s.get("plugin_name", "—"),
            )
        console.print(table)

    @service_app.command("call")
    def call_service(
        name: str = typer.Argument(..., help="Имя сервиса (напр. yandex.sync_devices)"),
        kwargs_json: str | None = typer.Option(None, "--json", "-j", help='JSON kwargs: \'{"key": "val"}\''),
        json_out: bool = typer.Option(False, "--raw", help="Вывод результата как JSON"),
    ) -> None:
        """Вызвать зарегистрированный сервис ядра.

        Примеры:
          hc service call yandex.sync_devices
          hc service call devices.get --json '{"device_id": "abc"}'
        """
        import json
        console = Console()
        client = require_client(console)

        kwargs: dict = {}
        if kwargs_json:
            try:
                kwargs = json.loads(kwargs_json)
            except json.JSONDecodeError as e:
                console.print(f"[red]Ошибка: невалидный JSON — {e}[/red]")
                raise typer.Exit(code=1)

        result = anyio.run(client.call_service, name, kwargs or None)
        if result is None:
            raise typer.Exit(code=1)

        ok = result.get("ok", True)
        if not ok:
            console.print(f"[red]Ошибка: {result.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        payload = result.get("result", result)
        if json_out:
            print_json(payload)
        else:
            from rich.pretty import Pretty
            console.print(Pretty(payload, expand_all=True))

    app.add_typer(service_app, name="service")
