"""hc wg — управление WireGuard через wireguard плагин Core."""
from __future__ import annotations

from pathlib import Path

import anyio
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hc.commands._client_helpers import require_client


def register(app: typer.Typer) -> None:
    wg_app = typer.Typer(
        help="Управление WireGuard VPN (wireguard plugin)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    # ------------------------------------------------------------------ port-pool

    @wg_app.command("port-pool")
    def port_pool(
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Показать пул портов: диапазон, занятые, свободные, ёмкость."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.port_pool", None)
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        total = data.get("total", 0)
        used  = data.get("used", 0)
        free  = data.get("free", 0)
        pct   = data.get("capacity_pct", 0)

        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        color = "green" if pct < 70 else "yellow" if pct < 90 else "red"

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("", style="bold dim")
        table.add_column("")
        table.add_row("Диапазон",  f"{data.get('range_start')}–{data.get('range_end')}")
        table.add_row("Всего",     str(total))
        table.add_row("Занято",    f"[{color}]{used}[/{color}]")
        table.add_row("Свободно", f"[green]{free}[/green]")
        table.add_row("Занятость", f"[{color}]{bar} {pct}%[/{color}]")
        if data.get("used_ports"):
            table.add_row("Порты",   ", ".join(str(p) for p in data["used_ports"]))

        console.print(Panel(table, title="WireGuard · пул портов", expand=False))
        if free == 0:
            console.print("\n[red]Пул исчерпан![/red] Увеличь WG_PORT_RANGE_END в конфиге wireguard плагина.")

    # ------------------------------------------------------------------ networks

    @wg_app.command("networks")
    def networks(
        json_out: bool = typer.Option(False, "--json", help="JSON вывод"),
    ) -> None:
        """Список WireGuard сетей."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.list_networks", None)
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        items = data.get("networks", []) if isinstance(data, dict) else []

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not items:
            console.print("[dim]Нет WireGuard сетей.[/dim]")
            console.print("[dim]Создать: hc wg create-network <name> --subnet 10.0.0.0/24 --port 51820[/dim]")
            return

        table = Table(title=f"WireGuard сети ({len(items)})")
        table.add_column("Имя",       style="bold cyan")
        table.add_column("Интерфейс")
        table.add_column("Подсеть")
        table.add_column("Порт",      justify="right")
        table.add_column("Пиров",     justify="right")
        table.add_column("Статус")

        for n in items:
            up = n.get("up")
            status = (
                Text("up",      style="green") if up is True  else
                Text("down",    style="red")   if up is False else
                Text("unknown", style="dim")
            )
            table.add_row(
                n.get("name", "?"),
                n.get("interface", "?"),
                n.get("subnet", "?"),
                str(n.get("port", "?")),
                str(n.get("peers", 0)),
                status,
            )
        console.print(table)

    # ------------------------------------------------------------------ create-network

    @wg_app.command("create-network")
    def create_network(
        name: str = typer.Argument(..., help="Имя сети (напр. management, agents)"),
        subnet: str = typer.Option("10.88.0.0/24", "--subnet", help="Подсеть CIDR"),
        port:   int = typer.Option(51820,           "--port",   help="UDP порт"),
        dns:    str = typer.Option("1.1.1.1",       "--dns",    help="DNS для клиентов"),
        allowed_ips: str = typer.Option("0.0.0.0/0", "--allowed-ips", help="AllowedIPs для клиентов"),
        endpoint: str | None = typer.Option(None,   "--endpoint", help="Публичный endpoint (host:port)"),
        interface: str | None = typer.Option(None,  "--iface",    help="Имя интерфейса (wg0, wg1…)"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Создать новую WireGuard сеть.

        Примеры:
          hc wg create-network management --subnet 10.0.1.0/24 --port 51820
          hc wg create-network agents --subnet 10.0.2.0/24 --port 51821 --endpoint vpn.example.com:51821
        """
        console = Console()
        client = require_client(console)
        kwargs: dict = {
            "name": name, "subnet": subnet, "port": port,
            "dns": dns, "allowed_ips": allowed_ips,
        }
        if endpoint:  kwargs["endpoint"]  = endpoint
        if interface: kwargs["interface"] = interface

        data = anyio.run(client.call_service, "wireguard.create_network", kwargs)
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not data.get("ok"):
            console.print(f"[red]Ошибка: {data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        console.print(
            f"[green]✓[/green] Сеть [bold]{name}[/bold] создана: "
            f"{data.get('interface')} · {subnet} · порт {port}"
        )

    # ------------------------------------------------------------------ status

    @wg_app.command("status")
    def status(
        network: str = typer.Argument("default", help="Имя сети"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Статус WireGuard сети."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.network_status", {"name": network})
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not data.get("ok"):
            console.print(f"[red]{data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        up = data.get("up")
        status_text = (
            Text("● up",   style="green") if up is True  else
            Text("○ down", style="red")   if up is False else
            Text("? unknown", style="dim")
        )
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("", style="bold dim")
        table.add_column("")
        table.add_row("Сеть",      data.get("name", "?"))
        table.add_row("Интерфейс", data.get("interface", "?"))
        table.add_row("Подсеть",   data.get("subnet", "?"))
        table.add_row("Порт",      str(data.get("port", "?")))
        table.add_row("Статус",    status_text)
        table.add_row("Пиров",     str(data.get("peers_total", 0)))
        table.add_row("Активно",   str(data.get("peers_active", 0)))
        console.print(Panel(table, title=f"WireGuard: {network}", expand=False))

    # ------------------------------------------------------------------ peers

    @wg_app.command("peers")
    def peers(
        network: str = typer.Argument("default", help="Имя сети"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Список пиров в сети."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.list_peers", {"network": network})
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        items = data.get("peers", []) if isinstance(data, dict) else []

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not items:
            console.print(f"[dim]Нет пиров в сети '{network}'.[/dim]")
            return

        table = Table(title=f"Пиры сети '{network}' ({len(items)})")
        table.add_column("Client ID", style="bold cyan")
        table.add_column("IP")
        table.add_column("Public Key", style="dim")
        for p in items:
            pub = str(p.get("public_key", ""))
            table.add_row(
                str(p.get("client_id", "?")),
                str(p.get("ip", "?")),
                pub[:24] + "…" if len(pub) > 24 else pub,
            )
        console.print(table)

    # ------------------------------------------------------------------ health

    @wg_app.command("health")
    def health(
        network: str = typer.Argument("default", help="Имя сети"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Состояние mesh-туннелей (handshake, трафик, alive/dead)."""
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.mesh_health", {"network": network})
        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not data.get("ok"):
            console.print(f"[red]{data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        healthy = data.get("healthy", False)
        header = (
            f"[green]✓ Все туннели живы[/green]" if healthy else
            f"[red]✗ {data.get('dead', 0)} туннелей мертво[/red]"
        )
        console.print(f"\n{header}  [dim](сеть: {network})[/dim]\n")

        tunnels = data.get("tunnels", [])
        if not tunnels:
            console.print("[dim]Нет данных (нет активных пиров или wg show недоступен).[/dim]")
            return

        table = Table()
        table.add_column("Client ID", style="bold")
        table.add_column("IP")
        table.add_column("Статус")
        table.add_column("Last handshake", style="dim")
        table.add_column("RX",  justify="right", style="dim")
        table.add_column("TX",  justify="right", style="dim")

        for t in tunnels:
            alive = t.get("alive", False)
            ago   = t.get("last_handshake_ago")
            ago_s = f"{ago}s" if ago is not None else "—"
            table.add_row(
                str(t.get("client_id", "?")),
                str(t.get("endpoint") or "—"),
                Text("● alive", style="green") if alive else Text("✕ dead", style="red"),
                ago_s,
                _fmt(t.get("rx_bytes", 0)),
                _fmt(t.get("tx_bytes", 0)),
            )
        console.print(table)

    # ------------------------------------------------------------------ config

    @wg_app.command("config")
    def config(
        client_id: str = typer.Argument(..., help="ID клиента/агента"),
        network: str = typer.Option("default", "--network", "-n", help="Имя сети"),
        output: Path | None = typer.Option(None, "--output", "-o", help="Сохранить .conf в файл"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Получить WireGuard конфиг для клиента.

        Примеры:
          hc wg config host01
          hc wg config host01 --network agents -o ~/agents-host01.conf
        """
        console = Console()
        client = require_client(console)
        kwargs = {"client_id": client_id, "network": network}
        data = anyio.run(client.call_service, "wireguard.get_config", kwargs)

        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if not data.get("ok"):
            console.print(f"[red]{data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        config_text = str(data.get("config_text", ""))

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(config_text, encoding="utf-8")
            console.print(f"[green]✓[/green] Сохранено: [bold]{output}[/bold]")
            console.print(f"[dim]wg-quick up {output}[/dim]")
            return

        console.print(Panel(
            config_text.strip(),
            title=f"[bold]{client_id}[/bold] @ {network}",
            subtitle="[dim]-o ~/wg.conf[/dim]",
            border_style="cyan",
        ))
        console.print(
            f"[dim]IP: {data.get('address')}  "
            f"Endpoint: {data.get('endpoint')}  "
            f"DNS: {data.get('dns')}[/dim]"
        )

    # ------------------------------------------------------------------ tunnel-status

    @wg_app.command("tunnel-status")
    def tunnel_status(
        agent_id: str = typer.Argument(..., help="ID агента"),
        network: str  = typer.Option("default", "--network", "-n"),
        iface: str | None = typer.Option(None, "--iface", help="Имя wg-интерфейса на агенте"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Запросить `wg show` на удалённом агенте и показать состояние его туннелей.

        Примеры:
          hc wg tunnel-status vds
          hc wg tunnel-status apartment1 --network agents
        """
        console = Console()
        client = require_client(console)
        kwargs: dict = {"agent_id": agent_id, "network": network}
        if iface:
            kwargs["iface"] = iface
        data = anyio.run(client.call_service, "wireguard.tunnel_status", kwargs)

        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not data.get("ok"):
            console.print(f"[red]{data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        tunnels = data.get("tunnels", [])
        if not tunnels:
            console.print(f"[dim]Агент {agent_id!r}: нет пиров в wg show.[/dim]")
            return

        table = Table(title=f"Туннели агента {agent_id!r} (сеть: {network})")
        table.add_column("Peer",    style="bold")
        table.add_column("Статус")
        table.add_column("Endpoint",           style="dim")
        table.add_column("Handshake",          style="dim")
        table.add_column("RX",   justify="right", style="dim")
        table.add_column("TX",   justify="right", style="dim")

        for t in tunnels:
            alive = t.get("alive", False)
            ago   = t.get("last_handshake_ago")
            table.add_row(
                str(t.get("peer_id", "?")),
                Text("● alive", style="green") if alive else Text("✕ dead", style="red"),
                str(t.get("endpoint") or "—"),
                f"{ago}s ago" if ago is not None else "—",
                _fmt(t.get("rx_bytes", 0)),
                _fmt(t.get("tx_bytes", 0)),
            )
        console.print(table)

    # ------------------------------------------------------------------ mesh-snapshot

    @wg_app.command("mesh-snapshot")
    def mesh_snapshot(
        network: str   = typer.Argument("default", help="Имя сети"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        """Полный снапшот mesh-сети: опросить все подключённые агенты.

        Показывает матрицу туннелей: кто кого видит и с каким handshake.

        Примеры:
          hc wg mesh-snapshot
          hc wg mesh-snapshot agents
        """
        console = Console()
        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.mesh_snapshot", {"network": network})

        if data is None:
            console.print("[red]Ошибка: wireguard плагин недоступен.[/red]")
            raise typer.Exit(code=1)

        if json_out:
            from hc.json_output import print_json
            print_json(data)
            return

        if not data.get("ok"):
            console.print(f"[red]{data.get('error', 'unknown')}[/red]")
            raise typer.Exit(code=1)

        summary = data.get("summary", {})
        healthy = summary.get("healthy", False)
        dead    = summary.get("dead_tunnels", 0)
        total   = summary.get("total_tunnels", 0)

        status_line = (
            f"[green]✓ Все туннели живы[/green]" if healthy
            else f"[red]✗ {dead}/{total} туннелей мертво[/red]"
        )
        console.print(f"\n{status_line}  [dim](сеть: {network})[/dim]")
        console.print(
            f"[dim]Узлов: {summary.get('total_nodes', 0)}, "
            f"подключено: {summary.get('connected_nodes', 0)}, "
            f"туннелей: {total}[/dim]\n"
        )

        tunnels = data.get("tunnels", [])
        if not tunnels:
            console.print("[dim]Нет данных (нет активных агентов в сети).[/dim]")
            return

        table = Table(title=f"Матрица туннелей · {network}")
        table.add_column("From → To",  style="bold")
        table.add_column("Статус")
        table.add_column("Handshake",   style="dim")
        table.add_column("RX",  justify="right", style="dim")
        table.add_column("TX",  justify="right", style="dim")
        table.add_column("Endpoint",    style="dim")

        for t in sorted(tunnels, key=lambda x: (x["from"], x["to"])):
            alive = t.get("alive", False)
            ago   = t.get("last_handshake_ago")
            table.add_row(
                f"{t['from']} → {t['to']}",
                Text("● alive", style="green") if alive else Text("✕ dead", style="red"),
                f"{ago}s" if ago is not None else "—",
                _fmt(t.get("rx_bytes", 0)),
                _fmt(t.get("tx_bytes", 0)),
                str(t.get("endpoint") or "—"),
            )
        console.print(table)

    # ------------------------------------------------------------------ remove-peer

    @wg_app.command("remove-peer")
    def remove_peer(
        client_id: str = typer.Argument(..., help="ID клиента"),
        network: str = typer.Option("default", "--network", "-n"),
        yes: bool = typer.Option(False, "--yes", "-y"),
    ) -> None:
        """Удалить пир из сети."""
        console = Console()
        if not yes:
            confirmed = typer.confirm(f"Удалить пир '{client_id}' из сети '{network}'?", default=False)
            if not confirmed:
                console.print("[dim]Отменено.[/dim]")
                raise typer.Exit(code=0)

        client = require_client(console)
        data = anyio.run(client.call_service, "wireguard.remove_peer", {"client_id": client_id, "network": network})
        if not data or not data.get("ok"):
            console.print(f"[red]{data.get('error') if data else 'Ошибка'}[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] Пир [bold]{client_id}[/bold] удалён из сети '{network}'.")

    app.add_typer(wg_app, name="wg")


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"
