from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hc.config import Config
from hc.core_ops import compose_project_from_source, require_docker
from hc.commands._compose_helpers import (
    base_compose_file,
    override_compose_path,
)
from hc.commands.recovery import RecoveryContext


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _write_text(p: Path, text: str) -> None:
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _remove_managed_block(text: str, name: str) -> str:
    start = f"# BEGIN hc recovery: {name}"
    end = f"# END hc recovery: {name}"
    out_lines: list[str] = []
    skipping = False
    for ln in text.splitlines():
        if start in ln:
            skipping = True
            continue
        if skipping and end in ln:
            skipping = False
            continue
        if not skipping:
            out_lines.append(ln)
    return "\n".join(out_lines).rstrip() + "\n"


def _ensure_sections(text: str) -> str:
    def _normalize_empty_root_maps(s: str) -> str:
        lines = s.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln in {"services:", "volumes:"}:
                key = ln[:-1]
                j = i + 1
                has_child = False
                while j < len(lines):
                    nxt = lines[j]
                    if not nxt.strip() or nxt.lstrip().startswith("#"):
                        j += 1
                        continue
                    if nxt and not nxt.startswith(" "):
                        break
                    has_child = True
                    break
                if not has_child:
                    out.append(f"{key}: {{}}")
                    i += 1
                    continue
            out.append(ln)
            i += 1
        return "\n".join(out)

    t = text.strip()
    if not t:
        return (
            "# Дополнительные сервисы для recovery-режима.\n"
            "# Управляется частично через `hc recovery compose enable|disable ...`\n"
            "\n"
            "services: {}\n"
            "\n"
            "volumes: {}\n"
        )
    if "services:" not in t:
        t = "services: {}\n\n" + t
    if "volumes:" not in t:
        t = t.rstrip() + "\n\nvolumes: {}\n"
    t = _normalize_empty_root_maps(t)
    return t.rstrip() + "\n"


