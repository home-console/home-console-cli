from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hc.config import Config
from hc.constants import CONFIG_PATH, CORE_SRC_DIR
from hc.core_source import COMPOSE_MODES


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    def doctor() -> None:
        """Диагностика системы: Docker, конфиг, исходники, порты, диск."""
        console = Console()
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(min_width=28)
        table.add_column()
        table.add_column(style="dim")

        ok = "[green]✓[/green]"
        warn = "[yellow]![/yellow]"
        fail = "[red]✗[/red]"
        issues: list[str] = []

        def row(label: str, icon: str, detail: str = "") -> None:
            table.add_row(label, icon, detail)

        # ── Docker ────────────────────────────────────────────────────
        docker_bin = shutil.which("docker")
        if docker_bin:
            ver = subprocess.run(  # noqa: S603
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, check=False, timeout=5,
            )
            docker_ver = ver.stdout.strip() or "?"
            row("Docker", ok, f"v{docker_ver}  ({docker_bin})")
        else:
            row("Docker", fail, "не найден — установи Docker или OrbStack")
            issues.append("Docker не найден")

        compose_ver = subprocess.run(  # noqa: S603
            ["docker", "compose", "version", "--short"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if compose_ver.returncode == 0:
            row("Docker Compose", ok, compose_ver.stdout.strip())
        else:
            row("Docker Compose", warn, "не определена версия")

        git_bin = shutil.which("git")
        if git_bin:
            git_ver = subprocess.run(  # noqa: S603
                ["git", "--version"], capture_output=True, text=True, check=False
            )
            row("git", ok, git_ver.stdout.strip())
        else:
            row("git", warn, "не найден — нужен для hc core init/update")

        # ── Config ────────────────────────────────────────────────────
        table.add_row("")
        if CONFIG_PATH.exists():
            cfg = Config.load()
            host_ok = bool(cfg.core.host.strip())
            token_ok = bool(cfg.core.token.strip())
            row("Конфиг (~/.config/hc)", ok, str(CONFIG_PATH))
            row("  Core host",  ok if host_ok else warn,
                f"{cfg.core.host}:{cfg.core.port}" if host_ok else "не задан")
            row("  Token",      ok if token_ok else warn,
                "задан" if token_ok else "не задан — запусти hc connect")
        else:
            row("Конфиг (~/.config/hc)", warn, "не найден — запусти hc connect или hc setup")
            issues.append("Конфиг не найден")

        # ── Core sources ──────────────────────────────────────────────
        table.add_row("")
        if CORE_SRC_DIR.exists():
            row("Core исходники", ok, str(CORE_SRC_DIR))
            for mode, rel in COMPOSE_MODES.items():
                cf = CORE_SRC_DIR / rel
                row(f"  compose [{mode}]", ok if cf.exists() else warn,
                    rel if cf.exists() else f"{rel}  ← не найден")
            env_file = CORE_SRC_DIR / ".env"
            row("  .env", ok if env_file.exists() else warn,
                ".env готов" if env_file.exists() else "нет — будет создан при hc env up")
        else:
            row("Core исходники", warn, f"не найдены — запусти hc core init")
            issues.append("Core исходники не найдены")

        # ── Ports ─────────────────────────────────────────────────────
        table.add_row("")
        CHECK_PORTS = {18080: "UI (caddy)", 18000: "Core API", 5432: "PostgreSQL", 6379: "Redis"}
        for port, label in CHECK_PORTS.items():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                in_use = s.connect_ex(("127.0.0.1", port)) == 0
            icon = ok if in_use else "[dim]—[/dim]"
            row(f"  :{port} {label}", icon, "listening" if in_use else "free")

        # ── Disk ─────────────────────────────────────────────────────
        table.add_row("")
        try:
            stat = shutil.disk_usage(Path.home())
            free_gb = stat.free / 1024 ** 3
            total_gb = stat.total / 1024 ** 3
            used_pct = (stat.used / stat.total) * 100
            disk_color = "red" if used_pct > 90 else "yellow" if used_pct > 75 else "green"
            row("Диск (home)", f"[{disk_color}]{'!' if used_pct > 75 else '✓'}[/{disk_color}]",
                f"{free_gb:.1f} GB свободно из {total_gb:.1f} GB ({used_pct:.0f}% занято)")
            if used_pct > 90:
                issues.append(f"Диск почти полон ({used_pct:.0f}%)")
        except Exception:
            pass

        console.print(table)

        if issues:
            console.print()
            for issue in issues:
                console.print(f"[yellow]![/yellow] {issue}")
        else:
            console.print("\n[green]✓ Всё в порядке[/green]")
