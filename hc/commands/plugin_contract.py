"""hc plugin contract / hc plugin graph — инспекция контрактов и графа зависимостей."""
from __future__ import annotations

import anyio
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client
from hc.json_output import print_json


def register_contract(plugin_app: typer.Typer) -> None:

    @plugin_app.command("contract")
    def contract(
        plugin_name: str = typer.Argument(..., help="Имя плагина"),
        json_out: bool   = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Показать контракт плагина: что потребляет, на что подписан, что предоставляет.

        Примеры:
          hc plugin contract telegram_bot
          hc plugin contract wireguard --json
        """
        console = Console()
        client  = require_client(console)

        async def _fetch() -> dict:
            return await client._request_json_absolute(
                "GET", f"/api/v1/admin/inspector/plugins/{plugin_name}/contract"
            )

        data = anyio.run(_fetch)

        if data is None:
            console.print("[red]Ошибка: не удалось получить контракт.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            print_json(data)
            return

        if data.get("error"):
            console.print(f"[red]{data['error']}[/red]")
            raise typer.Exit(code=1)

        loaded  = data.get("loaded", False)
        missing = data.get("missing", [])
        healthy = len(missing) == 0
        res     = data.get("resolution", {})
        contract_raw = data.get("contract", {})

        status = "[green]loaded[/green]" if loaded else "[yellow]not loaded[/yellow]"
        health = "[green]✓ healthy[/green]" if healthy else f"[red]✗ {len(missing)} missing[/red]"

        console.print(f"\n[bold]{plugin_name}[/bold]  {status}  {health}\n")

        # Consumes services
        consumed = res.get("consumed_services", {})
        if consumed:
            t = Table(title="Потребляет сервисы", show_header=True, box=None, padding=(0, 1))
            t.add_column("Сервис", style="cyan")
            t.add_column("Провайдер", style="green")
            t.add_column("Причина", style="dim")
            for svc, info in consumed.items():
                t.add_row(svc, info.get("provider", "?"), info.get("reason", ""))
            console.print(t)

        # Subscribes events
        events = res.get("subscribed_events", {})
        if events:
            t = Table(title="Подписан на события", show_header=True, box=None, padding=(0, 1))
            t.add_column("Событие", style="yellow")
            t.add_column("Публикует", style="green")
            t.add_column("Причина", style="dim")
            for evt, providers in events.items():
                for p in providers:
                    t.add_row(evt, p.get("provider", "?"), p.get("reason", ""))
            console.print(t)

        # Capabilities required
        caps = res.get("required_capabilities", {})
        if caps:
            t = Table(title="Требует capability", show_header=True, box=None, padding=(0, 1))
            t.add_column("Capability", style="magenta")
            t.add_column("Провайдер", style="green")
            for cap, providers in caps.items():
                for p in providers:
                    t.add_row(cap, p.get("provider", "?"))
            console.print(t)

        # Provides
        provides_svcs   = contract_raw.get("provides_services", [])
        provides_events = contract_raw.get("provides_events", [])
        provides_caps   = contract_raw.get("capabilities_provided", [])

        if provides_svcs or provides_events or provides_caps:
            t = Table(title="Предоставляет", show_header=True, box=None, padding=(0, 1))
            t.add_column("Тип", style="dim")
            t.add_column("Имя")
            t.add_column("Описание", style="dim")
            for s in provides_svcs:
                t.add_row("service", s.get("service", ""), s.get("description", ""))
            for e in provides_events:
                t.add_row("event", e.get("event", ""), e.get("description", ""))
            for c in provides_caps:
                t.add_row("capability", c, "")
            console.print(t)

        # Missing
        if missing:
            console.print()
            t = Table(title="[red]Не хватает[/red]", show_header=True, box=None, padding=(0, 1))
            t.add_column("Тип", style="red")
            t.add_column("Имя", style="red")
            t.add_column("Причина", style="dim")
            for m in missing:
                t.add_row(m.get("kind", "?"), m.get("name", "?"), m.get("reason", ""))
            console.print(t)

        console.print()


def register_graph(plugin_app: typer.Typer) -> None:

    @plugin_app.command("graph")
    def graph(
        json_out:   bool = typer.Option(False, "--json", help="JSON вывод"),
        dot_format: bool = typer.Option(False, "--dot",  help="DOT-формат для Graphviz"),
    ) -> None:
        """Показать граф зависимостей всех плагинов.

        Примеры:
          hc plugin graph
          hc plugin graph --json
          hc plugin graph --dot > deps.dot && dot -Tpng deps.dot -o deps.png
        """
        console = Console()
        client  = require_client(console)

        async def _fetch() -> dict:
            return await client._request_json_absolute(
                "GET", "/api/v1/admin/inspector/contract/graph"
            )

        data = anyio.run(_fetch)

        if data is None:
            console.print("[red]Ошибка: не удалось получить граф.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            print_json(data)
            return

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        if dot_format:
            # DOT output для Graphviz
            print("digraph hc_plugins {")
            print('  rankdir=LR; node [shape=box, style=filled];')
            for n in nodes:
                name    = n if isinstance(n, str) else n.get("name", "")
                loaded  = n.get("loaded", True) if isinstance(n, dict) else True
                color   = '"#22c55e"' if loaded else '"#94a3b8"'
                print(f'  "{name}" [fillcolor={color}, fontcolor=white];')
            for e in edges:
                style = "solid" if e.get("type") == "consumes_service" else "dashed"
                color = "blue" if e.get("type") == "consumes_service" else "orange"
                print(f'  "{e["from"]}" -> "{e["to"]}" [label="{e.get("label","")}", style={style}, color={color}];')
            print("}")
            return

        if not nodes and not edges:
            console.print("[yellow]Граф пуст — нет контрактов или плагинов не найдено.[/yellow]")
            return

        # Summary table
        console.print(f"\n[bold]Plugin Dependency Graph[/bold]  "
                      f"[dim]{len(nodes)} плагинов, {len(edges)} связей[/dim]\n")

        # Nodes
        t = Table(title="Плагины", show_header=True, box=None, padding=(0, 1))
        t.add_column("Плагин", style="bold")
        t.add_column("Статус")
        t.add_column("Версия", style="dim")
        for n in nodes:
            if isinstance(n, str):
                t.add_row(n, "[dim]?[/dim]", "")
            else:
                loaded  = n.get("loaded", False)
                status  = Text("● loaded", style="green") if loaded else Text("○ not loaded", style="dim")
                t.add_row(n.get("name", "?"), status, n.get("version", ""))
        console.print(t)

        if edges:
            console.print()
            t = Table(title="Связи", show_header=True, box=None, padding=(0, 1))
            t.add_column("От", style="cyan")
            t.add_column("Тип", style="dim")
            t.add_column("→ К", style="green")
            t.add_column("Имя", style="dim")
            type_labels = {
                "consumes_service": "сервис",
                "subscribes_event": "событие",
                "requires_capability": "capability",
            }
            for e in sorted(edges, key=lambda x: (x.get("from", ""), x.get("type", ""))):
                t.add_row(
                    e.get("from", "?"),
                    type_labels.get(e.get("type", ""), e.get("type", "")),
                    e.get("to", "?"),
                    e.get("label", ""),
                )
            console.print(t)

        console.print()
