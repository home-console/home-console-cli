"""hc event — инспекция и live-стрим событий event bus."""
from __future__ import annotations

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.commands._client_helpers import require_client
from hc.json_output import print_json


def register(app: typer.Typer) -> None:
    event_app = typer.Typer(
        help="Инспекция event bus ядра",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @event_app.command("list")
    def list_events(
        json_out: bool = typer.Option(False, "--json", help="Машинный вывод JSON"),
    ) -> None:
        """Показать текущие подписки на события (snapshot)."""
        console = Console()
        client = require_client(console)
        events = anyio.run(client.list_events_inspector)
        if events is None:
            console.print("[red]Ошибка: не удалось получить список событий.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            print_json({"ok": True, "events": events})
            return

        table = Table(title=f"Event subscriptions ({len(events)})")
        table.add_column("Event type", style="bold")
        table.add_column("Subscribers")
        for e in sorted(events, key=lambda x: x.get("event_name", "")):
            subs = e.get("subscribers", [])
            sub_str = ", ".join(
                f"{s.get('plugin', '?')}" for s in subs
            ) if subs else "—"
            table.add_row(e.get("event_name", ""), sub_str)
        console.print(table)

    @event_app.command("tail")
    def tail(
        filter: str = typer.Option("*", "--filter", "-f", help="Glob-фильтр по типу события (напр. device.* или *)"),
        json_out: bool = typer.Option(False, "--json", help="Вывод каждого события как JSON строка"),
    ) -> None:
        """Live-стрим событий event bus (SSE). Ctrl+C для остановки.

        Примеры:
          hc event tail                      — все события
          hc event tail --filter device.*    — только device-события
          hc event tail --filter "*.error"   — события с суффиксом .error
        """
        console = Console()
        client = require_client(console)

        if not json_out:
            console.print(f"[dim]Слушаю события (filter={filter!r}). Ctrl+C для остановки…[/dim]\n")

        async def _run() -> None:
            import json as _json
            from rich.text import Text

            async for event in client.stream_events(filter=filter):
                event_type = event.get("type", "?")
                data = event.get("data", {})

                if json_out:
                    console.print(_json.dumps(event, ensure_ascii=False))
                    continue

                # Цвет по префиксу типа события
                if "error" in event_type:
                    color = "red"
                elif "warn" in event_type:
                    color = "yellow"
                elif event_type.startswith("internal."):
                    color = "dim"
                else:
                    color = "cyan"

                t = Text()
                t.append(f"  {event_type}", style=f"bold {color}")
                if data:
                    # Показываем ключи payload без значений (могут быть большими)
                    keys = list(data.keys())[:6]
                    keys_str = ", ".join(keys)
                    if len(data) > 6:
                        keys_str += f" … (+{len(data) - 6})"
                    t.append(f"  {{{keys_str}}}", style="dim")
                console.print(t)

        try:
            anyio.run(_run)
        except KeyboardInterrupt:
            if not json_out:
                console.print("\n[dim]Остановлено.[/dim]")

    @event_app.command("emit")
    def emit(
        event_type: str = typer.Argument(..., help="Тип события (напр. device.updated)"),
        data_json: str | None = typer.Option(None, "--json", "-j", help='JSON payload: \'{"key": "val"}\''),
    ) -> None:
        """Послать событие в event bus ядра.

        Примеры:
          hc event emit test.ping
          hc event emit device.updated --json '{"device_id": "abc"}'
        """
        import json
        console = Console()
        client = require_client(console)

        data: dict = {}
        if data_json:
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError as e:
                console.print(f"[red]Ошибка: невалидный JSON — {e}[/red]")
                raise typer.Exit(code=1)

        result = anyio.run(client.emit_event, event_type, data or None)
        if result is None:
            raise typer.Exit(code=1)

        if result.get("ok"):
            console.print(f"[green]✓[/green] Событие [bold]{event_type}[/bold] опубликовано.")
        else:
            console.print(f"[red]Ошибка: {result.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

    app.add_typer(event_app, name="event")
