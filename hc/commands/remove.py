from __future__ import annotations

import os

import anyio
import typer
from rich.console import Console
from rich.table import Table

from hc.client import HCClient
from hc.config import Config


def register(app: typer.Typer) -> None:
    @app.command("remove")
    def remove(
        name: str = typer.Argument(..., help="Имя плагина"),
        force: bool = typer.Option(False, "--force", help="Удалить даже если есть зависимые"),
    ) -> None:
        console = Console()
        cfg = Config.load()
        token = os.getenv("HC_TOKEN") or cfg.core.token
        if not cfg.core.host.strip() or not token.strip():
            console.print("[red]Ошибка: Сначала подключись: hc connect <host>[/red]")
            raise typer.Exit(code=1)

        base_url = f"http://{cfg.core.host}:{cfg.core.port}"
        client = HCClient(base_url=base_url, token=token, verify_ssl=cfg.core.verify_ssl)

        async def _deps() -> tuple[list[str], list[dict] | None]:
            plugins = await client.get_plugins()
            deps: list[str] = []
            if isinstance(plugins, list):
                for p in plugins:
                    pd = p.get("dependencies") or p.get("depends_on") or []
                    if isinstance(pd, list) and name in {str(x) for x in pd}:
                        deps.append(str(p.get("name", "")))
            return deps, plugins

        dependents, plugins = anyio.run(_deps)
        if plugins is None:
            raise typer.Exit(code=1)

        if dependents and not force:
            table = Table(title="Нельзя удалить — есть зависимые")
            table.add_column("Плагин", style="bold")
            for d in dependents:
                table.add_row(d)
            console.print(table)
            console.print("[red]Ошибка: используй --force, если точно хочешь удалить[/red]")
            raise typer.Exit(code=1)

        if dependents:
            console.print(f"[yellow]Внимание:[/yellow] зависимые плагины: {', '.join(dependents)}")

        if not typer.confirm(f"Удалить {name}?", default=False):
            raise typer.Exit(code=0)

        data = anyio.run(client.remove_plugin, name)
        if data is None:
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] {name} удалён")