def _inject_into_root_section(text: str, section: str, block: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for ln in lines:
        if not inserted and ln == f"{section}: {{}}":
            out.append(f"{section}:")
            out.append(block.rstrip("\n"))
            inserted = True
            continue
        out.append(ln)
        if not inserted and ln == f"{section}:":
            out.append(block.rstrip("\n"))
            inserted = True
    if not inserted:
        out.append("")
        out.append(f"{section}:")
        out.append(block.rstrip("\n"))
    return "\n".join(out).rstrip() + "\n"


def _blocks(service: str) -> tuple[str, str]:
    if service == "redis":
        return (
            "\n".join(
                [
                    "  # BEGIN hc recovery: redis",
                    "  redis:",
                    "    image: redis:7-alpine",
                    "    container_name: redis",
                    "    restart: unless-stopped",
                    "    command: [\"redis-server\", \"--appendonly\", \"yes\"]",
                    "    ports:",
                    "      - \"6379:6379\"",
                    "    volumes:",
                    "      - redis-data:/data",
                    "  # END hc recovery: redis",
                    "",
                ]
            ),
            "\n".join(
                [
                    "  # BEGIN hc recovery: redis",
                    "  redis-data:",
                    "  # END hc recovery: redis",
                    "",
                ]
            ),
        )
    if service == "postgres":
        return (
            "\n".join(
                [
                    "  # BEGIN hc recovery: postgres",
                    "  postgres:",
                    "    image: postgres:16-alpine",
                    "    container_name: postgres",
                    "    restart: unless-stopped",
                    "    environment:",
                    "      - POSTGRES_USER=postgres",
                    "      - POSTGRES_PASSWORD=postgres",
                    "      - POSTGRES_DB=core",
                    "    ports:",
                    "      - \"5432:5432\"",
                    "    volumes:",
                    "      - postgres-data:/var/lib/postgresql/data",
                    "  # END hc recovery: postgres",
                    "",
                ]
            ),
            "\n".join(
                [
                    "  # BEGIN hc recovery: postgres",
                    "  postgres-data:",
                    "  # END hc recovery: postgres",
                    "",
                ]
            ),
        )
    if service == "pgadmin":
        return (
            "\n".join(
                [
                    "  # BEGIN hc recovery: pgadmin",
                    "  pgadmin:",
                    "    image: dpage/pgadmin4:latest",
                    "    container_name: pgadmin",
                    "    restart: unless-stopped",
                    "    environment:",
                    "      - PGADMIN_DEFAULT_EMAIL=admin@local",
                    "      - PGADMIN_DEFAULT_PASSWORD=admin",
                    "    ports:",
                    "      - \"5050:80\"",
                    "    depends_on:",
                    "      - postgres",
                    "    volumes:",
                    "      - pgadmin-data:/var/lib/pgadmin",
                    "  # END hc recovery: pgadmin",
                    "",
                ]
            ),
            "\n".join(
                [
                    "  # BEGIN hc recovery: pgadmin",
                    "  pgadmin-data:",
                    "  # END hc recovery: pgadmin",
                    "",
                ]
            ),
        )
    if service == "redisinsight":
        return (
            "\n".join(
                [
                    "  # BEGIN hc recovery: redisinsight",
                    "  redisinsight:",
                    "    image: redis/redisinsight:latest",
                    "    container_name: redisinsight",
                    "    restart: unless-stopped",
                    "    ports:",
                    "      - \"5540:5540\"",
                    "    depends_on:",
                    "      - redis",
                    "    volumes:",
                    "      - redisinsight-data:/data",
                    "  # END hc recovery: redisinsight",
                    "",
                ]
            ),
            "\n".join(
                [
                    "  # BEGIN hc recovery: redisinsight",
                    "  redisinsight-data:",
                    "  # END hc recovery: redisinsight",
                    "",
                ]
            ),
        )
    if service == "ui":
        return (
            "\n".join(
                [
                    "  # BEGIN hc recovery: ui",
                    "  caddy:",
                    "    profiles: [\"hc-disabled\"]",
                    "  # END hc recovery: ui",
                    "",
                ]
            ),
            "",
        )
    raise ValueError(service)


def build_app(ctx: RecoveryContext) -> typer.Typer:
    app = typer.Typer(
        help="Аналитика и управление docker compose (через override)",
        context_settings={"help_option_names": ["-h", "--help"]},
        no_args_is_help=True,
    )

    @app.command("info")
    def info() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        project = compose_project_from_source(console, src)
        base = base_compose_file(project.cwd)
        override = project.cwd / "docker-compose.recovery.yml"
        body = f"base: {base}\n"
        body += f"override: {override} ({'exists' if override.exists() else 'missing'})\n"
        body += f"mode: {Config.load().recovery.mode}\n"
        console.print(Panel.fit(body, title="compose files"))

        services = ctx.compose_capture(console, src, ["config", "--services"]).splitlines()
        tbl = Table(title="compose services")
        tbl.add_column("service", style="bold")
        for s in services:
            if s.strip():
                tbl.add_row(s.strip())
        console.print(tbl)

        override_text = _read_text(override)
        feats = [
            ("redis", "# BEGIN hc recovery: redis" in override_text),
            ("postgres", "# BEGIN hc recovery: postgres" in override_text),
            ("pgadmin", "# BEGIN hc recovery: pgadmin" in override_text),
            ("redisinsight", "# BEGIN hc recovery: redisinsight" in override_text),
            ("ui disabled (caddy)", "# BEGIN hc recovery: ui" in override_text),
        ]
        ft = Table(title="recovery features (override)")
        ft.add_column("feature", style="bold")
        ft.add_column("enabled")
        for name, on in feats:
            ft.add_row(name, "[green]yes[/green]" if on else "[dim]no[/dim]")
        console.print(ft)

    @app.command("lint")
    def lint() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        _ = ctx.compose_capture(console, src, ["config"])
        console.print("[green]✓[/green] compose валиден")

    @app.command("ps")
    def ps() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        ctx.run_compose(console, src, ["ps"])

    @app.command("config")
    @app.command("services")
    def services() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        out = ctx.compose_capture(console, src, ["config", "--services"])
        console.print(out)

    def config() -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        out = ctx.compose_capture(console, src, ["config"])
        console.print(out)

    @app.command("enable")
    def enable(service: str = typer.Argument(..., help="redis|postgres|pgadmin|redisinsight|ui")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        p = override_compose_path(console, src)
        name = service.strip().lower()
        supported = {"redis", "postgres", "pgadmin", "redisinsight", "ui"}
        if name not in supported:
            console.print(f"[red]Неизвестно: {name}[/red]")
            raise typer.Exit(code=2)

        text = _ensure_sections(_read_text(p))
        text = _remove_managed_block(text, name)

        # deps
        if name == "pgadmin" and "# BEGIN hc recovery: postgres" not in text:
            s, v = _blocks("postgres")
            text = _inject_into_root_section(text, "services", s)
            text = _inject_into_root_section(text, "volumes", v)
        if name == "redisinsight" and "# BEGIN hc recovery: redis" not in text:
            s, v = _blocks("redis")
            text = _inject_into_root_section(text, "services", s)
            text = _inject_into_root_section(text, "volumes", v)

        if name == "ui":
            # enable ui = убрать disable блок
            text = _remove_managed_block(text, "ui")
        else:
            s, v = _blocks(name)
            text = _inject_into_root_section(text, "services", s)
            if v.strip():
                text = _inject_into_root_section(text, "volumes", v)
        _write_text(p, text)
        console.print(f"[green]✓[/green] Включил {name} в {p}")

    @app.command("disable")
    def disable(service: str = typer.Argument(..., help="redis|postgres|pgadmin|redisinsight|ui")) -> None:
        console = Console()
        require_docker(console)
        src = ctx.resolve_source(console)
        p = override_compose_path(console, src)
        name = service.strip().lower()
        supported = {"redis", "postgres", "pgadmin", "redisinsight", "ui"}
        if name not in supported:
            console.print(f"[red]Неизвестно: {name}[/red]")
            raise typer.Exit(code=2)

        text = _read_text(p)
        if not text.strip():
            console.print("[yellow]override-compose отсутствует или пустой — выключать нечего.[/yellow]")
            raise typer.Exit(code=0)

        if name == "ui":
            t = _ensure_sections(text)
            t = _remove_managed_block(t, "ui")
            s, _ = _blocks("ui")
            t = _inject_into_root_section(t, "services", s)
            _write_text(p, t)
            console.print(f"[green]✓[/green] UI отключён (caddy через profiles) в {p}")
            return

        text2 = _remove_managed_block(text, name)
        _write_text(p, _ensure_sections(text2))
        console.print(f"[green]✓[/green] Выключил {name} в {p}")

    return app

