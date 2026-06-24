from __future__ import annotations

import json
from typing import Any, Optional

import anyio
import typer
from rich.console import Console
from rich.pretty import Pretty

from hc.commands._client_helpers import require_client

action_app = typer.Typer(
    help="Platform actions registry (plugin.json actions)",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def register(app: typer.Typer) -> None:
    app.add_typer(action_app, name="action")


@action_app.command("list")
def list_actions(
    plugin: Optional[str] = typer.Option(None, "--plugin", help="Filter by plugin name"),
) -> None:
    console = Console()
    client = require_client(console)
    data = anyio.run(client.list_actions, plugin)
    if not data:
        raise typer.Exit(code=1)
    payload = data.get("result") if isinstance(data.get("result"), dict) else data
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        console.print("[red]Ошибка:[/red] не удалось получить список actions.")
        raise typer.Exit(code=1)
    console.print(Pretty(items))


@action_app.command("get")
def get_action(action_id: str) -> None:
    console = Console()
    client = require_client(console)
    data = anyio.run(client.get_action, action_id)
    if not data:
        raise typer.Exit(code=1)
    payload = data.get("result") if isinstance(data.get("result"), dict) else data
    console.print(Pretty(payload))


@action_app.command("invoke")
def invoke_action(
    action_id: str,
    json_params: str = typer.Option("{}", "--json-params", help="JSON object of invoke params"),
) -> None:
    console = Console()
    client = require_client(console)
    try:
        params: dict[str, Any] = json.loads(json_params) if json_params.strip() else {}
    except json.JSONDecodeError as exc:
        console.print(f"[red]Ошибка:[/red] невалидный JSON в --json-params: {exc}")
        raise typer.Exit(code=2) from exc
    if not isinstance(params, dict):
        console.print("[red]Ошибка:[/red] --json-params должен быть JSON-объектом")
        raise typer.Exit(code=2)
    data = anyio.run(client.invoke_action, action_id, params)
    if not data:
        raise typer.Exit(code=1)
    payload = data.get("result") if isinstance(data.get("result"), dict) else data
    console.print(Pretty(payload))
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise typer.Exit(code=1)
